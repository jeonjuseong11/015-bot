import argparse
import sys
from decimal import Decimal
import gspread
from gspread.utils import rowcol_to_a1
from oauth2client.service_account import ServiceAccountCredentials
import psycopg2
import pymysql
from datetime import date, timedelta, datetime
import re
import traceback

# [GA4 라이브러리]
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension,
    Filter, FilterExpression, FilterExpressionList,
)

from settings import (
    HDB_CONFIG,
    JSON_KEY_FILE,
    SHEET_ID,
    TAB_NAME,
    YH_DB_CONFIG,
    require_db_settings,
    require_google_settings,
)

# ==============================================================================
# [설정] GA4 속성 ID
# ==============================================================================
# [0]: Arreo(445891939), [1]: 015(444980831)
GA4_WEB_IDS = ['445891939', '444980831'] 
GA4_APP_ID = '461784274'
# 015 웹 MAU(Y열)를 읽는 속성
GA4_MAU_WEB_IDS = ['444980831']
# 기본(어제) 실행 때, 그보다 앞선 며칠의 GA4 열을 다시 읽어 덮어쓴다.
# GA4 하루 치가 확정되는 데 24~48시간이 걸리기 때문. 0이면 재수집 안 함.
GA_REFRESH_DAYS = 2
GA_PAGE_SIZE = 10000   # run_report 한 번에 받는 행 수
# GA4 조회가 일부 실패해 빈칸으로 들어간 날짜 (종료 코드 판단용)
GA_INCOMPLETE_DATES = []
# 세션 출처에 아래 문자열이 들어간 방문은 웹 사용자 수에서 뺀다 (봇·추천 스팸).
#   예) 2026-09-01 a015.com 방문자 440명 중 408명이 trafficheap.cc 추천 유입(세이셸, 새벽 0~4시)
GA_EXCLUDE_SOURCES = ['trafficheap']
# 출처 제외 필터가 거부되어 제외 없이 집계한 지표 (실행 로그용)
GA_FILTER_FALLBACKS = set()
# 웹 로그인 이벤트 이름 (호스트별 '웹 로그인(이벤트)' 열)
GA_WEB_LOGIN_EVENT = 'login'

# [키워드 설정]
GA_CONFIG = {
    # 1. [웹] URL 키워드
    'web_signup_keyword': ['join', 'regist', 'signup', 'SignUp'],   
    'web_charge_keyword': ['charge', 'payment'],          
    'web_voice_keyword': ['voice', 'vms'],                
    'web_guide_keyword': ['guide', 'help', 'notice'],
    
    # 웹 로그인 페이지 (이벤트가 없어서 페이지 방문자로 대체)
    'web_login_page_keyword': ['signin', 'login'], 
    
    # 2. [앱] 화면 이름 키워드
    'app_signup_keyword': ['SignUp', 'Join', 'Register'], 
    
    # 3. [이벤트] 앱 로그인
    'app_login_event_name': 'login',
    
    # 4. [호스트] 도메인 분류
    'host_a015': ['a015', 'new.arreo'],
    'host_arreo': ['arreo'],
    'host_exclude': ['localhost', '127.0.0.1', '192.168', 'confirm.seoultel', 'stage.', 'test-']  # 개발/테스트 환경 제외
}

# [설정] 시트 이름 정의 (실제 시트 이름과 똑같이 맞춰주세요!)
TAB_NAME_INPUT = '데이터입력시트'         # 1. 데이터 입력하는 곳
TAB_NAME_GRAPH = '그래프_데이터'          # 2. 수식 복사하는 곳
TAB_NAME_REPORT = '평일보고양식'          # 3. 날짜 헤더 바꾸는 곳
TAB_NAME_MOBILE_GRAPH = '모바일_그래프_데이터'  # 4. B~K열, 수식 복사
TAB_NAME_MOBILE_REPORT = '모바일보고양식'      # 5. 날짜 헤더 바꾸는 곳

# 그래프 시트별 마지막 열 (1=A, 2=B ...). B열=날짜, C열부터 복사.
GRAPH_END_COLUMN = 46         # 그래프_데이터: AT열
MOBILE_GRAPH_END_COLUMN = 11  # 모바일_그래프_데이터: K열

# ------------------------------------------------------------------------------
# [설정] 모바일보고양식 레이아웃
# ------------------------------------------------------------------------------
# 같은 표가 좌/우 두 벌 있고, 각 블록은 "당일까지의 최근 N일"을 보여준다.
#   start_col : 블록 첫 데이터 열 (4=D, 13=M)
#   days      : 블록이 보여주는 일수
# 보고일 셀(C4/L4)은 시트에 =TODAY()가 들어있어 코드가 건드리지 않는다.
MOBILE_BLOCKS = [
    {"start_col": 4,  "days": 2},   # D~E
    {"start_col": 13, "days": 3},   # M~O
]

MOBILE_HEADER_ROW = 7            # 날짜 헤더 ('25일')
MOBILE_DATA_FIRST_ROW = 8        # 아래 목록과 1:1 대응하는 연속 행
# {tab}=데이터입력시트, {row}=해당 날짜의 행. 시트에 원래 들어있던 수식 형태 그대로.
MOBILE_DATA_FORMULAS = [
    '=IFERROR(\'{tab}\'!Q{row}, "")',   # 8행  회원가입    = GA4 app_signup
    '=IFERROR(\'{tab}\'!T{row}, "")',   # 9행  앱 로그인   = GA4 app_login
    '=IFERROR(\'{tab}\'!D{row}, "")',   # 10행 신규 가입자 = YH new_user
    # 11행 누적 가입자: E열이 '1,915,507(3,797)' 꼴이라 괄호 안만 뽑는다.
    '=REGEXEXTRACT(IFERROR(\'{tab}\'!E{row}, ""), "\\((.+)\\)")',
]
MOBILE_USAGE_HEADER_ROW = 15     # 'MAU(07/26)' / 'DAU(07/26)'
MOBILE_USAGE_VALUE_ROW = 16      # 당일 1일치만, 2칸
MOBILE_USAGE_FORMULAS = [
    "='{tab}'!AL{row}",   # MAU (mau_app)
    "='{tab}'!AK{row}",   # DAU (dau_app)
]

# ------------------------------------------------------------------------------
# [설정] 평일보고양식 날짜 헤더 위치
# ------------------------------------------------------------------------------
# 왼쪽 블록 = 최근 2일, 오른쪽 블록 = 최근 3일. 둘 다 당일로 끝난다.
# 데이터 셀은 시트의 INDEX/XMATCH가 이 헤더를 보고 따라오므로 헤더만 쓰면 된다.
#   (범위, 일수, 반복묶음)
#   반복묶음 3 = 015 문자발송 / 010 문자발송 / ARREO 010 문자발송
REPORT_DATE_RANGES = [
    ("D7:I7",   2, 3),   # 왼쪽 문자유형 헤더
    ("D26:E26", 2, 1),   # 왼쪽 충전금액
    ("E30:F30", 2, 1),   # 왼쪽 기관 고객사 SMS 전송량
    ("T7:AB7",  3, 3),   # 오른쪽 문자유형 헤더
    ("T26:V26", 3, 1),   # 오른쪽 충전금액
    ("U30:W30", 3, 1),   # 오른쪽 015 일반 전송(라우팅)
]


# ==============================================================================
# [유틸] 콤마 찍기 함수
# ==============================================================================
def fmt(val):
    try:
        if val == "" or val is None: return ""
        if isinstance(val, (int, float, Decimal)): return f"{int(val):,}"
        if isinstance(val, str) and val.isdigit(): return f"{int(val):,}"
        return val
    except: return val

# ==============================================================================
# [함수] 데이터 수집 - DB
# ==============================================================================
def get_yh_data(target_date):
    print(f"🐘 YH-DB 조회... ({target_date})")
    data = {}
    try:
        conn = psycopg2.connect(**YH_DB_CONFIG)
        cur = conn.cursor()
        
        # 신규/충전
        cur.execute("SELECT COUNT(*) FROM arreo.say015_subscription WHERE subs_start_date LIKE %s || '%%'", (target_date,))
        data['new_user'] = cur.fetchone()[0]
        
        cur.execute("select sum(h.pg) from arreo.mbr_usr_h h where h.wrt_dttm LIKE %s || '%%' and h.acct_gb='1' and h.sms_gb in ('W', 'M') and h.site_id='arreo'", (target_date,))
        res = cur.fetchone()[0]
        data['charge'] = int(res) if res else 0

        # Mode
        y_date = date(int(target_date[:4]), int(target_date[4:6]), int(target_date[6:]))
        today_str = (y_date + timedelta(days=1)).strftime('%Y%m%d')
        
        sql_mode = f"SELECT SUM(CASE WHEN t.cmp_usr_id = '00000' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'LMS01' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'MMS01' THEN t.cnt ELSE 0 END) FROM (SELECT x.cmp_usr_id, COUNT(*) AS cnt FROM arreo.smt_send x WHERE x.snd_dttm >= '{target_date}' AND x.snd_dttm < '{today_str}' AND x.sms_st != 'C' AND x.site_id = 'arreo' GROUP BY x.cmp_usr_id) t"
        cur.execute(sql_mode); row = cur.fetchone() or (0,0,0)
        data['mode_sms'], data['mode_lms'], data['mode_mms'] = row

        # ARREO / A015
        sql_arreo = f"SELECT SUM(CASE WHEN t.cmp_usr_id = '00000' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'LMS01' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'MMS01' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = '00000' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'LMS01' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END), SUM(CASE WHEN t.cmp_usr_id = 'MMS01' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END) FROM (SELECT x.cmp_usr_id, x.agt_node_id , COUNT(*) AS cnt FROM arreo.xms_send x WHERE x.snd_dttm >= '{target_date}' AND x.snd_dttm < '{today_str}' AND x.sms_st != 'C' AND x.callback NOT LIKE '015%%' AND x.rslt_val = '-100' AND x.site_id = 'arreo' GROUP BY x.cmp_usr_id, x.agt_node_id) t"
        cur.execute(sql_arreo); row = cur.fetchone() or (0,0,0,0,0,0)
        data['arreo_sms'], data['arreo_lms'], data['arreo_mms'] = row[0:3]
        data['a015_sms'], data['a015_lms'], data['a015_mms'] = row[3:6]

        # 1:1 / 1:N
        data['one_to_one_sms'] = 0; data['one_to_n_sms'] = 0
        data['one_to_one_lms'] = 0; data['one_to_n_lms'] = 0
        data['one_to_one_mms'] = 0; data['one_to_n_mms'] = 0

        sql_1to1 = """
            select m.cmp_usr_id,
                sum(case when recipients_count = 1 then 1 else 0 end),
                sum(case when recipients_count > 1 then recipients_count else 0 end)
            from arreo.smt_send_m m
            where m.snd_dttm >= %s and m.snd_dttm < %s
            group by m.cmp_usr_id
        """
        cur.execute(sql_1to1, (target_date, today_str))
        for c_id, o1, on_ in cur.fetchall():
            o1 = int(o1 or 0); on_ = int(on_ or 0)
            if c_id == "00000": data["one_to_one_sms"] = o1; data["one_to_n_sms"] = on_
            elif c_id == "LMS01": data["one_to_one_lms"] = o1; data["one_to_n_lms"] = on_
            elif c_id == "MMS01": data["one_to_one_mms"] = o1; data["one_to_n_mms"] = on_
        
        conn.close(); return data
    except Exception as e: print(f"❌ YH DB Error: {e}"); return None

def get_hdb_data(target_date):
    print(f"🐬 HDB 조회... ({target_date})")
    data = {}
    try:
        conn = pymysql.connect(**HDB_CONFIG)
        cur = conn.cursor()
        
        cur.execute(f"select count(*) from mosms.mo_sms_h where SND_DT = '{target_date}' and sender not in ('01585040006','01228423734') and sender not like ('015%%') and receiver LIKE '015%%' AND (LENGTH(receiver) = 10 OR LENGTH(receiver) = 11)"); s1 = cur.fetchone()[0]
        cur.execute(f"select count(*) from momms.mo_mms_h where SND_DT = '{target_date}' and sender not in ('01585040006','01228423734') and sender not like ('015%%') and receiver LIKE '015%%' AND (LENGTH(receiver) = 10 OR LENGTH(receiver) = 11)"); s2 = cur.fetchone()[0]
        data['gen_in_total'] = s1 + s2

        cur.execute(f"select count(*) from (select sender from mosms.mo_sms_h where SND_DT='{target_date}' and receiver='01585040006' union all select sender from momms.mo_mms_h where SND_DT='{target_date}' and receiver='01585040006') a"); data['cs_in'] = cur.fetchone()[0]
        cur.execute(f"select count(*) from (select sender, receiver from mosms.mo_sms_h where SND_DT='{target_date}' union all select sender, receiver from momms.mo_mms_h where SND_DT='{target_date}') a where sender not in ('01585040006','01228423734') and sender not like ('015%%') and receiver LIKE '015%%'"); data['total_in'] = cur.fetchone()[0]

        cur.execute(f"select 'SMS', COUNT(*) from SMSMT.SMS_H force index (IDX_SMS_H6) where SND_DT ='{target_date}' and GROUP_CODE like 'B00%%' and RESULT_CODE = '-100' and SETTLE_CODE like 'SMT-%%' union all select 'MMS', COUNT(*) from MMSMT.MMS_H force index (IDX_MMS_H6) where SND_DT ='{target_date}' and GROUP_CODE like 'B00%%' and RESULT_CODE = '-100' and SETTLE_CODE like 'SMT-%%'")
        rows = cur.fetchall(); data['route_sms'] = 0; data['route_mms'] = 0
        for r in rows:
            if r[0] == 'SMS': data['route_sms'] = r[1]
            if r[0] == 'MMS': data['route_mms'] = r[1]

        cur.execute(f"select 'SMS',count(*) from smsmt.sms_h a , smsmt.mbr_cmp_m b where a.GROUP_CODE = b.grp_cd and a.BILL_CODE = b.bill_cd and b.ad_flag ='N' and SND_DT ='{target_date}' and RESULT_CODE ='-100' union all select 'LMS',count(*) from mmsmt.mms_h a , smsmt.mbr_cmp_m b where a.GROUP_CODE = b.grp_cd and a.BILL_CODE = b.bill_cd and b.ad_flag ='N' and SND_DT ='{target_date}' and RESULT_CODE ='-100'")
        rows = cur.fetchall(); data['gen_sms'] = 0; data['gen_lms'] = 0
        for r in rows:
            if r[0] == 'SMS': data['gen_sms'] = r[1]
            if r[0] == 'LMS': data['gen_lms'] = r[1]
        
        conn.close(); return data
    except Exception as e: print(f"❌ HDB Error: {e}"); return None

# ==============================================================================
# [함수] 데이터 수집 - GA4
# ==============================================================================
# 집계 원칙
#   1) "사용자 수"는 더할 수 없는 값이다. 페이지별 행을 받아 합산하면 여러 페이지를 본
#      한 사람이 여러 명으로 잡힌다. 그래서 키워드 조건은 GA4 필터로 넘기고,
#      호스트별 고유 사용자 수를 GA4가 계산하게 한다.
#   2) 같은 호스트가 두 속성에 모두 잡히면(스트림 중복 전송) 더하지 않고 큰 값만 쓴다.
#   3) 조회에 실패한 항목은 0이 아니라 None(시트에는 빈칸)으로 둔다.
#      0은 "방문자 없음"으로 읽히기 때문. 빈칸은 다음 실행의 GA4 재수집이 채운다.
GA_WEB_PAGE_METRICS = [
    # (결과 키, GA_CONFIG 키워드 키)
    ('web_signup',  'web_signup_keyword'),
    ('cash_charge', 'web_charge_keyword'),
    ('voicemail',   'web_voice_keyword'),
    ('guide',       'web_guide_keyword'),
    ('web_login',   'web_login_page_keyword'),
]
GA_WEB_KEYS = [k for k, _ in GA_WEB_PAGE_METRICS] + ['a015_user', 'arreo_user', 'mau_015_total']
GA_APP_KEYS = ['app_signup', 'app_login', 'dau_app', 'mau_app']

# ------------------------------------------------------------------------------
# 호스트별 분리 집계 (데이터입력시트 AU열부터, 기존 A~AT열은 그대로 둔다)
# ------------------------------------------------------------------------------
# 기존 열(P~Y)은 a015 계열 호스트를 합친 값이다. 아래는 같은 지표를 호스트별로 따로 적는다.
#   (라벨, 호스트에 들어 있어야 하는 문자열)
GA_HOST_SPLIT = [
    ('a015.com', 'a015.com'),
    ('new.arreo.com', 'new.arreo.com'),
]
#   (결과 키, 시트 머리글) — 한 호스트가 차지하는 열 순서
GA_HOST_METRICS = [
    ('web_signup', '회원가입페이지'), ('cash_charge', '캐쉬충전페이지'), ('web_login', '웹 로그인'),
    ('voicemail', '음성사서함'), ('guide', '이용가이드'), ('visitors', '방문자'), ('mau', '월 활성사용자'),
]
INPUT_BASE_COLUMNS = 46      # A~AT: 기존 레이아웃
GA_HOST_FIRST_COLUMN = 47    # AU


def host_key(label, metric):
    return f"{metric}@{label}"


# 호스트별 세부 지표와 로그인 '횟수'(login 이벤트). 계산해서 결과에는 담지만 시트에는 쓰지 않는다.
# (test/ga4_preview.py 로 확인 가능. 시트에 넣으려면 GA_HOST_KEYS 뒤에 덧붙인다.)
GA_LOGIN_EVENT_KEYS = [host_key(label, 'login_event') for label, _ in GA_HOST_SPLIT]
GA_DETAIL_KEYS = [host_key(label, metric) for label, _ in GA_HOST_SPLIT for metric, _ in GA_HOST_METRICS] \
    + GA_LOGIN_EVENT_KEYS

# 시트 AU열부터 쓰는 값: 서비스 4곳 × (MAU, DAU)
#   AU/AV a015.com · AW/AX new.arreo.com · AY/AZ 015앱 · BA/BB arreo.com
GA_USAGE_GROUPS = ['a015.com', 'new.arreo.com', '015앱', 'arreo.com']
GA_USAGE_METRICS = [('mau', 'MAU'), ('dau', 'DAU')]
GA_HOST_KEYS = [host_key(label, metric) for label in GA_USAGE_GROUPS for metric, _ in GA_USAGE_METRICS]
INPUT_END_COLUMN = INPUT_BASE_COLUMNS + len(GA_HOST_KEYS)   # 54 = BB
GA_APP_PLATFORMS = ['Android', 'iOS']


def _contains_any(field_name, keywords):
    """field 값에 keywords 중 하나라도 들어 있으면 통과 (대소문자 구분, 기존 `k in value`와 동일)."""
    return FilterExpression(or_group=FilterExpressionList(expressions=[
        FilterExpression(filter=Filter(
            field_name=field_name,
            string_filter=Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.CONTAINS,
                value=keyword,
                case_sensitive=True,
            ),
        ))
        for keyword in keywords
    ]))


def _build_filter(contains=None, exclude_sources=None):
    """contains 조건과 '출처 제외' 조건을 AND로 묶는다. 둘 다 없으면 None."""
    parts = []
    if contains:
        parts.append(_contains_any(*contains))
    if exclude_sources:
        parts.append(FilterExpression(not_expression=_contains_any("sessionSource", exclude_sources)))
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return FilterExpression(and_group=FilterExpressionList(expressions=parts))


def _ga_rows(client, prop, date_fmt, dimensions, metric, contains=None, exclude_sources=None):
    """하루치 리포트를 끝 페이지까지 읽어 [([차원값...], 정수값), ...] 로 돌려준다.

    contains=(차원이름, [키워드...]) 를 주면 그 차원에 키워드가 포함된 행만 집계한다.
    exclude_sources=[...] 를 주면 세션 출처에 그 문자열이 들어간 방문을 뺀다.
    """
    dimension_filter = _build_filter(contains, exclude_sources)
    rows, offset = [], 0
    while True:
        kwargs = dict(
            property=prop,
            date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=metric)],
            limit=GA_PAGE_SIZE,
            offset=offset,
        )
        if dimension_filter is not None:
            kwargs["dimension_filter"] = dimension_filter
        resp = client.run_report(RunReportRequest(**kwargs))
        for row in resp.rows:
            rows.append(([v.value for v in row.dimension_values], int(row.metric_values[0].value)))
        offset += len(resp.rows)
        if not resp.rows or offset >= resp.row_count:
            return rows


def _web_users_by_host(client, prop, date_fmt, metric="activeUsers", path_keywords=None):
    """{호스트: 고유 사용자 수}. 개발/테스트 호스트는 뺀다."""
    contains = ("pagePath", path_keywords) if path_keywords else None
    args = (client, prop, date_fmt, ["hostName"], metric, contains)
    if GA_EXCLUDE_SOURCES:
        try:
            rows = _ga_rows(*args, exclude_sources=GA_EXCLUDE_SOURCES)
        except Exception as e:
            # 지표와 출처 조건을 함께 못 쓰는 경우 등. 값을 비우는 것보다 제외 없이라도 집계한다.
            # (접속 장애라면 아래 재시도도 실패해 그대로 오류로 올라간다.)
            rows = _ga_rows(*args)
            if metric not in GA_FILTER_FALLBACKS:
                GA_FILTER_FALLBACKS.add(metric)
                print(f"   ⚠️ 출처 제외 필터 미적용({metric}) — 제외 없이 집계합니다: {str(e)[:200]}")
    else:
        rows = _ga_rows(*args)

    out = {}
    for (host,), cnt in rows:
        if any(k in host for k in GA_CONFIG['host_exclude']):
            continue
        out[host] = out.get(host, 0) + cnt
    return out


def _web_login_events_by_host(client, prop, date_fmt):
    """{호스트: login 이벤트 횟수}.

    한 사이트의 데이터가 같은 속성의 두 스트림으로 복제 전송되는 경우가 있어
    (a015 → a015 + 015works), 호스트별로 스트림 중 큰 값 하나만 쓴다.
    """
    per_stream = {}
    for (event_name, host, stream), cnt in _ga_rows(
            client, prop, date_fmt, ["eventName", "hostName", "streamName"], "eventCount",
            ("eventName", [GA_WEB_LOGIN_EVENT])):
        if event_name != GA_WEB_LOGIN_EVENT or any(k in host for k in GA_CONFIG['host_exclude']):
            continue
        per_stream[(host, stream)] = per_stream.get((host, stream), 0) + cnt
    out = {}
    for (host, _stream), cnt in per_stream.items():
        out[host] = max(out.get(host, 0), cnt)
    return out


def _merge_max(merged, per_host):
    """속성 간 병합: 같은 호스트는 큰 값 하나만 남긴다 (중복 전송 이중 집계 방지)."""
    for host, cnt in per_host.items():
        merged[host] = max(merged.get(host, 0), cnt)


def _is_a015_host(host):
    return any(k in host for k in GA_CONFIG['host_a015'])


def _app_platform_total(rows):
    return sum(cnt for (platform,), cnt in rows if platform in GA_APP_PLATFORMS)


def get_ga4_detailed_data(target_date):
    """GA4 지표 dict. 조회 실패한 묶음(웹/앱)의 값은 None, 사유는 res['errors']."""
    print(f"📊 GA4 상세 데이터 수집 중... ({target_date})")

    date_fmt = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    client = BetaAnalyticsDataClient.from_service_account_json(JSON_KEY_FILE)

    res = {key: None for key in GA_WEB_KEYS + GA_APP_KEYS + GA_DETAIL_KEYS + GA_HOST_KEYS}
    res['errors'] = []

    # 1. WEB — 한 속성이라도 실패하면 합계를 믿을 수 없으므로 웹 묶음 전체를 비운다.
    try:
        visitors = {}
        login_events = {}
        login_events_ok = True
        page_users = {key: {} for key, _ in GA_WEB_PAGE_METRICS}
        for web_id in GA4_WEB_IDS:
            prop_web = f"properties/{web_id}"
            _merge_max(visitors, _web_users_by_host(client, prop_web, date_fmt))
            try:
                _merge_max(login_events, _web_login_events_by_host(client, prop_web, date_fmt))
            except Exception as e:
                # 덧붙인 열 하나 때문에 기존 웹 지표 전체를 비우지 않는다.
                login_events_ok = False
                print(f"   ⚠️ 웹 로그인 이벤트 조회 실패({web_id}) — 해당 열만 빈칸: {str(e)[:200]}")
            for key, keyword_key in GA_WEB_PAGE_METRICS:
                _merge_max(page_users[key], _web_users_by_host(
                    client, prop_web, date_fmt, path_keywords=GA_CONFIG[keyword_key]))

        # 015 웹 MAU (Y열): 기존과 같이 015 속성에서만 읽는다.
        mau = {}
        for web_id in GA4_MAU_WEB_IDS:
            _merge_max(mau, _web_users_by_host(
                client, f"properties/{web_id}", date_fmt, metric="active28DayUsers"))

        web = {key: sum(page_users[key].values()) for key, _ in GA_WEB_PAGE_METRICS}
        web['a015_user'] = sum(c for h, c in visitors.items() if _is_a015_host(h))
        web['arreo_user'] = sum(
            c for h, c in visitors.items()
            if not _is_a015_host(h) and any(k in h for k in GA_CONFIG['host_arreo']))
        web['mau_015_total'] = sum(c for h, c in mau.items() if _is_a015_host(h))

        # 호스트별 분리 값 (합산 값과 같은 원본에서 나눠 담는다)
        per_host = dict(page_users, visitors=visitors, mau=mau)
        for label, needle in GA_HOST_SPLIT:
            for metric, _ in GA_HOST_METRICS:
                web[host_key(label, metric)] = sum(
                    c for h, c in per_host[metric].items() if needle in h)
            web[host_key(label, 'login_event')] = sum(
                c for h, c in login_events.items() if needle in h) if login_events_ok else None
            web[host_key(label, 'dau')] = web[host_key(label, 'visitors')]   # 'mau@호스트'는 위에서 채워짐

        # arreo.com: DAU는 기존 '아레오 방문자'(X열)와 같은 값, MAU는 같은 호스트 기준 28일 사용자
        mau_all = dict(mau)
        for web_id in GA4_WEB_IDS:
            if web_id not in GA4_MAU_WEB_IDS:
                _merge_max(mau_all, _web_users_by_host(
                    client, f"properties/{web_id}", date_fmt, metric="active28DayUsers"))
        web[host_key('arreo.com', 'dau')] = web['arreo_user']
        web[host_key('arreo.com', 'mau')] = sum(
            c for h, c in mau_all.items()
            if not _is_a015_host(h) and any(k in h for k in GA_CONFIG['host_arreo']))
        res.update(web)
    except Exception as e:
        res['errors'].append(f"web: {e}")
        print(f"⚠️ GA4 웹 조회 실패 — 웹 지표는 빈칸으로 둡니다: {e}")

    # 2. APP
    try:
        prop_app = f"properties/{GA4_APP_ID}"
        app = {
            # 가입 화면을 본 고유 사용자 (화면별 합산이 아니라 필터로 한 번에)
            'app_signup': _app_platform_total(_ga_rows(
                client, prop_app, date_fmt, ["platform"], "activeUsers",
                ("unifiedScreenName", GA_CONFIG['app_signup_keyword']))),
            # 로그인 이벤트 횟수 (횟수는 더해도 되는 값)
            'app_login': sum(
                cnt for (event_name, platform), cnt in _ga_rows(
                    client, prop_app, date_fmt, ["eventName", "platform"], "eventCount")
                if platform in GA_APP_PLATFORMS
                and event_name == GA_CONFIG['app_login_event_name']),
            'dau_app': _app_platform_total(_ga_rows(
                client, prop_app, date_fmt, ["platform"], "activeUsers")),
            'mau_app': _app_platform_total(_ga_rows(
                client, prop_app, date_fmt, ["platform"], "active28DayUsers")),
        }
        app[host_key('015앱', 'mau')] = app['mau_app']   # AL열과 같은 값
        app[host_key('015앱', 'dau')] = app['dau_app']   # AK열과 같은 값
        res.update(app)
    except Exception as e:
        res['errors'].append(f"app: {e}")
        print(f"⚠️ GA4 앱 조회 실패 — 앱 지표는 빈칸으로 둡니다: {e}")

    return res


# 데이터입력시트에서 GA4 값이 들어가는 열. process_one_day의 행 조립 순서와 같아야 한다.
GA_INPUT_COLUMNS = [
    ("P", "web_signup"), ("Q", "app_signup"), ("R", "cash_charge"),
    ("S", "web_login"), ("T", "app_login"), ("U", "voicemail"), ("V", "guide"),
    ("W", "a015_user"), ("X", "arreo_user"), ("Y", "mau_015_total"),
    ("AK", "dau_app"), ("AL", "mau_app"),
] + [
    (re.sub(r"\d+", "", rowcol_to_a1(1, GA_HOST_FIRST_COLUMN + i)), key)
    for i, key in enumerate(GA_HOST_KEYS)
]


def ensure_host_columns(sheet_input):
    """서비스별 MAU/DAU 열(AU~BB)이 들어갈 자리와 머리글을 준비한다. 이미 있으면 아무것도 안 한다.

    머리글 행은 C열이 '날짜'인 행으로 찾는다. 그 윗행에 서비스 이름, 그 행에 MAU/DAU를 적는다.
    """
    if sheet_input.col_count < INPUT_END_COLUMN:
        sheet_input.add_cols(INPUT_END_COLUMN - sheet_input.col_count)

    head = sheet_input.col_values(3)[:10]
    header_row = next((i for i, v in enumerate(head, start=1) if str(v).strip() == '날짜'), None)
    if not header_row or header_row < 2:
        print("   ⚠️ 머리글 행(C열 '날짜')을 못 찾아 호스트별 머리글은 건너뜁니다.")
        return

    first = rowcol_to_a1(header_row, GA_HOST_FIRST_COLUMN)
    if sheet_input.get(rowcol_to_a1(header_row, INPUT_END_COLUMN)):
        return   # 마지막 열까지 머리글이 있으면 끝

    hosts, metrics = [], []
    for label in GA_USAGE_GROUPS:
        hosts.extend([label] + [""] * (len(GA_USAGE_METRICS) - 1))
        metrics.extend(title for _, title in GA_USAGE_METRICS)
    sheet_input.update(rowcol_to_a1(header_row - 1, GA_HOST_FIRST_COLUMN), [hosts, metrics],
                       value_input_option='USER_ENTERED')
    print(f"   ✅ 서비스별 MAU/DAU 머리글 추가 ({first}~)")


def refresh_ga_columns(sheet_input, target_date_obj):
    """이미 입력된 날짜 행의 GA4 열만 다시 채운다.

    GA4는 하루 치가 확정되기까지 24~48시간이 걸려서, 다음 날 아침에 한 번 읽은 값은
    실제보다 작다. 며칠 뒤 다시 읽어 덮어쓴다. 조회에 실패한 항목은 건드리지 않는다.
    """
    s_date = target_date_obj.strftime('%Y-%m-%d')
    row = find_input_rows(sheet_input, [target_date_obj])[target_date_obj]
    if not row:
        print(f"⏭️  {s_date} GA4 재수집 건너뜀 — 데이터입력시트에 행이 없습니다.")
        return True

    ensure_host_columns(sheet_input)
    ga_data = get_ga4_detailed_data(target_date_obj.strftime('%Y%m%d'))
    updates = [
        {"range": f"{col}{row}", "values": [[fmt(ga_data[key])]]}
        for col, key in GA_INPUT_COLUMNS if ga_data[key] is not None
    ]
    if updates:
        sheet_input.batch_update(updates, value_input_option='USER_ENTERED')
    print(f"   ✅ {s_date} GA4 열 {len(updates)}칸 재수집 ({row}행)")
    return not ga_data['errors']

# ==============================================================================
# [함수] 셀에 든 날짜 읽기 (재실행 감지용)
# ==============================================================================
SHEETS_EPOCH = date(1899, 12, 30)  # 구글 시트 날짜 serial 기준일


def _cell_date(sheet, row, col):
    """셀 값을 date로. 날짜가 아니면 None."""
    got = sheet.get(rowcol_to_a1(row, col), value_render_option='UNFORMATTED_VALUE')
    raw = got[0][0] if got and got[0] else None
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return SHEETS_EPOCH + timedelta(days=int(raw))
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


# ==============================================================================
# [함수] 그래프 시트에 한 줄 추가 (B열=날짜, C열~끝 = 윗행 복사)
# ==============================================================================
def add_graph_row(spreadsheet, tab_name, target_date_obj, end_column):
    print(f"🔹 [{tab_name}] 그래프 행 추가 시작...")
    sheet = spreadsheet.worksheet(tab_name)

    DATE_COL = 2        # B열
    START_COPY_COL = 3  # C열부터 복사

    # 1) B열에 값이 있는 마지막 행 찾기
    date_idx = DATE_COL - 1
    last_value_row = 0
    for i, r in enumerate(sheet.get_all_values(), start=1):
        cell = r[date_idx] if len(r) > date_idx else ""
        if str(cell).strip():
            last_value_row = i

    if last_value_row == 0:
        print(f"   ⚠️ [{tab_name}] B열에 기준 행이 없어 건너뜁니다.")
        return

    # 이미 그 날짜가 마지막 행에 있으면 재실행이므로 추가하지 않는다.
    # (중복 행이 생기면 누적 수식 '=G{n-1}+D{n}'이 영구히 틀어진다)
    if _cell_date(sheet, last_value_row, DATE_COL) == target_date_obj:
        print(f"   ⏭️ [{tab_name}] {target_date_obj} 이미 있음 — 건너뜁니다.")
        return

    source_row = last_value_row
    target_row = last_value_row + 1

    # 2) 행 추가 + B열 날짜 직접 입력 (자동 증가 아님, 코드에서 계산)
    #    inherit_from_before: B열의 날짜 서식('mm월 dd일')을 윗행에서 물려받게 한다.
    sheet.insert_row([], index=target_row, inherit_from_before=True)
    sheet.update_cell(target_row, DATE_COL, target_date_obj.strftime("%Y-%m-%d"))

    # 3) C열 ~ 끝까지 복사 (마우스로 꼭지점 끌어내린 효과)
    sheet.spreadsheet.batch_update({
        "requests": [{
            "copyPaste": {
                "source": {
                    "sheetId": sheet.id,
                    "startRowIndex": source_row - 1,
                    "endRowIndex": source_row,
                    "startColumnIndex": START_COPY_COL - 1,
                    "endColumnIndex": end_column
                },
                "destination": {
                    "sheetId": sheet.id,
                    "startRowIndex": target_row - 1,
                    "endRowIndex": target_row,
                    "startColumnIndex": START_COPY_COL - 1,
                    "endColumnIndex": end_column
                },
                "pasteType": "PASTE_NORMAL"
            }
        }]
    })

    print(f"   ✅ [{tab_name}] 행 추가 완료 → row={target_row}, 날짜={target_date_obj.strftime('%Y-%m-%d')}")


# ==============================================================================
# [함수] 데이터입력시트 C열 날짜 표기
# ==============================================================================
# C열은 최근 며칠만 'NN일' 텍스트로 두고 나머지는 YYYY-MM-DD 날짜로 유지한다.
# 'NN일'이 여러 달에 걸치면 MATCH/XMATCH가 볼 후보가 중복되기 때문.
# 보고양식이 최대 3일 뒤를 조회하므로 7일이면 여유가 있다.
INPUT_DAY_TEXT_WINDOW = 7

DAY_TEXT = re.compile(r"(\d+)일")
ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _parse_input_date(month_cell, day_cell):
    """B열('26년 07월') + C열('26일' 또는 '2026-07-26') -> date. 못 읽으면 None."""
    text = str(day_cell).strip()

    iso = ISO_DATE.fullmatch(text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None

    day = DAY_TEXT.fullmatch(text)
    ym = re.findall(r"\d+", str(month_cell))
    if not day or len(ym) < 2:
        return None
    try:
        return date(2000 + int(ym[0]), int(ym[1]), int(day.group(1)))
    except ValueError:
        return None


def find_input_rows(sheet_input, wanted_dates):
    """{date: 행번호}. DB 오류로 건너뛴 날이 있으면 행이 연속이 아니므로 실제로 찾는다."""
    months = sheet_input.col_values(2)
    days = sheet_input.col_values(3)

    found = {}
    for idx, (m, d) in enumerate(zip(months, days), start=1):
        dt = _parse_input_date(m, d)
        if dt:
            found[dt] = idx

    return {d: found.get(d) for d in wanted_dates}


def normalize_input_dates(sheet_input, target_date_obj, window=INPUT_DAY_TEXT_WINDOW):
    """윈도우보다 오래된 C열 'NN일' 표기를 YYYY-MM-DD 로 바꾼다.

    target 이후 행은 건드리지 않는다 (과거 날짜로 재실행해도 최신 표기가 안 망가지게).
    """
    cutoff = target_date_obj - timedelta(days=window - 1)
    months = sheet_input.col_values(2)
    days = sheet_input.col_values(3)

    rows = []
    for idx, (m, d) in enumerate(zip(months, days), start=1):
        if not DAY_TEXT.fullmatch(str(d).strip()):
            continue
        dt = _parse_input_date(m, d)
        if dt and dt < cutoff:
            rows.append((idx, dt))

    if not rows:
        print(f"   ✅ C열 표기 정상 (최근 {window}일만 'NN일')")
        return

    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        sheet_input.batch_update(
            [{"range": f"C{idx}", "values": [[dt.strftime("%Y-%m-%d")]]} for idx, dt in chunk],
            value_input_option='USER_ENTERED',
        )
        # 기존 변환 셀과 같은 서식으로 맞춘다.
        sheet_input.spreadsheet.batch_update({"requests": [
            {"repeatCell": {
                "range": {"sheetId": sheet_input.id, "startRowIndex": idx - 1,
                          "endRowIndex": idx, "startColumnIndex": 2, "endColumnIndex": 3},
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
                "fields": "userEnteredFormat.numberFormat",
            }} for idx, _ in chunk
        ]})

    print(f"   ✅ C열 {len(rows)}칸 YYYY-MM-DD 변환 ({rows[0][1]} ~ {rows[-1][1]})")


# ==============================================================================
# [함수] 평일보고양식 날짜 헤더 갱신
# ==============================================================================
def update_report_dates(sheet_report, target_date_obj):
    def labels(n):
        return [f"{(target_date_obj - timedelta(days=i)).day:02d}일" for i in range(n - 1, -1, -1)]

    sheet_report.batch_update(
        [{"range": rng, "values": [labels(n) * groups]} for rng, n, groups in REPORT_DATE_RANGES],
        value_input_option='USER_ENTERED',
    )
    print(f"   ✅ 날짜 헤더 갱신 (왼쪽 {labels(2)}, 오른쪽 {labels(3)})")


# ==============================================================================
# [함수] 모바일보고양식 갱신 (좌/우 블록 각각 최근 N일)
# ==============================================================================
def update_mobile_report(spreadsheet, target_date_obj, input_rows):
    print(f"🔹 [{TAB_NAME_MOBILE_REPORT}] 갱신 시작...")
    sheet = spreadsheet.worksheet(TAB_NAME_MOBILE_REPORT)

    def ref(template, d):
        row = input_rows.get(d)
        return template.format(tab=TAB_NAME_INPUT, row=row) if row else ""

    def rng(row, col, width):
        return f"{rowcol_to_a1(row, col)}:{rowcol_to_a1(row, col + width - 1)}"

    updates = []
    for block in MOBILE_BLOCKS:
        c0 = block["start_col"]
        n = block["days"]
        days = [target_date_obj - timedelta(days=i) for i in range(n - 1, -1, -1)]
        last_day = days[-1]

        updates.append({
            "range": rng(MOBILE_HEADER_ROW, c0, n),
            "values": [[f"{d.day:02d}일" for d in days]],
        })
        updates.append({
            "range": (
                f"{rowcol_to_a1(MOBILE_DATA_FIRST_ROW, c0)}:"
                f"{rowcol_to_a1(MOBILE_DATA_FIRST_ROW + len(MOBILE_DATA_FORMULAS) - 1, c0 + n - 1)}"
            ),
            "values": [[ref(t, d) for d in days] for t in MOBILE_DATA_FORMULAS],
        })
        # MAU/DAU는 당일 1일치만 (블록 폭과 무관하게 2칸)
        updates.append({
            "range": rng(MOBILE_USAGE_HEADER_ROW, c0, 2),
            "values": [[f"MAU({last_day:%m/%d})", f"DAU({last_day:%m/%d})"]],
        })
        updates.append({
            "range": rng(MOBILE_USAGE_VALUE_ROW, c0, 2),
            "values": [[ref(t, last_day) for t in MOBILE_USAGE_FORMULAS]],
        })

    sheet.batch_update(updates, value_input_option='USER_ENTERED')

    missing = sorted(d for d, r in input_rows.items() if not r)
    if missing:
        print(f"   ⚠️ 데이터입력시트에서 못 찾은 날짜(빈칸 처리): {[str(d) for d in missing]}")
    print(f"   ✅ 갱신 완료")


# ==============================================================================
# [함수] 하루 처리 프로세스 (시트별 기능 분리 버전)
# ==============================================================================
def process_one_day(target_date_obj):
    q_date = target_date_obj.strftime('%Y%m%d')
    s_date = target_date_obj.strftime('%Y-%m-%d')
    
    print(f"\n{'='*50}")
    print(f"📅 날짜 처리 시작: {s_date}")
    print(f"{'='*50}")

    # 0. 시트 먼저 열고 재실행 여부 확인 (중복 행 방지)
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
        spreadsheet = gspread.authorize(creds).open_by_key(SHEET_ID)
        sheet_input = spreadsheet.worksheet(TAB_NAME_INPUT)
    except Exception:
        print(f"❌ {s_date} 구글 시트 연결 실패:")
        traceback.print_exc()
        return False

    if find_input_rows(sheet_input, [target_date_obj])[target_date_obj]:
        print(f"⏭️  {s_date} 이미 입력되어 있음 — 건너뜁니다.")
        print("    (다시 넣으려면 데이터입력시트/그래프시트의 해당 행을 지우고 재실행)")
        return True

    # 1. 데이터 수집 (모든 시트 공통)
    yh = get_yh_data(q_date)
    hdb = get_hdb_data(q_date)
    ga_data = get_ga4_detailed_data(q_date)
    if ga_data['errors']:
        # DB 수치는 그대로 넣고, 실패한 GA4 칸만 빈칸으로 둔다 (0으로 채우지 않는다).
        GA_INCOMPLETE_DATES.append(target_date_obj)

    if not yh or not hdb:
        print(f"❌ {s_date} 데이터 수집 실패로 건너뜁니다.")
        return False

    # 2. 변수 계산 및 준비
    a015_tot = yh['a015_sms'] + yh['a015_lms'] + yh['a015_mms']
    gen_tot = hdb['gen_sms'] + hdb['gen_lms'] 
    
    # 날짜 문자열 (보고양식용)
    # 날짜 문자열 (데이터입력용)
    ym = f"{s_date[2:4]}년 {s_date[5:7]}월"; dd = f"{s_date[8:10]}일"

    try:
        # ======================================================================
        # [Sheet 1] 데이터입력시트 -> "처음에 만든 기능 (데이터 입력)"
        # ======================================================================
        print(f"🔹 [{TAB_NAME_INPUT}] 데이터 입력 시작...")
        ensure_host_columns(sheet_input)
        col_b = sheet_input.col_values(2)
        last_row = len(col_b)
        input_target_row = last_row + 1
        
        # 행 확보
        if any(str(v).strip() for v in sheet_input.row_values(input_target_row)):
            sheet_input.insert_row([], index=input_target_row)

        # 윗줄 서식 복사 (기본)
        sheet_input.spreadsheet.batch_update({
            "requests": [{"copyPaste": {
                "source": { 
                    "sheetId": sheet_input.id, 
                    "startRowIndex": last_row-1, "endRowIndex": last_row, 
                    "startColumnIndex": 0, "endColumnIndex": INPUT_END_COLUMN 
                },
                "destination": { 
                    "sheetId": sheet_input.id, 
                    "startRowIndex": input_target_row-1, "endRowIndex": input_target_row, 
                    "startColumnIndex": 0, "endColumnIndex": INPUT_END_COLUMN 
                },
                "pasteType": "PASTE_NORMAL"
            }}]
        })

        # 데이터 조립
        def parse_and_add(cell_val, new_cnt):
            try:
                txt = str(cell_val).strip()
                if not txt: return fmt(new_cnt)
                if '(' in txt:
                    p = txt.split('('); m = int(re.sub(r'[^\d]', '', p[0])); s = int(re.sub(r'[^\d]', '', p[1]))
                    return f"{m + new_cnt:,}({s + new_cnt:,})"
                else: n = int(re.sub(r'[^\d]', '', txt)); return f"{n + new_cnt:,}"
            except: return fmt(new_cnt)

        prev_acc = sheet_input.cell(last_row, 5).value 
        curr_acc = parse_and_add(prev_acc, yh['new_user'])

        row = []
        row.extend(["", ym, dd]) 
        row.extend([fmt(yh['new_user']), curr_acc, fmt(yh['charge'])]) 
        row.extend([fmt(yh['mode_sms']), fmt(yh['mode_lms']), fmt(yh['mode_mms'])])
        row.append(f"=SUM(G{input_target_row}:I{input_target_row})")
        row.extend([fmt(hdb['gen_in_total']), "0", fmt(hdb['cs_in'])])
        row.append(f"=O{input_target_row}-K{input_target_row}-M{input_target_row}")
        row.append(fmt(hdb['total_in']))
        
        # GA4 데이터 (Y: 015 전체 MAU)
        row.extend([
            fmt(ga_data['web_signup']), fmt(ga_data['app_signup']),
            fmt(ga_data['cash_charge']),
            fmt(ga_data['web_login']), fmt(ga_data['app_login']),
            fmt(ga_data['voicemail']), fmt(ga_data['guide']),
            fmt(ga_data['a015_user']), fmt(ga_data['arreo_user']),
            fmt(ga_data['mau_015_total']) 
        ])
        
        row.extend([fmt(hdb['route_sms']), fmt(hdb['route_mms'])])
        row.append(f"=Z{input_target_row}+AA{input_target_row}")
        row.extend([fmt(hdb['gen_sms']), fmt(hdb['gen_lms']), fmt(gen_tot)])
        row.append(f"=IFERROR(ROUND(AB{input_target_row}/AE{input_target_row}*100, 0), 0)")
        row.extend([fmt(yh['arreo_sms']), fmt(yh['arreo_lms']), fmt(yh['arreo_mms'])])
        row.append(f"=SUM(AG{input_target_row}:AI{input_target_row})")
        
        # 앱 DAU, 앱 MAU
        row.extend([fmt(ga_data['dau_app']), fmt(ga_data['mau_app'])])
        
        row.extend([fmt(yh["one_to_one_sms"]), fmt(yh["one_to_one_lms"]), fmt(yh["one_to_one_mms"]), fmt(f"=SUM(AM{input_target_row}:AO{input_target_row})")])
        row.extend([fmt(yh['a015_sms']), fmt(yh['a015_lms']), fmt(yh['a015_mms']), fmt(a015_tot)])

        # AU~BB: 서비스별 MAU/DAU (기존 A~AT 레이아웃은 그대로)
        assert len(row) == INPUT_BASE_COLUMNS, f"기존 열 수가 {INPUT_BASE_COLUMNS}이 아님: {len(row)}"
        row.extend(fmt(ga_data[key]) for key in GA_HOST_KEYS)

        sheet_input.update(f"A{input_target_row}", [row], value_input_option='USER_ENTERED')
        print(f"   ✅ 입력 완료")

        normalize_input_dates(sheet_input, target_date_obj)


        # ======================================================================
        # [Sheet 2] 그래프_데이터 / 모바일_그래프_데이터 -> B열 날짜, C열~끝 복사
        # ======================================================================
        add_graph_row(spreadsheet, TAB_NAME_GRAPH, target_date_obj, GRAPH_END_COLUMN)
        add_graph_row(spreadsheet, TAB_NAME_MOBILE_GRAPH, target_date_obj, MOBILE_GRAPH_END_COLUMN)

        # ======================================================================
        # [Sheet 3] 평일보고양식 -> "일 수 변경 기능 (D7:I7)"
        # ======================================================================
        print(f"🔹 [{TAB_NAME_REPORT}] 헤더 날짜 변경 시작...")
        sheet_report = spreadsheet.worksheet(TAB_NAME_REPORT)
        update_report_dates(sheet_report, target_date_obj)

        print(f"🔹 [{TAB_NAME_REPORT}] 1:1 발송 수식 갱신 시작...")
        input_tab = TAB_NAME_INPUT
        new_formulas = [
            [f"='{input_tab}'!AM{input_target_row}"],
            [f"='{input_tab}'!AN{input_target_row}"],
            [f"='{input_tab}'!AO{input_target_row}"],
        ]
        # 왼쪽 블록은 헤더 7행 / 데이터 8~10행,
        # 오른쪽 블록은 헤더 8행 / 데이터 9~11행으로 한 행 밀려 있다.
        # AE8에 쓰면 '1:1 발송' 헤더를 덮어쓰고 SMS/LMS/MMS가 한 칸씩 어긋난다.
        sheet_report.update("L8:L10", new_formulas, value_input_option="USER_ENTERED")
        sheet_report.update("AE9:AE11", new_formulas, value_input_option="USER_ENTERED")

        print(f"   ✅ 수식 갱신 완료")

        # ======================================================================
        # [Sheet 5] 모바일보고양식 -> 날짜 헤더 + 날짜별 수식
        # ======================================================================
        max_days = max(b["days"] for b in MOBILE_BLOCKS)
        wanted = [target_date_obj - timedelta(days=i) for i in range(max_days)]
        input_rows = find_input_rows(sheet_input, wanted)
        input_rows[target_date_obj] = input_target_row  # 방금 쓴 행은 확실히 안다
        update_mobile_report(spreadsheet, target_date_obj, input_rows)
        return True

    except Exception:
        print(f"❌ {s_date} 처리 중 오류:")
        traceback.print_exc()
        return False


def open_input_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(TAB_NAME_INPUT)


# ==============================================================================
# [Main]
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description='일일 통계 자동화 봇')
    parser.add_argument('--date', type=str, help='특정 날짜 실행 (YYYYMMDD)')
    parser.add_argument('--start', type=str, help='시작 날짜 (YYYYMMDD)')
    parser.add_argument('--end', type=str, help='종료 날짜 (YYYYMMDD)')
    parser.add_argument('--refresh-ga', action='store_true',
                        help='행을 추가하지 않고 지정한 날짜의 GA4 열만 다시 채움 (--date 또는 --start/--end 필요)')
    parser.add_argument('--no-ga-refresh', action='store_true',
                        help=f'기본 실행 때 직전 {GA_REFRESH_DAYS}일의 GA4 재수집을 생략')
    
    args = parser.parse_args()

    require_db_settings()
    require_google_settings()
    
    start_date = None
    end_date = None
    daily_run = not args.date and not (args.start and args.end)

    if args.date:
        try:
            d = datetime.strptime(args.date, '%Y%m%d').date()
            start_date = d; end_date = d
        except ValueError: print("❌ 날짜 형식이 올바르지 않습니다."); return
    elif args.start and args.end:
        try:
            start_date = datetime.strptime(args.start, '%Y%m%d').date()
            end_date = datetime.strptime(args.end, '%Y%m%d').date()
        except ValueError: print("❌ 날짜 형식이 올바르지 않습니다."); return
    else:
        yesterday = date.today() - timedelta(days=1)
        start_date = yesterday; end_date = yesterday

    print(f"🚀 봇 실행 범위: {start_date} ~ {end_date}")

    # GA4 열만 다시 채우는 모드 (행 추가/보고양식 갱신 없음)
    if args.refresh_ga:
        if daily_run:
            print("❌ --refresh-ga 는 --date 또는 --start/--end 와 함께 써야 합니다."); return
        sheet_input = open_input_sheet()
        failed = []
        curr = start_date
        while curr <= end_date:
            if not refresh_ga_columns(sheet_input, curr):
                failed.append(curr)
            curr += timedelta(days=1)
        if failed:
            print(f"\n❌ GA4 재수집 실패 {len(failed)}건: {[str(d) for d in failed]}")
            sys.exit(1)
        print("\n🎉 GA4 재수집이 끝났습니다.")
        return

    failed = []
    curr = start_date
    while curr <= end_date:
        if not process_one_day(curr):
            failed.append(curr)
        curr += timedelta(days=1)

    # 기본(어제) 실행이면, 그 앞 며칠의 GA4 값을 확정치로 다시 채운다.
    if daily_run and not args.no_ga_refresh and GA_REFRESH_DAYS > 0:
        print(f"\n🔄 직전 {GA_REFRESH_DAYS}일 GA4 재수집...")
        try:
            sheet_input = open_input_sheet()
            for i in range(1, GA_REFRESH_DAYS + 1):
                past = start_date - timedelta(days=i)
                if not refresh_ga_columns(sheet_input, past):
                    GA_INCOMPLETE_DATES.append(past)
        except Exception:
            print("❌ GA4 재수집 중 오류:")
            traceback.print_exc()
            failed.append("GA4 재수집")

    if GA_INCOMPLETE_DATES:
        days = sorted({str(d) for d in GA_INCOMPLETE_DATES})
        print(f"\n⚠️ GA4 조회가 일부 실패한 날짜 {len(days)}건: {days}")
        print("    해당 칸은 빈칸입니다. `python main.py --refresh-ga --date YYYYMMDD` 로 다시 채우세요.")
        failed.extend(days)

    if failed:
        # cron/모니터링이 실패를 감지하려면 종료 코드가 0이 아니어야 한다.
        print(f"\n❌ 실패한 날짜 {len(failed)}건: {[str(d) for d in failed]}")
        sys.exit(1)

    print("\n🎉 모든 작업이 종료되었습니다.")

if __name__ == "__main__":
    main()
