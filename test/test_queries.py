import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # YH-DB (PostgreSQL)
import pymysql   # HDB (MariaDB)
from datetime import date, timedelta

from settings import HDB_CONFIG, YH_DB_CONFIG, require_db_settings
# ==============================================================================
# [테스트 로직]
# ==============================================================================

def get_target_date():
    """어제 날짜 구하기"""
    yesterday = date.today() - timedelta(days=1)
    return yesterday.strftime('%Y%m%d'), yesterday.strftime('%Y-%m-%d')

def check_yh_db(query_date):
    print(f"\n🐘 [YH-DB] PostgreSQL 쿼리 테스트 (기준일: {query_date})")
    print("-" * 60)
    
    try:
        conn = psycopg2.connect(**YH_DB_CONFIG)
        cur = conn.cursor()

        # 1. 신규 가입
        sql = "SELECT COUNT(*) FROM arreo.say015_subscription WHERE subs_start_date LIKE %s || '%%'"
        cur.execute(sql, (query_date,))
        print(f"1. 신규 가입자 수 : {cur.fetchone()[0]} 명")

        # 2. 충전액
        sql = """
            select sum(h.pg) from arreo.mbr_usr_h h
            where h.wrt_dttm LIKE %s || '%%' and h.acct_gb = '1' 
            and h.sms_gb in ('W', 'M') and h.site_id = 'arreo'
        """
        cur.execute(sql, (query_date,))
        res = cur.fetchone()[0]
        print(f"2. 충전액        : {res if res else 0} 원")

        # 날짜 범위 계산 (어제~오늘)
        y_date = date(int(query_date[:4]), int(query_date[4:6]), int(query_date[6:]))
        today_str = (y_date + timedelta(days=1)).strftime('%Y%m%d')

        # 3. 015Mode (집계)
        sql = f"""
            SELECT
                SUM(CASE WHEN t.cmp_usr_id = '00000' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'LMS01' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'MMS01' THEN t.cnt ELSE 0 END)
            FROM (
                SELECT x.cmp_usr_id, COUNT(*) AS cnt FROM arreo.smt_send x
                WHERE x.snd_dttm >= '{query_date}' AND x.snd_dttm < '{today_str}'
                AND x.sms_st != 'C' AND x.site_id = 'arreo'
                GROUP BY x.cmp_usr_id
            ) t
        """
        cur.execute(sql)
        r = cur.fetchone()
        print(f"3. 015Mode 발송  : SMS({r[0]}), LMS({r[1]}), MMS({r[2]})")

        # 4. ARREO & A015
        sql = f"""
            SELECT
                SUM(CASE WHEN t.cmp_usr_id = '00000' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'LMS01' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'MMS01' and t.agt_node_id = 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = '00000' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'LMS01' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END),
                SUM(CASE WHEN t.cmp_usr_id = 'MMS01' and t.agt_node_id != 'NETIAN_AGT3.6.7' THEN t.cnt ELSE 0 END)
            FROM (
                SELECT x.cmp_usr_id, x.agt_node_id , COUNT(*) AS cnt FROM arreo.xms_send x
                WHERE x.snd_dttm >= '{query_date}' AND x.snd_dttm < '{today_str}'
                AND x.sms_st != 'C' AND x.callback NOT LIKE '015%%' AND x.rslt_val = '-100' AND x.site_id = 'arreo'
                GROUP BY x.cmp_usr_id, x.agt_node_id
            ) t
        """
        cur.execute(sql)
        r = cur.fetchone()
        print(f"4. ARREO 발송    : SMS({r[0]}), LMS({r[1]}), MMS({r[2]})")
        print(f"5. A015 발송     : SMS({r[3]}), LMS({r[4]}), MMS({r[5]})")

        conn.close()

    except Exception as e:
        print(f"❌ YH-DB 쿼리 에러: {e}")

def check_hdb(query_date):
    print(f"\n🐬 [HDB] MariaDB 쿼리 테스트 (기준일: {query_date})")
    print("-" * 60)
    
    try:
        conn = pymysql.connect(**HDB_CONFIG)
        cur = conn.cursor()

        # 1. 010->015 일반
        sql = f"""
            select count(*) from (
               select sender, receiver, SND_DT from mosms.mo_sms_h where SND_DT = '{query_date}'
               union all 
               select sender, receiver, SND_DT from momms.mo_mms_h where SND_DT = '{query_date}'
            ) as inbound 
            where inbound.sender not in ('01585040006','01228423734')
            and SENDER not like ('015%%')
            and inbound.receiver LIKE '015%%' AND (LENGTH(inbound.receiver) = 10 OR LENGTH(inbound.receiver) = 11)
        """
        cur.execute(sql)
        print(f"1. 일반(Inbound) : {cur.fetchone()[0]} 건")

        # 2. 고객센터
        sql = f"""
            select count(*) from (
               select sender, receiver, SND_DT from mosms.mo_sms_h where SND_DT = '{query_date}'
               union all 
               select sender, receiver, SND_DT from momms.mo_mms_h where SND_DT = '{query_date}'
            ) as cs 
            where receiver = '01585040006' AND SND_DT = '{query_date}'
        """
        cur.execute(sql)
        print(f"2. 고객센터      : {cur.fetchone()[0]} 건")

        # 3. 합계
        sql = f"""
            select count(*) from (
               select sender, receiver, SND_DT from mosms.mo_sms_h where SND_DT = '{query_date}'
               union all 
               select sender, receiver, SND_DT from momms.mo_mms_h where SND_DT = '{query_date}'
            ) as total 
            where total.sender not in ('01585040006','01228423734')
            and SENDER not like ('015%%') and total.receiver LIKE '015%%'
        """
        cur.execute(sql)
        print(f"3. 전체 합계     : {cur.fetchone()[0]} 건")

        # 4. 라우팅
        sql = f"""
            select 'SMS', COUNT(*) from SMSMT.SMS_H force index (IDX_SMS_H6)
            where SND_DT ='{query_date}' and GROUP_CODE like 'B00%%' and RESULT_CODE = '-100' and SETTLE_CODE like 'SMT-%%'
            union all 
            select 'MMS', COUNT(*) from MMSMT.MMS_H force index (IDX_MMS_H6)
            where SND_DT ='{query_date}' and GROUP_CODE like 'B00%%' and RESULT_CODE = '-100' and SETTLE_CODE like 'SMT-%%'
        """
        cur.execute(sql)
        rows = cur.fetchall()
        print(f"4. 라우팅 결과   : {rows}")

        # 5. 일반 발송
        # ★ 주의: mbr_cmp_m 테이블의 정확한 DB명(예: smsmt.mbr_cmp_m)을 확인 필요
        sql = f"""
            select 'SMS',count(*) from smsmt.sms_h a , smsmt.mbr_cmp_m b
            where a.GROUP_CODE = b.grp_cd and a.BILL_CODE = b.bill_cd and b.ad_flag ='N' and SND_DT ='{query_date}' and RESULT_CODE ='-100'
            union all 
            select 'LMS',count(*) from mmsmt.mms_h a , smsmt.mbr_cmp_m b
            where a.GROUP_CODE = b.grp_cd and a.BILL_CODE = b.bill_cd and b.ad_flag ='N' and SND_DT ='{query_date}' and RESULT_CODE ='-100'
        """
        cur.execute(sql)
        rows = cur.fetchall()
        print(f"5. 일반 발송     : {rows}")

        conn.close()

    except Exception as e:
        print(f"❌ HDB 쿼리 에러: {e}")

if __name__ == "__main__":
    require_db_settings()
    q_date, _ = get_target_date()
    check_yh_db(q_date)
    check_hdb(q_date)
