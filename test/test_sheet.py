import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import traceback
from datetime import datetime

from settings import JSON_KEY_FILE, SHEET_ID, TAB_NAME, require_google_settings

def append_today_row():
    print("----- 📅 날짜 입력 테스트 시작 -----")

    require_google_settings()
    
    # [1] JSON 파일 확인
    if not os.path.exists(JSON_KEY_FILE):
        print(f"❌ 오류: '{JSON_KEY_FILE}' 파일을 찾을 수 없습니다!")
        return

    try:
        print("🔌 구글 서버 접속 중...")
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
        client = gspread.authorize(creds)
        
        print(f"📂 '{TAB_NAME}' 시트 여는 중...")
        sheet = client.open_by_key(SHEET_ID).worksheet(TAB_NAME)
        
        # [2] 오늘 날짜 구하기 (YYYY-MM-DD 형식)
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        print(f"🖊️ 맨 아래 줄에 날짜('{today_str}') 추가 시도...")
        
        # [3] 데이터 추가 (A열: 날짜, B열: 테스트 메시지)
        # 실제 로봇은 append_row를 쓰기 때문에 똑같이 테스트합니다.
        test_data = [today_str, "테스트 연결 성공 ✅"]
        
        sheet.append_row(test_data)
        
        print("✅ 성공! 시트 맨 아래에 오늘 날짜가 들어갔는지 확인해보세요.")

    except Exception:
        print("\n❌ 상세 에러 로그:")
        print("---------------------------------------------------")
        traceback.print_exc()
        print("---------------------------------------------------")

if __name__ == "__main__":
    append_today_row()
