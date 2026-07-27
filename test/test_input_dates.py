"""데이터입력시트 C열 날짜 표기 로직 점검 (외부 접속 불필요).

    python test/test_input_dates.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stubs

main = _stubs.install()


class FakeInput:
    id = 42

    def __init__(self, months, days):
        self.cols = {2: months, 3: days}
        self.value_updates = []
        self.format_rows = []
        self.spreadsheet = self

    def col_values(self, n):
        return self.cols[n]

    def batch_update(self, data, value_input_option=None):
        if isinstance(data, list):                       # worksheet.batch_update
            self.value_updates.extend(data)
        else:                                            # spreadsheet.batch_update
            for req in data["requests"]:
                self.format_rows.append(req["repeatCell"]["range"]["startRowIndex"] + 1)


def build(target, span):
    """target 기준 span일치를 'NN일' 텍스트로 채운 가짜 시트."""
    dates = [target - main.timedelta(days=i) for i in range(span - 1, -1, -1)]
    months = ["년월"] + [f"{d:%y}년 {d:%m}월" for d in dates]
    days = ["일"] + [f"{d.day:02d}일" for d in dates]
    return FakeInput(months, days), dates


def test_converts_only_outside_window():
    target = date(2026, 8, 2)
    sheet, dates = build(target, 20)          # 7/14 ~ 8/2
    main.normalize_input_dates(sheet, target, window=7)

    converted = {u["range"]: u["values"][0][0] for u in sheet.value_updates}
    cutoff = date(2026, 7, 27)                # target - 6

    # 윈도우 밖(7/14~7/26) 13칸만 변환
    assert len(converted) == 13, converted
    assert converted["C2"] == "2026-07-14", converted["C2"]
    assert converted["C14"] == "2026-07-26", converted["C14"]
    # 윈도우 안(7/27~8/2)은 손대지 않음
    for row in range(15, 22):
        assert f"C{row}" not in converted, f"C{row} 건드리면 안 됨"
    # 서식도 같은 행들에만
    assert sorted(sheet.format_rows) == sorted(int(r[1:]) for r in converted), sheet.format_rows
    assert all(main.date.fromisoformat(v) < cutoff for v in converted.values())


def test_noop_when_already_clean():
    target = date(2026, 8, 2)
    sheet, _ = build(target, 5)               # 전부 윈도우 안
    main.normalize_input_dates(sheet, target, window=7)
    assert sheet.value_updates == []
    assert sheet.format_rows == []


def test_month_boundary_window_has_no_duplicate_days():
    # 7/27~8/2 -> '27일','28일','29일','30일','31일','01일','02일' 전부 서로 다름
    target = date(2026, 8, 2)
    labels = {f"{(target - main.timedelta(days=i)).day:02d}일" for i in range(7)}
    assert len(labels) == 7, labels


def test_parses_both_formats():
    # 변환된 과거 행(YYYY-MM-DD)과 최근 행('NN일')이 섞여 있어도 다 찾아야 한다
    sheet = FakeInput(
        ["년월", "26년 07월", "26년 07월", "26년 08월"],
        ["일", "2026-07-30", "31일", "01일"],
    )
    got = main.find_input_rows(sheet, [date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 1)])
    assert got == {date(2026, 7, 30): 2, date(2026, 7, 31): 3, date(2026, 8, 1): 4}, got


if __name__ == "__main__":
    test_converts_only_outside_window()
    test_noop_when_already_clean()
    test_month_boundary_window_has_no_duplicate_days()
    test_parses_both_formats()
    print("✅ C열 날짜 표기 점검 통과")
