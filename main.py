import argparse
import sys
from decimal import Decimal
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import psycopg2
import pymysql
from datetime import date, timedelta, datetime
import re
import traceback

# [GA4 라이브러리]
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension
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

# [키워드 설정]
GA_CONFIG = {
    # 1. [웹] URL 키워드
    'web_signup_keyword': ['join', 'regist', 'signup'],   
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
    'host_arreo': ['arreo']               
}

# [설정] 시트 이름 정의 (실제 시트 이름과 똑같이 맞춰주세요!)
TAB_NAME_INPUT = '데이터입력시트'    # 1. 데이터 입력하는 곳
TAB_NAME_GRAPH = '그래프_데이터'     # 2. 수식 복사하는 곳
TAB_NAME_REPORT = '평일보고양식'     # 3. 날짜 헤더 바꾸는 곳
GRAPH_START_ROW = 4                 # 그래프_데이터 데이터 시작 행 (헤더/여백 고려)
GRAPH_DATE_COLUMN = 3              # 0이면 날짜 입력 안 함 (1=A, 2=B ...)


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
# [함수] 데이터 수집 - GA4 (집계 기준 수정)
# ==============================================================================
def get_ga4_detailed_data(target_date):
    print(f"📊 GA4 상세 데이터 수집 중... ({target_date})")
    
    date_fmt = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    client = BetaAnalyticsDataClient.from_service_account_json(JSON_KEY_FILE)
    
    res = {
        'web_signup': 0, 'app_signup': 0, 'cash_charge': 0,
        'web_login': 0, 'app_login': 0,
        'voicemail': 0, 'guide': 0,
        'a015_user': 0, 'arreo_user': 0,
        'mau_015_total': 0,   # Y열 (015 Web + App)
        'dau_app': 0,         # AK열 (App only)
        'mau_app': 0          # AL열 (App only)
    }
    
    # 1. WEB 속성들 조회
    for web_id in GA4_WEB_IDS:
        try:
            prop_web = f"properties/{web_id}"
            
            # 1-1. 웹 페이지 [사용자 수]
            req_page = RunReportRequest(
                property=prop_web,
                date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
                dimensions=[Dimension(name="pagePath")],
                metrics=[Metric(name="activeUsers")]  
            )
            for row in client.run_report(req_page).rows:
                path = row.dimension_values[0].value
                cnt = int(row.metric_values[0].value) 
                
                if any(k in path for k in GA_CONFIG['web_signup_keyword']): res['web_signup'] += cnt
                if any(k in path for k in GA_CONFIG['web_charge_keyword']): res['cash_charge'] += cnt
                if any(k in path for k in GA_CONFIG['web_voice_keyword']):  res['voicemail'] += cnt
                if any(k in path for k in GA_CONFIG['web_guide_keyword']):  res['guide'] += cnt
                if any(k in path for k in GA_CONFIG['web_login_page_keyword']): res['web_login'] += cnt

            # 1-3. 웹 호스트 (방문자)
            req_host = RunReportRequest(
                property=prop_web,
                date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
                dimensions=[Dimension(name="hostName")],
                metrics=[Metric(name="activeUsers")]
            )
            for row in client.run_report(req_host).rows:
                host = row.dimension_values[0].value
                users = int(row.metric_values[0].value)
                
                if any(k in host for k in GA_CONFIG['host_a015']): 
                    res['a015_user'] += users
                elif any(k in host for k in GA_CONFIG['host_arreo']): 
                    res['arreo_user'] += users

            # 1-4. 웹 MAU (015 사이트만 Y열 합산)
            if web_id == '444980831': # 015 사이트 ID
                res_mau_web = client.run_report(RunReportRequest(
                    property=prop_web,
                    date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
                    metrics=[Metric(name="active28DayUsers")]
                ))
                if res_mau_web.rows:
                    res['mau_015_total'] += int(res_mau_web.rows[0].metric_values[0].value)

        except Exception as e:
            print(f"⚠️ Web ID({web_id}) 조회 중 오류: {e}")

    # 2. APP 속성 조회
    try:
        prop_app = f"properties/{GA4_APP_ID}"

        # 2-1. 앱 화면 [사용자 수]
        req_screen = RunReportRequest(
            property=prop_app,
            date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
            dimensions=[Dimension(name="unifiedScreenName")],
            metrics=[Metric(name="activeUsers")] 
        )
        for row in client.run_report(req_screen).rows:
            screen = row.dimension_values[0].value
            cnt = int(row.metric_values[0].value)
            if any(k in screen for k in GA_CONFIG['app_signup_keyword']): res['app_signup'] += cnt

        # 2-2. 앱 이벤트 (로그인)
        req_app_evt = RunReportRequest(
            property=prop_app,
            date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
            dimensions=[Dimension(name="eventName")],
            metrics=[Metric(name="eventCount")]
        )
        for row in client.run_report(req_app_evt).rows:
            if row.dimension_values[0].value == GA_CONFIG['app_login_event_name']:
                res['app_login'] += int(row.metric_values[0].value)

        # 2-3. 앱 DAU (AK열용)
        req_app_dau = RunReportRequest(
            property=prop_app,
            date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
            metrics=[Metric(name="activeUsers")]
        )
        if client.run_report(req_app_dau).rows:
            res['dau_app'] = int(client.run_report(req_app_dau).rows[0].metric_values[0].value)

        # 2-4. 앱 MAU (AL열용 & Y열 합산용)
        res_mau_app = client.run_report(RunReportRequest(
            property=prop_app,
            date_ranges=[DateRange(start_date=date_fmt, end_date=date_fmt)],
            metrics=[Metric(name="active28DayUsers")]
        ))
        if res_mau_app.rows:
            mau_app_val = int(res_mau_app.rows[0].metric_values[0].value)
            res['mau_app'] = mau_app_val         # AL열 (App Only)
            res['mau_015_total'] += mau_app_val  # Y열 (015 Total)

    except Exception as e:
        print(f"⚠️ App ID 조회 중 오류: {e}")

    return res

# ==============================================================================
# [함수] 하루 처리 프로세스 (시트별 기능 분리 버전)
# ==============================================================================
def process_one_day(target_date_obj):
    q_date = target_date_obj.strftime('%Y%m%d')
    s_date = target_date_obj.strftime('%Y-%m-%d')
    
    print(f"\n{'='*50}")
    print(f"📅 날짜 처리 시작: {s_date}")
    print(f"{'='*50}")

    # 1. 데이터 수집 (모든 시트 공통)
    yh = get_yh_data(q_date)
    hdb = get_hdb_data(q_date)
    ga_data = get_ga4_detailed_data(q_date)

    if not yh or not hdb: 
        print(f"❌ {s_date} 데이터 수집 실패로 건너뜁니다.")
        return

    # 2. 변수 계산 및 준비
    a015_tot = yh['a015_sms'] + yh['a015_lms'] + yh['a015_mms']
    gen_tot = hdb['gen_sms'] + hdb['gen_lms'] 
    
    # 날짜 문자열 (보고양식용)
    prev_date_obj = target_date_obj - timedelta(days=1)
    str_curr = f"{target_date_obj.day:02d}일"
    str_prev = f"{prev_date_obj.day:02d}일"
    
    # 날짜 문자열 (데이터입력용)
    ym = f"{s_date[2:4]}년 {s_date[5:7]}월"; dd = f"{s_date[8:10]}일"

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SHEET_ID)

        # ======================================================================
        # [Sheet 1] 데이터입력시트 -> "처음에 만든 기능 (데이터 입력)"
        # ======================================================================
        print(f"🔹 [{TAB_NAME_INPUT}] 데이터 입력 시작...")
        sheet_input = spreadsheet.worksheet(TAB_NAME_INPUT)
        
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
                    "startColumnIndex": 0, "endColumnIndex": 46 
                },
                "destination": { 
                    "sheetId": sheet_input.id, 
                    "startRowIndex": input_target_row-1, "endRowIndex": input_target_row, 
                    "startColumnIndex": 0, "endColumnIndex": 46 
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

        sheet_input.update(f"A{input_target_row}", [row], value_input_option='USER_ENTERED')
        print(f"   ✅ 입력 완료")


        # ======================================================================
        # [Sheet 2] 그래프_데이터 -> B열은 날짜 직접 입력, 나머지는 그대로 복사
        # ======================================================================
        print(f"🔹 [{TAB_NAME_GRAPH}] 그래프 행 추가 시작...")
        sheet_graph = spreadsheet.worksheet(TAB_NAME_GRAPH)

        graph_rows = sheet_graph.get_all_values()

        DATE_COL = 2        # B열
        START_COPY_COL = 3  # C열부터 복사
        END_COPY_COL = 46   # 실제 마지막 컬럼 번호에 맞춰 수정

        # 1) 마지막 값이 있는 행 찾기
        last_value_row = 0
        date_idx = DATE_COL - 1
        for i, r in enumerate(graph_rows, start=1):
            cell = r[date_idx] if len(r) > date_idx else ""
            if str(cell).strip():
                last_value_row = i

        # 2) 새 행 위치
        source_row = last_value_row
        graph_target_row = last_value_row + 1

        # 3) 행 추가
        sheet_graph.insert_row([], index=graph_target_row)

        # 4) B열 날짜 직접 입력 (자동 증가 아님, 코드에서 계산)
        sheet_graph.update_cell(
            graph_target_row,
            DATE_COL,
            target_date_obj.strftime("%Y-%m-%d")
        )

        # 5) C열 ~ 끝까지 복사 (마우스로 꼭지점 끌어내린 효과)
        sheet_graph.spreadsheet.batch_update({
            "requests": [{
                "copyPaste": {
                    "source": {
                        "sheetId": sheet_graph.id,
                        "startRowIndex": source_row - 1,
                        "endRowIndex": source_row,
                        "startColumnIndex": START_COPY_COL - 1,
                        "endColumnIndex": END_COPY_COL
                    },
                    "destination": {
                        "sheetId": sheet_graph.id,
                        "startRowIndex": graph_target_row - 1,
                        "endRowIndex": graph_target_row,
                        "startColumnIndex": START_COPY_COL - 1,
                        "endColumnIndex": END_COPY_COL
                    },
                    "pasteType": "PASTE_NORMAL"
                }
            }]
        })

        print(f"   ✅ 그래프_데이터 행 추가 완료 → row={graph_target_row}, 날짜={target_date_obj.strftime('%Y-%m-%d')}")

        # ======================================================================
        # [Sheet 3] 평일보고양식 -> "일 수 변경 기능 (D7:I7)"
        # ======================================================================
        print(f"🔹 [{TAB_NAME_REPORT}] 헤더 날짜 변경 시작...")
        sheet_report = spreadsheet.worksheet(TAB_NAME_REPORT)
        
        # D(전), E(현), F(전), G(현), H(전), I(현)
        header_vals = [[str_prev, str_curr, str_prev, str_curr, str_prev, str_curr]]
        sheet_report.update("D7:I7", header_vals, value_input_option='USER_ENTERED')
        # E30(전), F30(현)
        sheet_report.update("E30:F30", [[str_prev, str_curr]], value_input_option='USER_ENTERED')
        # D26(전), E26(현)
        sheet_report.update("D26:E26", [[str_prev, str_curr]], value_input_option='USER_ENTERED')
        print(f"   ✅ 변경 완료 ({str_prev} vs {str_curr})")

        print(f"🔹 [{TAB_NAME_REPORT}] 1:1 발송 수식 갱신 시작...")
        input_tab = TAB_NAME_INPUT
        new_formulas = [
            [f"='{input_tab}'!AM{input_target_row}"],
            [f"='{input_tab}'!AN{input_target_row}"],
            [f"='{input_tab}'!AO{input_target_row}"],
        ]
        sheet_report.update("L8:L10", new_formulas, value_input_option="USER_ENTERED")
        sheet_report.update("AE8:AE10", new_formulas, value_input_option="USER_ENTERED")

        print(f"   ✅ 수식 갱신 완료")

    except Exception as e:
        print(f"❌ {s_date} 처리 중 오류:"); traceback.print_exc()
        
# ==============================================================================
# [Main]
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description='일일 통계 자동화 봇')
    parser.add_argument('--date', type=str, help='특정 날짜 실행 (YYYYMMDD)')
    parser.add_argument('--start', type=str, help='시작 날짜 (YYYYMMDD)')
    parser.add_argument('--end', type=str, help='종료 날짜 (YYYYMMDD)')
    
    args = parser.parse_args()

    require_db_settings()
    require_google_settings()
    
    start_date = None
    end_date = None

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

    curr = start_date
    while curr <= end_date:
        process_one_day(curr)
        curr += timedelta(days=1)
        
    print("\n🎉 모든 작업이 종료되었습니다.")

if __name__ == "__main__":
    main()
