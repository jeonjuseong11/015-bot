# 015-bot (로컬 실행용)

Google Sheets/DB/GA4 데이터를 모아서 일일 보고용 시트를 자동 갱신하는 스크립트입니다.

## 요구사항

- Python 3.x
- Google 서비스 계정 키(JSON)
- DB 접속 권한 (YH-DB, HDB)

## 설치

```bash
pip install -r requirements.txt
```

## 설정 (필수)

1. `local_settings.example.py` → `local_settings.py`로 복사
2. `local_settings.py`에 아래 값을 채움

- `JSON_KEY_FILE`: 구글 서비스 계정 JSON 키 파일 경로
- `SHEET_ID`, `TAB_NAME`
- `YH_DB_CONFIG`, `HDB_CONFIG`

환경변수로도 설정할 수 있습니다 (우선순위: 환경변수 → `local_settings.py`).

- Google: `GOOGLE_JSON_KEY_FILE`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_TAB`
- YH DB: `YH_DB_HOST`, `YH_DB_PORT`, `YH_DB_USER`, `YH_DB_PASSWORD`, `YH_DB_DATABASE`
- HDB: `HDB_HOST`, `HDB_PORT`, `HDB_USER`, `HDB_PASSWORD`, `HDB_DATABASE`(선택)

### Google Sheets 권한

- 서비스 계정 이메일에 스프레드시트를 공유(편집 권한)해야 합니다.
- `JSON_KEY_FILE`은 로컬에만 두고 커밋하지 마세요.

## 실행

기본 동작: 어제 날짜 기준으로 실행합니다.

```bash
python main.py
```

특정 날짜 실행:

```bash
python main.py --date 20240131
```

기간 실행:

```bash
python main.py --start 20240101 --end 20240131
```

## 시트 구조(기본값)

기본 시트 이름은 다음과 같습니다 (필요 시 `main.py` 상단에서 변경).

- 데이터 입력: `데이터입력시트`
- 그래프 데이터: `그래프_데이터` (B열 날짜, C~AT열 복사)
- 모바일 그래프 데이터: `모바일_그래프_데이터` (B열 날짜, C~K열 복사)
- 보고 양식: `평일보고양식`
- 모바일 보고 양식: `모바일보고양식` (좌 D~E 2일, 우 M~O 3일 / 모두 당일까지)

## 수동 점검 스크립트

```bash
python test/test_db.py       # DB 연결/기본 쿼리 점검
python test/test_queries.py  # 쿼리 결과 샘플 확인
python test/test_sheet.py    # Google Sheets 연결 확인
python test/ga4_test.py      # GA4 API 연결 확인
python test/test_graph_row.py      # 그래프 행 추가 로직 점검 (외부 접속 불필요)
python test/test_mobile_report.py  # 모바일보고양식 셀 계산 점검 (외부 접속 불필요)
python test/test_mobile_live.py    # 모바일 시트 2개만 실제 시트 상대로 점검 (기본 dry-run)
```
