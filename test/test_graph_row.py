"""add_graph_row() 자체 점검. DB/구글 접속 없이 가짜 시트로 확인.

    python test/test_graph_row.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stubs

main = _stubs.install()


class FakeSheet:
    id = 777

    def __init__(self, rows):
        self._rows = rows
        self.inserted_at = None
        self.updated_cell = None
        self.copy_request = None
        self.spreadsheet = self

    def get_all_values(self):
        return self._rows

    def get(self, a1, value_render_option=None):
        # B{n} 형태만 쓴다. 시트에는 'YYYY-MM-DD' 문자열이 들어있다고 가정.
        row = int(a1[1:])
        return [[self._rows[row - 1][1]]]

    def insert_row(self, values, index, inherit_from_before=False):
        self.inserted_at = index
        self.inherited = inherit_from_before

    def update_cell(self, row, col, value):
        self.updated_cell = (row, col, value)

    def batch_update(self, body):
        self.copy_request = body["requests"][0]["copyPaste"]

    # spreadsheet.worksheet(name) 대역
    def worksheet(self, name):
        return self


def test_appends_after_last_filled_b_column():
    # 1행 헤더, 2~3행 데이터(B열 채움), 4행은 C열만 있고 B는 빈칸
    sheet = FakeSheet([
        ["", "날짜", "값"],
        ["", "2024-01-01", "10"],
        ["", "2024-01-02", "20"],
        ["", "", ""],
    ])
    main.add_graph_row(sheet, "모바일_그래프_데이터", date(2024, 1, 3), 11)

    assert sheet.inserted_at == 4, sheet.inserted_at
    assert sheet.inherited is True, "B열 날짜 서식은 윗행에서 물려받아야 한다"
    assert sheet.updated_cell == (4, 2, "2024-01-03"), sheet.updated_cell

    cp = sheet.copy_request
    # 원본 = 3행, 대상 = 4행 (0-based, end는 배타적)
    assert (cp["source"]["startRowIndex"], cp["source"]["endRowIndex"]) == (2, 3), cp["source"]
    assert (cp["destination"]["startRowIndex"], cp["destination"]["endRowIndex"]) == (3, 4), cp["destination"]
    # C열(0-based 2) ~ K열(0-based 10, end 11)
    assert (cp["source"]["startColumnIndex"], cp["source"]["endColumnIndex"]) == (2, 11), cp["source"]


def test_skips_when_date_already_present():
    # 마지막 행이 이미 당일이면 재실행이므로 아무것도 안 해야 한다
    sheet = FakeSheet([
        ["", "날짜", "값"],
        ["", "2024-01-02", "20"],
    ])
    main.add_graph_row(sheet, "모바일_그래프_데이터", date(2024, 1, 2), 11)

    assert sheet.inserted_at is None
    assert sheet.copy_request is None


def test_skips_when_no_reference_row():
    sheet = FakeSheet([["", "", ""]])
    main.add_graph_row(sheet, "모바일_그래프_데이터", date(2024, 1, 3), 11)

    assert sheet.inserted_at is None
    assert sheet.copy_request is None


if __name__ == "__main__":
    test_appends_after_last_filled_b_column()
    test_skips_when_date_already_present()
    test_skips_when_no_reference_row()
    print("✅ add_graph_row 점검 통과")
