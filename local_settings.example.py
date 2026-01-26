# 이 파일을 `local_settings.py`로 복사한 뒤 값들을 채워주세요.
# - `local_settings.py`는 비밀정보(계정/비번/키) 포함 파일이므로 커밋/공유 금지
# - 아래 값들은 예시이며, 실제 환경에 맞게 변경해야 합니다.

# ==============================================================================
# Google Sheets
# ==============================================================================

# 서비스 계정 JSON 키 파일 경로 (예: "service_account.json" 또는 절대경로)
JSON_KEY_FILE = "service_account.json"

# 스프레드시트 ID (URL의 /d/<ID>/ 부분)
SHEET_ID = "YOUR_SHEET_ID"

# 탭(워크시트) 이름
TAB_NAME = "데이터입력시트"

# ==============================================================================
# Databases
# ==============================================================================

# 1. YH-DB 접속 정보 (PostgreSQL)
YH_DB_CONFIG = {
    "host": "YOUR_PG_HOST",      # 예: "127.0.0.1" 또는 사내 DB IP
    "port": 5432,                # 포트 번호 (보통 5432)
    "user": "YOUR_PG_USER",
    "password": "YOUR_PG_PASS",
    "database": "YOUR_PG_DB",    # 접속할 DB 이름 (예: postgres, yh 등)
}

# 2. HDB 접속 정보 (MariaDB)
HDB_CONFIG = {
    "host": "YOUR_MYSQL_HOST",   # 예: "127.0.0.1" 또는 사내 DB IP
    "port": 3306,                # 포트 번호 (보통 3306)
    "user": "YOUR_MYSQL_USER",
    "password": "YOUR_MYSQL_PASS",
    # "database": "YOUR_MYSQL_DB",  # 필요 시 사용
    "charset": "utf8",
}
