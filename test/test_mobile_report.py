"""모바일보고양식 셀 계산 점검. DB/구글 접속 없이 가짜 시트로 확인.

    python test/test_mobile_report.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stubs

main = _stubs.install()


class FakeSheet:
    def __init__(self, cols=None):
        self.cols = cols or {}
        self.updates = []

    def col_values(self, col):
        return self.cols.get(col, [])

    def batch_update(self, data, value_input_option=None):
        self.updates.extend(data)

    def worksheet(self, name):
        return self


def run_report(target, rows_by_date):
    sheet = FakeSheet()
    main.update_mobile_report(sheet, target, rows_by_date)
    return {u["range"]: u["values"] for u in sheet.updates}


def test_two_and_three_day_blocks():
    # 보고일 2026-07-27(월) → 당일 26일
    target = date(2026, 7, 26)
    rows = {
        date(2026, 7, 24): 100,
        date(2026, 7, 25): 101,
        date(2026, 7, 26): 102,
    }
    up = run_report(target, rows)

    # 보고일 C4/L4는 시트의 =TODAY() 수식이므로 건드리면 안 된다
    assert "C4" not in up and "L4" not in up, up.keys()

    # 날짜 헤더: 왼쪽 2일(D~E), 오른쪽 3일(M~O)
    assert up["D7:E7"] == [["25일", "26일"]], up["D7:E7"]
    assert up["M7:O7"] == [["24일", "25일", "26일"]], up["M7:O7"]

    # 데이터 8~11행 = 회원가입(Q), 앱로그인(T), 신규(D), 누적(E 괄호 안)
    t = main.TAB_NAME_INPUT
    assert up["D8:E11"] == [
        [f'=IFERROR(\'{t}\'!Q101, "")', f'=IFERROR(\'{t}\'!Q102, "")'],
        [f'=IFERROR(\'{t}\'!T101, "")', f'=IFERROR(\'{t}\'!T102, "")'],
        [f'=IFERROR(\'{t}\'!D101, "")', f'=IFERROR(\'{t}\'!D102, "")'],
        [f'=REGEXEXTRACT(IFERROR(\'{t}\'!E101, ""), "\\((.+)\\)")',
         f'=REGEXEXTRACT(IFERROR(\'{t}\'!E102, ""), "\\((.+)\\)")'],
    ], up["D8:E11"]
    assert up["M8:O11"][0] == [
        f'=IFERROR(\'{t}\'!Q100, "")', f'=IFERROR(\'{t}\'!Q101, "")', f'=IFERROR(\'{t}\'!Q102, "")'
    ], up["M8:O11"][0]

    # MAU/DAU는 두 블록 모두 당일 1일치, 2칸
    assert up["D15:E15"] == [["MAU(07/26)", "DAU(07/26)"]], up["D15:E15"]
    assert up["M15:N15"] == [["MAU(07/26)", "DAU(07/26)"]], up["M15:N15"]
    assert up["D16:E16"] == [[f"='{t}'!AL102", f"='{t}'!AK102"]], up["D16:E16"]
    assert up["M16:N16"] == [[f"='{t}'!AL102", f"='{t}'!AK102"]], up["M16:N16"]


def test_missing_day_becomes_blank():
    target = date(2026, 7, 26)
    rows = {date(2026, 7, 24): None, date(2026, 7, 25): 101, date(2026, 7, 26): 102}
    up = run_report(target, rows)
    # 24일 열(M)만 빈칸
    assert up["M8:O11"][0][0] == "", up["M8:O11"][0]
    assert up["M8:O11"][0][1] != ""


def test_find_input_rows_parses_sheet_text():
    sheet = FakeSheet({
        2: ["년월", "26년 07월", "26년 07월", "26년 08월"],
        3: ["일", "25일", "26일", "01일"],
    })
    got = main.find_input_rows(sheet, [date(2026, 7, 26), date(2026, 8, 1), date(2026, 7, 24)])
    assert got[date(2026, 7, 26)] == 3, got
    assert got[date(2026, 8, 1)] == 4, got
    assert got[date(2026, 7, 24)] is None, got


if __name__ == "__main__":
    test_two_and_three_day_blocks()
    test_missing_day_becomes_blank()
    test_find_input_rows_parses_sheet_text()
    print("✅ 모바일보고양식 점검 통과")
