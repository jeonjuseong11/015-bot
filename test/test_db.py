import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2  # YH-DB용
import pymysql   # HDB용

from settings import HDB_CONFIG, YH_DB_CONFIG, require_db_settings

# ==============================================================================
# [테스트 로직]
# ==============================================================================

def check_yh_db_connection():
    print("\n🐘 [1] YH-DB (PostgreSQL) 접속 시도 중...")
    try:
        # 연결 시도
        conn = psycopg2.connect(**YH_DB_CONFIG)
        cur = conn.cursor()
        
        # 버전 확인 쿼리 실행
        cur.execute("SELECT version();")
        db_version = cur.fetchone()
        
        print(f"✅ 접속 성공! DB 버전: {db_version[0][:30]}...")
        conn.close()
    except Exception as e:
        print(f"❌ 접속 실패! 이유:\n{e}")

def check_hdb_connection():
    print("\n🐬 [2] HDB (MariaDB) 접속 시도 중...")
    try:
        # 연결 시도
        conn = pymysql.connect(**HDB_CONFIG)
        cur = conn.cursor()
        
        # 버전 확인 쿼리 실행
        cur.execute("SELECT VERSION();")
        db_version = cur.fetchone()
        
        print(f"✅ 접속 성공! DB 버전: {db_version[0]}")
        conn.close()
    except Exception as e:
        print(f"❌ 접속 실패! 이유:\n{e}")

if __name__ == "__main__":
    print("----- 🔌 데이터베이스 연결 테스트 시작 -----")
    print("※ FortiClient VPN이 켜져 있어야 합니다!")

    require_db_settings()
    
    check_yh_db_connection()
    check_hdb_connection()
    
    print("\n----- 테스트 종료 -----")
