"""DB/GA4가 필요 없는 시트 갱신만 실제 스프레드시트 상대로 점검.

`데이터입력시트`는 읽기만 한다.

    python test/test_sheets_live.py                  # dry-run (기본, 아무것도 안 씀)
    python test/test_sheets_live.py --date 20260726  # 날짜 지정
    python test/test_sheets_live.py --write          # 실제로 시트에 쓴다
    python test/test_sheets_live.py --only report    # 모바일보고양식만

dry-run은 읽기만 실제로 하고 쓰기는 전부 출력으로 대체한다.
"""
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import main
from settings import JSON_KEY_FILE, SHEET_ID, require_google_settings


# ==============================================================================
# dry-run 래퍼: 읽기는 통과, 쓰기는 출력만
# ==============================================================================
WRITE_METHODS = {
    "batch_update", "update", "update_cell", "update_cells",
    "insert_row", "insert_rows", "append_row", "clear", "delete_rows",
}


def _describe(method, args, kwargs):
    """쓰기 호출 인자를 사람이 읽을 수 있게 몇 줄로."""
    if method == "batch_update" and args and isinstance(args[0], list):
        for item in args[0]:
            yield f"{item['range']:<10} = {item['values']}"
    elif method == "batch_update":
        yield f"(spreadsheet 요청) {args[0] if args else ''}"
    elif method == "update" and len(args) >= 2:
        yield f"{args[0]:<10} = {args[1]}"
    elif method == "update_cell" and len(args) >= 3:
        yield f"r{args[0]}c{args[1]:<6} = {args[2]!r}"
    elif method in ("insert_row", "insert_rows"):
        index = kwargs.get("index", args[1] if len(args) > 1 else "?")
        yield f"index={index} (빈 행 삽입)"
    else:
        yield f"args={args} kwargs={kwargs}"


class DryRun:
    """실제 gspread 객체를 감싸서 읽기는 위임하고 쓰기는 삼킨다."""

    def __init__(self, real, label):
        self._real = real
        self._label = label

    def __getattr__(self, name):
        if name in WRITE_METHODS:
            def skipped(*args, **kwargs):
                print(f"   [dry-run] {self._label}.{name}()")
                for line in _describe(name, args, kwargs):
                    print(f"             {line}")
            return skipped
        if name == "worksheet":
            return lambda title: DryRun(self._real.worksheet(title), title)
        if name == "spreadsheet":
            return DryRun(self._real.spreadsheet, self._label)
        return getattr(self._real, name)


# ==============================================================================
def open_spreadsheet():
    require_google_settings()
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEY_FILE, scope)
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def main_cli():
    parser = argparse.ArgumentParser(description="모바일 시트만 점검")
    parser.add_argument("--date", help="당일 날짜 (YYYYMMDD). 기본: 어제")
    parser.add_argument("--write", action="store_true", help="실제로 시트에 쓴다")
    parser.add_argument("--only", choices=["graph", "report", "weekday", "dates"],
                        help="하나만 실행")
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y%m%d").date()
    else:
        target = date.today() - timedelta(days=1)

    real = open_spreadsheet()
    sheet = real if args.write else DryRun(real, "spreadsheet")

    mode = "✍️  WRITE" if args.write else "👀 DRY-RUN (아무것도 안 씀)"
    print(f"{mode} / 당일={target} / 보고일={target + timedelta(days=1)}\n")

    if args.only in (None, "dates"):
        print(f"🔹 [{main.TAB_NAME_INPUT}] C열 날짜 표기 정리")
        main.normalize_input_dates(sheet.worksheet(main.TAB_NAME_INPUT), target)
        print()

    if args.only in (None, "graph"):
        main.add_graph_row(
            sheet, main.TAB_NAME_MOBILE_GRAPH, target, main.MOBILE_GRAPH_END_COLUMN
        )
        print()

    if args.only in (None, "weekday"):
        # 평일보고양식은 날짜 헤더만 쓴다. 데이터는 시트의 INDEX/XMATCH가 따라옴.
        print(f"🔹 [{main.TAB_NAME_REPORT}] 날짜 헤더")
        main.update_report_dates(sheet.worksheet(main.TAB_NAME_REPORT), target)
        print()

    if args.only in (None, "report"):
        # 데이터입력시트는 읽기만 하므로 dry-run에서도 실제 객체를 쓴다.
        sheet_input = real.worksheet(main.TAB_NAME_INPUT)
        max_days = max(b["days"] for b in main.MOBILE_BLOCKS)
        wanted = [target - timedelta(days=i) for i in range(max_days)]
        input_rows = main.find_input_rows(sheet_input, wanted)

        print("📄 데이터입력시트 행 조회:")
        for d in sorted(input_rows):
            row = input_rows[d]
            print(f"   {d} → {row if row else '❌ 없음'}")
        print()

        main.update_mobile_report(sheet, target, input_rows)

    print("\n🎉 완료" + ("" if args.write else " (dry-run — 시트 변경 없음)"))


if __name__ == "__main__":
    main_cli()
