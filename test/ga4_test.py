import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric, Dimension

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
import pymysql

from settings import HDB_CONFIG, YH_DB_CONFIG, require_db_settings

# ==============================================================================
# [설정] 테스트할 정보 입력
# ==============================================================================
JSON_KEY_FILE = 'beep-5f601-9433bd407866.json' 
"""
✅ 추가로 보고 싶은 지표
- 누적 활성 기기수: totalUsers (누적 사용자 수를 기기수로 간주)
- 일일 추가 기기수: newUsers (해당일 신규 사용자)

※ GA4 Data API에는 "device" 전용 누적 지표가 제한적이라,
  일반적으로 앱에서는 사용자(=기기) 단위로 보는 경우가 많습니다.
"""

GA4_WEB_IDS = ['445891939','444980831']  # 웹 ID 리스트
GA4_APP_ID = '461784274'                 # 앱 ID

# "누적"을 계산할 시작일(너무 과거면 GA4가 400을 반환할 수 있음)
# 기본값은 GA4 최소 허용 범위(대략 2015년 이후)로 둡니다.
ACCUM_START_DATE = "2015-08-14"

# 키워드/이벤트 설정 (필요시 수정)
WEB_SIGNUP_KEYWORDS = ['join', 'regist', 'signup']
WEB_CHARGE_KEYWORDS = ['charge', 'payment']
WEB_LOGIN_KEYWORDS = ['signin', 'login']
APP_SIGNUP_KEYWORDS = ['signup', 'join', 'register']
SEND_EVENT_NAMES = ['Send Message', 'send_message', 'sendMessage']
APP_CHARGE_EVENT_NAMES = ['purchase', 'in_app_purchase']

# ==============================================================================
# [함수] 결과 출력 헬퍼
# ==============================================================================
def print_report(response, title):
    print(f"\n>> {title}")
    print("-" * 80) # 구분선 길이 조금 늘림
    
    if not response.rows:
        print("❌ 데이터가 없습니다.")
        return

    # 헤더 (자동으로 컬럼이 늘어납니다)
    headers = [h.name for h in response.dimension_headers] + \
              [h.name for h in response.metric_headers]
    print(f" | ".join(headers))
    print("-" * 80)

    # 데이터 (최대 20개)
    for i, row in enumerate(response.rows):
        if i >= 20: break
        dims = [v.value for v in row.dimension_values]
        mets = [v.value for v in row.metric_values]
        print(f" | ".join(dims + mets))
    print("-" * 80)

# ==============================================================================
# [유틸] 단일 metric 값 뽑기
# ==============================================================================
def get_single_metric(client, prop, metric_name, start_date, end_date):
    req = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        metrics=[Metric(name=metric_name)],
    )
    res = client.run_report(req)
    if not res.rows:
        return 0
    return res.rows[0].metric_values[0].value

def sum_metric_by_keyword(client, prop, date_str, dimension_name, metric_name, keywords, limit=200):
    req = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
        dimensions=[Dimension(name=dimension_name)],
        metrics=[Metric(name=metric_name)],
        limit=limit,
    )
    res = client.run_report(req)
    if not res.rows:
        return 0

    keys = [k.lower() for k in keywords]
    total = 0
    for row in res.rows:
        dim_val = row.dimension_values[0].value.lower()
        if any(k in dim_val for k in keys):
            total += int(row.metric_values[0].value)
    return total

def sum_event_count(client, prop, date_str, event_names, limit=200):
    req = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
        dimensions=[Dimension(name="eventName")],
        metrics=[Metric(name="eventCount")],
        limit=limit,
    )
    res = client.run_report(req)
    if not res.rows:
        return 0

    names = {n.lower() for n in event_names}
    total = 0
    for row in res.rows:
        if row.dimension_values[0].value.lower() in names:
            total += int(row.metric_values[0].value)
    return total

def ratio(numer, denom):
    if not denom:
        return "n/a"
    return f"{(numer / denom) * 100:.1f}%"


_MIN_START_DATE_RE = re.compile(r"must be greater than (\d{4}-\d{2}-\d{2})")


def get_total_users_cumulative(client, prop, end_date):
    """
    totalUsers를 '누적'처럼 보기 위해 start_date~end_date로 조회.
    start_date가 너무 과거면 GA4가 400을 반환하므로, 에러 메시지에서
    허용 최소 날짜를 파싱해 1일 더한 값으로 1회 재시도합니다.
    """
    start_date = ACCUM_START_DATE
    try:
        return get_single_metric(client, prop, "totalUsers", start_date, end_date)
    except Exception as e:
        m = _MIN_START_DATE_RE.search(str(e))
        if not m:
            raise

        min_allowed = date.fromisoformat(m.group(1)) + timedelta(days=1)
        end_dt = date.fromisoformat(end_date)
        if min_allowed > end_dt:
            return 0

        retry_start = min_allowed.isoformat()
        return get_single_metric(client, prop, "totalUsers", retry_start, end_date)

def print_funnel(title, visits, signups, charges, sends):
    print(f"\n>> {title}")
    print(f"- 방문(Active Users)         : {visits}")
    print(f"- 가입(키워드 매칭)          : {signups}   (전환율: {ratio(signups, visits)})")
    print(f"- 충전(키워드/이벤트)        : {charges}   (전환율: {ratio(charges, signups)})")
    print(f"- 발송(이벤트)               : {sends}   (전환율: {ratio(sends, charges)})")

def print_retention(title, dau, wau, mau):
    print(f"\n>> {title}")
    print(f"- DAU(1일 활성)             : {dau}")
    if wau is not None:
        print(f"- WAU(7일 활성)             : {wau}")
    print(f"- MAU(28일 활성)            : {mau}")
    print(f"- DAU/MAU (Stickiness)      : {ratio(dau, mau)}")

# ==============================================================================
# [메인] 테스트 실행
# ==============================================================================
def main():
    print("🚀 GA4 멀티(Web 2개 / App 1개) 연결 테스트 시작... (사용자 수 포함)\n")

    if not os.path.exists(JSON_KEY_FILE):
        print(f"❌ 오류: 키 파일('{JSON_KEY_FILE}')이 없습니다.")
        return

    try:
        client = BetaAnalyticsDataClient.from_service_account_json(JSON_KEY_FILE)
    except Exception as e:
        print(f"❌ 인증 오류: {e}")
        return

    # 어제 날짜 기준
    yesterday = date.today() - timedelta(days=1)
    date_str = yesterday.strftime('%Y-%m-%d')
    q_date = yesterday.strftime('%Y%m%d')
    print(f"📅 조회 대상 날짜: {date_str}\n")
    
    # ==========================================================================
    # 1. WEB 속성 테스트 (반복문으로 2개 모두 조회)
    # ==========================================================================
    for idx, web_id in enumerate(GA4_WEB_IDS):
        print(f"\n🌐 [1-{idx+1}] WEB 속성 테스트 (ID: {web_id})")
        print("=" * 80)
        web_prop = f"properties/{web_id}"

        try:
            # 1-1. 웹 페이지 URL 확인 (★ 수정됨: 조회수 + 사용자수)
            req_page = RunReportRequest(
                property=web_prop,
                date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
                dimensions=[Dimension(name="pagePath")],
                # Metric 2개 요청: 페이지뷰(screenPageViews), 활성사용자(activeUsers)
                metrics=[Metric(name="screenPageViews"), Metric(name="activeUsers")], 
                limit=100
            )
            print_report(client.run_report(req_page), f"ID:{web_id} - 상위 방문 페이지 (뷰 / 사용자)")

            # 1-2. 웹 이벤트 확인
            req_evt = RunReportRequest(
                property=web_prop,
                date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
                dimensions=[Dimension(name="eventName")],
                metrics=[Metric(name="eventCount")]
            )
            print_report(client.run_report(req_evt), f"ID:{web_id} - 발생 이벤트")

            # 1-3. 웹 호스트 확인
            req_host = RunReportRequest(
                property=web_prop,
                date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
                dimensions=[Dimension(name="hostName")],
                metrics=[Metric(name="activeUsers")]
            )
            print_report(client.run_report(req_host), f"ID:{web_id} - 방문 도메인")

            # 1-4. 웹 MAU
            req_mau = RunReportRequest(
                property=web_prop,
                date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
                metrics=[Metric(name="active28DayUsers")]
            )
            res_mau = client.run_report(req_mau)
            mau_val = res_mau.rows[0].metric_values[0].value if res_mau.rows else 0
            print(f"📊 ID:{web_id} - MAU (28일 활성 유저): {mau_val}")

            # 1-5. 누적 활성 기기수 / 일일 추가 기기수
            total_users = None
            new_users = None
            try:
                total_users = get_total_users_cumulative(client, web_prop, date_str)
            except Exception as e:
                print(f"⚠️ ID:{web_id} - 누적 활성 기기수 조회 실패: {e}")
            try:
                new_users = get_single_metric(client, web_prop, "newUsers", date_str, date_str)
            except Exception as e:
                print(f"⚠️ ID:{web_id} - 일일 추가 기기수 조회 실패: {e}")

            if total_users is not None:
                print(f"📈 ID:{web_id} - 누적 활성 기기수(누적 사용자): {total_users}")
            if new_users is not None:
                print(f"📈 ID:{web_id} - 일일 추가 기기수(신규 사용자): {new_users}")

            # 1-6. 전환 퍼널 (GA4 기반)
            visits = int(get_single_metric(client, web_prop, "activeUsers", date_str, date_str))
            signups = sum_metric_by_keyword(
                client, web_prop, date_str, "pagePath", "screenPageViews", WEB_SIGNUP_KEYWORDS
            )
            charges = sum_metric_by_keyword(
                client, web_prop, date_str, "pagePath", "screenPageViews", WEB_CHARGE_KEYWORDS
            )
            sends = sum_event_count(client, web_prop, date_str, SEND_EVENT_NAMES)
            print_funnel(f"ID:{web_id} - 전환 퍼널 (GA4 기반)", visits, signups, charges, sends)

            # 1-7. 리텐션/활성 지표
            try:
                wau = int(get_single_metric(client, web_prop, "active7DayUsers", date_str, date_str))
            except Exception:
                wau = None
            dau = visits
            mau = int(mau_val) if str(mau_val).isdigit() else 0
            print_retention(f"ID:{web_id} - 리텐션/활성", dau, wau, mau)

        except Exception as e:
            print(f"❌ WEB ID({web_id}) 조회 실패: {e}")


    print("\n\n")


    # ==========================================================================
    # 2. APP 속성 테스트
    # ==========================================================================
    print(f"📱 [2] APP 속성 테스트 (ID: {GA4_APP_ID})")
    print("=" * 80)
    app_prop = f"properties/{GA4_APP_ID}"

    try:
        # 2-1. 앱 화면 이름 확인 (★ 수정됨: 조회수 + 사용자수)
        req_screen = RunReportRequest(
            property=app_prop,
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            dimensions=[Dimension(name="unifiedScreenName")],
            # Metric 2개 요청: 화면조회(screenPageViews), 활성사용자(activeUsers)
            metrics=[Metric(name="screenPageViews"), Metric(name="activeUsers")],
            limit=20
        )
        print_report(client.run_report(req_screen), "상위 방문 화면 (뷰 / 사용자)")

        # 2-2. 앱 이벤트 확인
        req_app_evt = RunReportRequest(
            property=app_prop,
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")]
        )
        print_report(client.run_report(req_app_evt), "앱 발생 이벤트")

        # 2-3. 앱 MAU
        req_app_mau = RunReportRequest(
            property=app_prop,
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            metrics=[Metric(name="active28DayUsers")]
        )
        res_app_mau = client.run_report(req_app_mau)
        mau_val_app = res_app_mau.rows[0].metric_values[0].value if res_app_mau.rows else 0
        print(f"📊 앱 MAU (28일 활성 유저): {mau_val_app}")

        # 2-4. 누적 활성 기기수 / 일일 추가 기기수
        total_users_app = None
        new_users_app = None
        try:
            total_users_app = get_total_users_cumulative(client, app_prop, date_str)
        except Exception as e:
            print(f"⚠️ 앱 누적 활성 기기수 조회 실패: {e}")
        try:
            new_users_app = get_single_metric(client, app_prop, "newUsers", date_str, date_str)
        except Exception as e:
            print(f"⚠️ 앱 일일 추가 기기수 조회 실패: {e}")

        if total_users_app is not None:
            print(f"📈 앱 누적 활성 기기수(누적 사용자): {total_users_app}")
        if new_users_app is not None:
            print(f"📈 앱 일일 추가 기기수(신규 사용자): {new_users_app}")

        # 2-5. 전환 퍼널 (GA4 기반)
        app_visits = int(get_single_metric(client, app_prop, "activeUsers", date_str, date_str))
        app_signups = sum_metric_by_keyword(
            client, app_prop, date_str, "unifiedScreenName", "screenPageViews", APP_SIGNUP_KEYWORDS
        )
        app_charges = sum_event_count(client, app_prop, date_str, APP_CHARGE_EVENT_NAMES)
        app_sends = sum_event_count(client, app_prop, date_str, SEND_EVENT_NAMES)
        print_funnel("APP - 전환 퍼널 (GA4 기반)", app_visits, app_signups, app_charges, app_sends)

        # 2-6. 리텐션/활성 지표
        try:
            app_wau = int(get_single_metric(client, app_prop, "active7DayUsers", date_str, date_str))
        except Exception:
            app_wau = None
        app_mau = int(mau_val_app) if str(mau_val_app).isdigit() else 0
        print_retention("APP - 리텐션/활성", app_visits, app_wau, app_mau)

    except Exception as e:
        print(f"❌ APP 속성 조회 실패: {e}")

    # ==========================================================================
    # 3. DB 기반 지표 (VPN 필요)
    # ==========================================================================
    print("\n\n🗄️ [3] DB 기반 지표 (VPN 필요)")
    print("=" * 80)
    try:
        require_db_settings()
    except Exception as e:
        print(f"⚠️ DB 설정 오류: {e}")
        return

    # 3-1. YH-DB (충전/발송/1:1 vs 1:N)
    try:
        conn = psycopg2.connect(**YH_DB_CONFIG)
        cur = conn.cursor()

        # 충전 수/금액
        cur.execute(
            """
            select count(*), coalesce(sum(h.pg), 0)
            from arreo.mbr_usr_h h
            where h.wrt_dttm LIKE %s || '%%'
              and h.acct_gb = '1'
              and h.sms_gb in ('W', 'M')
              and h.site_id = 'arreo'
            """,
            (q_date,),
        )
        charge_cnt, charge_sum = cur.fetchone()
        print(f"- [YH] 충전 건수/금액: {charge_cnt}건 / {charge_sum}원")

        # 발송 성공률 (xms_send 기준)
        y_date = date(int(q_date[:4]), int(q_date[4:6]), int(q_date[6:]))
        today_str = (y_date + timedelta(days=1)).strftime('%Y%m%d')
        cur.execute(
            """
            select
                sum(case when x.rslt_val = '-100' then 1 else 0 end) as success,
                count(*) as total
            from arreo.xms_send x
            where x.snd_dttm >= %s and x.snd_dttm < %s
              and x.sms_st != 'C' and x.site_id = 'arreo'
            """,
            (q_date, today_str),
        )
        success_cnt, total_cnt = cur.fetchone()
        print(f"- [YH] 발송 성공률: {success_cnt}/{total_cnt} ({ratio(success_cnt, total_cnt)})")

        # 1:1 vs 1:N
        cur.execute(
            """
            select m.cmp_usr_id,
                sum(case when recipients_count = 1 then 1 else 0 end) as one_to_one,
                sum(case when recipients_count > 1 then recipients_count else 0 end) as one_to_n
            from arreo.smt_send_m m
            where m.snd_dttm >= %s and m.snd_dttm < %s
            group by m.cmp_usr_id
            """,
            (q_date, today_str),
        )
        print("- [YH] 1:1 vs 1:N (SMS/LMS/MMS)")
        for c_id, one_to_one, one_to_n in cur.fetchall():
            label = {"00000": "SMS", "LMS01": "LMS", "MMS01": "MMS"}.get(c_id, c_id)
            print(f"  {label}: 1:1={one_to_one} / 1:N={one_to_n} (비율 {ratio(one_to_one, one_to_one + one_to_n)})")

        conn.close()
    except Exception as e:
        print(f"⚠️ YH-DB 조회 실패: {e}")

    # 3-2. HDB (라우팅 성공률)
    try:
        conn = pymysql.connect(**HDB_CONFIG)
        cur = conn.cursor()
        cur.execute(
            f"""
            select 'SMS' as kind,
                   sum(case when RESULT_CODE = '-100' then 1 else 0 end) as success,
                   count(*) as total
            from SMSMT.SMS_H
            where SND_DT = '{q_date}' and GROUP_CODE like 'B00%%' and SETTLE_CODE like 'SMT-%%'
            union all
            select 'MMS' as kind,
                   sum(case when RESULT_CODE = '-100' then 1 else 0 end) as success,
                   count(*) as total
            from MMSMT.MMS_H
            where SND_DT = '{q_date}' and GROUP_CODE like 'B00%%' and SETTLE_CODE like 'SMT-%%'
            """
        )
        rows = cur.fetchall()
        for kind, success_cnt, total_cnt in rows:
            print(f"- [HDB] {kind} 라우팅 성공률: {success_cnt}/{total_cnt} ({ratio(success_cnt, total_cnt)})")
        conn.close()
    except Exception as e:
        print(f"⚠️ HDB 조회 실패: {e}")

if __name__ == "__main__":
    main()
