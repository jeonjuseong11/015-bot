"""main.py를 외부 라이브러리 없이 import 하기 위한 스텁.

시트 조작 로직만 점검할 때 사용한다. gspread/psycopg2/pymysql/GA4를 설치하지 않아도
`import main`이 되도록 sys.modules에 빈 모듈을 꽂는다.
rowcol_to_a1만 실제와 같은 동작이 필요해서 직접 구현한다.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rowcol_to_a1(row, col):
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def install():
    sys.path.insert(0, str(ROOT))

    for name in (
        "gspread", "gspread.utils", "psycopg2", "pymysql",
        "oauth2client", "oauth2client.service_account",
        "google", "google.analytics", "google.analytics.data_v1beta",
        "google.analytics.data_v1beta.types",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules["gspread.utils"].rowcol_to_a1 = rowcol_to_a1
    for name, attr in (
        ("oauth2client.service_account", "ServiceAccountCredentials"),
        ("google.analytics.data_v1beta", "BetaAnalyticsDataClient"),
        ("google.analytics.data_v1beta.types", "RunReportRequest"),
        ("google.analytics.data_v1beta.types", "DateRange"),
        ("google.analytics.data_v1beta.types", "Metric"),
        ("google.analytics.data_v1beta.types", "Dimension"),
    ):
        setattr(sys.modules[name], attr, object)

    import main
    return main
