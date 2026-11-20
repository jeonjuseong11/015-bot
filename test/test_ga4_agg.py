"""get_ga4_detailed_data() / refresh_ga_columns() 자체 점검. GA4 접속 없이 가짜 응답으로 확인.

    python test/test_ga4_agg.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _stubs

main = _stubs.install()

ARREO, A015, APP = "properties/445891939", "properties/444980831", "properties/461784274"


class FakeClient:
    @classmethod
    def from_service_account_json(cls, _path):
        return cls()


def install_fake(table, fail_props=(), reject_source_filter_for=()):
    """table[(prop, 차원들, metric, 필터차원 또는 None, 첫 키워드 또는 None)] = rows

    reject_source_filter_for: 출처 제외 필터와 함께 쓰면 GA4가 거부하는 지표 이름들.
    """
    calls = []
    main.GA_FILTER_FALLBACKS.clear()

    def fake_rows(client, prop, date_fmt, dimensions, metric, contains=None, exclude_sources=None):
        calls.append((prop, tuple(dimensions), metric, contains, exclude_sources))
        if prop in fail_props:
            raise RuntimeError("boom")
        if exclude_sources and metric in reject_source_filter_for:
            raise RuntimeError("incompatible")
        key = (prop, tuple(dimensions), metric,
               contains[0] if contains else None, contains[1][0] if contains else None)
        return table.get(key, [])

    main.BetaAnalyticsDataClient = FakeClient
    main._ga_rows = fake_rows
    return calls


def host_rows(**hosts):
    return [([h.replace("_", ".")], n) for h, n in hosts.items()]


def base_table():
    H, U = ("hostName",), "activeUsers"
    return {
        # 방문자: a015.com 이 두 속성에 모두 잡힘(중복 전송) → 큰 값 하나만
        (A015, H, U, None, None): host_rows(a015_com=100, new_arreo_com=30, localhost=999),
        (ARREO, H, U, None, None): host_rows(a015_com=80, www_arreo_com=50),
        # 로그인 페이지 방문자: GA4가 호스트별 고유값으로 준다
        (A015, H, U, "pagePath", "signin"): host_rows(a015_com=40, new_arreo_com=5),
        (ARREO, H, U, "pagePath", "signin"): host_rows(www_arreo_com=7),
        (A015, H, U, "pagePath", "join"): host_rows(a015_com=3),
        (A015, H, "active28DayUsers", None, None): host_rows(a015_com=900, new_arreo_com=200, www_arreo_com=1),
        (ARREO, H, "active28DayUsers", None, None): host_rows(www_arreo_com=4000, stage_arreo_com=77),
        (APP, ("platform",), U, None, None): [(["Android"], 10), (["iOS"], 6), (["web"], 99)],
        (APP, ("platform",), "active28DayUsers", None, None): [(["Android"], 70), (["iOS"], 30)],
        (APP, ("platform",), U, "unifiedScreenName", "SignUp"): [(["Android"], 2), (["iOS"], 1)],
        (APP, ("eventName", "platform"), "eventCount", None, None): [
            (["login", "Android"], 11), (["login", "iOS"], 4), (["login", "web"], 50),
            (["page_view", "Android"], 500)],
        # 웹 로그인 이벤트: a015.com 은 a015/015works 두 스트림에 복제됨 → 한 벌만
        (A015, ("eventName", "hostName", "streamName"), "eventCount", "eventName", "login"): [
            (["login", "a015.com", "a015"], 9), (["login", "a015.com", "015works"], 9),
            (["login", "new.arreo.com", "newarreo"], 21), (["login_fail", "a015.com", "a015"], 70),
            (["login", "localhost", "a015"], 5)],
    }


def test_dedup_and_host_split():
    install_fake(base_table())
    res = main.get_ga4_detailed_data("20261119")

    assert res["errors"] == []
    assert res["a015_user"] == 100 + 30          # a015.com 은 max(100, 80), localhost 제외
    assert res["arreo_user"] == 50
    assert res["web_login"] == 40 + 5 + 7
    assert res["web_signup"] == 3
    assert res["cash_charge"] == 0               # 실제로 0명 → 0 (빈칸 아님)
    assert res["mau_015_total"] == 900 + 200     # a015 계열 호스트만
    assert res["dau_app"] == 16 and res["mau_app"] == 100
    assert res["app_signup"] == 3 and res["app_login"] == 15


def test_page_metrics_use_filter_not_row_sum():
    calls = install_fake(base_table())
    main.get_ga4_detailed_data("20261119")

    web_calls = [c for c in calls if c[0] in (A015, ARREO) and c[2] != "eventCount"]
    assert all(c[1] == ("hostName",) for c in web_calls)      # pagePath 행을 받아 더하지 않는다
    assert all(c[4] == main.GA_EXCLUDE_SOURCES for c in web_calls)   # 사용자 수에는 출처 제외 적용
    assert ("pagePath", main.GA_CONFIG["web_login_page_keyword"]) in [c[3] for c in web_calls]


def test_web_failure_leaves_blank_not_zero():
    install_fake(base_table(), fail_props=(ARREO,))
    res = main.get_ga4_detailed_data("20261119")

    assert len(res["errors"]) == 1
    assert all(res[k] is None for k in main.GA_WEB_KEYS)      # 한 속성만 실패해도 웹 전체 빈칸
    assert res["dau_app"] == 16                               # 앱은 정상
    assert main.fmt(res["a015_user"]) == ""


class FakeInput:
    """1~3행 머리글(3행 C열 '날짜'), 4행=17일, 5행=18일."""

    def __init__(self, col_count=46, host_header=None):
        self.updates = None
        self.col_count = col_count
        self.header_writes = []
        self._host_header = host_header

    def col_values(self, col):
        return {2: ["", "", "", "26년 11월", "26년 11월"], 3: ["날짜별", "", "날짜", "17일", "18일"]}[col]

    def add_cols(self, n):
        self.col_count += n

    def get(self, a1, **_):
        return [[self._host_header]] if self._host_header else []

    def update(self, a1, values, value_input_option=None):
        self.header_writes.append((a1, values))

    def batch_update(self, updates, value_input_option=None):
        self.updates = {u["range"]: u["values"][0][0] for u in updates}


def test_refresh_writes_only_ga_cells_of_that_row():
    install_fake(base_table())
    sheet = FakeInput()
    assert main.refresh_ga_columns(sheet, date(2026, 11, 18)) is True

    assert set(sheet.updates) == {f"{col}5" for col, _ in main.GA_INPUT_COLUMNS}
    assert sheet.updates["W5"] == "130" and sheet.updates["AK5"] == "16"
    # AU/AV a015.com · AW/AX new.arreo.com · AY/AZ 015앱 · BA/BB arreo.com (각각 MAU, DAU)
    assert [sheet.updates[c + "5"] for c in ("AU", "AV", "AW", "AX", "AY", "AZ", "BA", "BB")] == \
        ["900", "100", "200", "30", "100", "16", "4,000", "50"]


def test_refresh_skips_failed_cells_and_missing_rows():
    install_fake(base_table(), fail_props=(APP,))
    sheet = FakeInput()
    assert main.refresh_ga_columns(sheet, date(2026, 11, 18)) is False
    assert "AK5" not in sheet.updates and "W5" in sheet.updates   # 실패한 앱 칸은 기존 값 유지
    assert "AY5" not in sheet.updates and "AU5" in sheet.updates  # 015앱 MAU/DAU도 건드리지 않음

    sheet2 = FakeInput()
    assert main.refresh_ga_columns(sheet2, date(2026, 11, 1)) is True
    assert sheet2.updates is None


def test_ga_columns_match_row_layout():
    cols = [c for c, _ in main.GA_INPUT_COLUMNS]
    assert cols[:10] == list("PQRSTUVWXY") and cols[10:12] == ["AK", "AL"]
    # 호스트별 14열은 기존 46열(A~AT) 바로 뒤, AU~BH
    assert cols[12:] == ["AU", "AV", "AW", "AX", "AY", "AZ", "BA", "BB"]   # 서비스 4곳 × (MAU, DAU)
    assert main.INPUT_END_COLUMN == 54


def test_usage_columns_match_existing_columns():
    install_fake(base_table())
    res = main.get_ga4_detailed_data("20261119")
    k = main.host_key

    assert res[k("a015.com", "dau")] + res[k("new.arreo.com", "dau")] == res["a015_user"]     # W열
    assert res[k("a015.com", "mau")] + res[k("new.arreo.com", "mau")] == res["mau_015_total"]  # Y열
    assert res[k("015앱", "dau")] == res["dau_app"] and res[k("015앱", "mau")] == res["mau_app"]  # AK, AL
    assert res[k("arreo.com", "dau")] == res["arreo_user"] == 50                                # X열
    assert res[k("arreo.com", "mau")] == 4000                                                   # 스테이징 제외


def test_usage_columns_blank_per_failed_side():
    install_fake(base_table(), fail_props=(APP,))
    res = main.get_ga4_detailed_data("20261119")
    k = main.host_key
    assert res[k("015앱", "mau")] is None and res[k("015앱", "dau")] is None
    assert res[k("a015.com", "mau")] == 900 and res[k("arreo.com", "dau")] == 50


def test_login_events_per_host_deduped_across_streams():
    install_fake(base_table())
    res = main.get_ga4_detailed_data("20261119")
    k = main.host_key

    assert res[k("a015.com", "login_event")] == 9            # 복제 스트림 18이 아니라 9, login_fail/localhost 제외
    assert res[k("new.arreo.com", "login_event")] == 21
    assert res["web_login"] == 40 + 5 + 7                    # 기존 '웹 로그인'(페이지 방문자)은 그대로


def test_login_event_failure_blanks_only_its_columns():
    install_fake(base_table())
    real = main._web_login_events_by_host
    main._web_login_events_by_host = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no streamName"))
    try:
        res = main.get_ga4_detailed_data("20261119")
    finally:
        main._web_login_events_by_host = real

    assert all(res[key] is None for key in main.GA_LOGIN_EVENT_KEYS)
    assert res["errors"] == [] and res["a015_user"] == 130 and res["web_login"] == 52


def test_source_filter_falls_back_instead_of_blanking():
    install_fake(base_table(), reject_source_filter_for=("active28DayUsers",))
    res = main.get_ga4_detailed_data("20261119")

    assert res["errors"] == []                               # 값을 비우지 않는다
    assert res["mau_015_total"] == 900 + 200                 # 제외 없이 집계한 값
    assert main.GA_FILTER_FALLBACKS == {"active28DayUsers"}
    assert res["a015_user"] == 130                           # 다른 지표는 영향 없음


def test_host_split_matches_combined_total():
    install_fake(base_table())
    res = main.get_ga4_detailed_data("20261119")
    k = main.host_key

    assert res[k("a015.com", "visitors")] == 100 and res[k("new.arreo.com", "visitors")] == 30
    assert res[k("a015.com", "visitors")] + res[k("new.arreo.com", "visitors")] == res["a015_user"]
    assert res[k("a015.com", "web_login")] == 40 and res[k("new.arreo.com", "web_login")] == 5
    assert res[k("a015.com", "mau")] == 900 and res[k("new.arreo.com", "mau")] == 200
    assert res[k("a015.com", "cash_charge")] == 0          # 방문 없음 → 0


def test_host_split_blank_when_web_fails():
    install_fake(base_table(), fail_props=(ARREO,))
    res = main.get_ga4_detailed_data("20261119")
    assert all(res[key] is None for key in main.GA_DETAIL_KEYS)
    assert all(res[main.host_key(g, m)] is None for g in ("a015.com", "new.arreo.com", "arreo.com") for m in ("mau", "dau"))
    assert res[main.host_key("015앱", "dau")] == 16          # 앱 쪽은 정상


def test_ensure_host_columns_adds_headers_once():
    sheet = FakeInput(col_count=46)
    main.ensure_host_columns(sheet)
    assert sheet.col_count == 54
    (a1, values), = sheet.header_writes
    assert a1 == "AU2"                                      # 머리글 행(3행)의 윗행부터 두 줄
    assert values[0] == ["a015.com", "", "new.arreo.com", "", "015앱", "", "arreo.com", ""]
    assert values[1] == ["MAU", "DAU"] * 4

    done = FakeInput(col_count=54, host_header="DAU")
    main.ensure_host_columns(done)
    assert done.header_writes == []                          # 이미 있으면 다시 안 쓴다


if __name__ == "__main__":
    test_dedup_and_host_split()
    test_page_metrics_use_filter_not_row_sum()
    test_web_failure_leaves_blank_not_zero()
    test_refresh_writes_only_ga_cells_of_that_row()
    test_refresh_skips_failed_cells_and_missing_rows()
    test_ga_columns_match_row_layout()
    test_host_split_matches_combined_total()
    test_usage_columns_match_existing_columns()
    test_usage_columns_blank_per_failed_side()
    test_login_events_per_host_deduped_across_streams()
    test_login_event_failure_blanks_only_its_columns()
    test_source_filter_falls_back_instead_of_blanking()
    test_host_split_blank_when_web_fails()
    test_ensure_host_columns_adds_headers_once()
    print("✅ GA4 집계 점검 통과")
