"""서비스별 MAU/DAU 과거 기간 일괄 조회. 시트에는 쓰지 않고 CSV 파일로만 남긴다.

    python3 test/ga4_usage_history.py 20250101 20261118
    -> ga4_usage_20250101_20261118.csv

열 순서는 데이터입력시트 AU~BB와 같다 (a015.com, new.arreo.com, 015앱, arreo.com × MAU, DAU).
집계 기준(제외 호스트, 봇 출처 제외, 속성 간 중복 방지)은 main.py와 같은 설정을 쓴다.
기간 전체를 속성당 조회 몇 번으로 읽으므로 하루씩 도는 ga4_preview.py보다 훨씬 빠르다.
"""
import csv
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

warnings.simplefilter("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from google.analytics.data_v1beta import BetaAnalyticsDataClient  # noqa: E402
from google.analytics.data_v1beta.types import (  # noqa: E402
    DateRange, Dimension, Metric, RunReportRequest,
)

PAGE = 100000
FALLBACKS = set()


def read_rows(client, prop, start, end, dimensions, metric, exclude_sources=None):
    """[([차원값...], 정수값), ...] — 끝 페이지까지."""
    rows, offset = [], 0
    dimension_filter = main._build_filter(None, exclude_sources)
    while True:
        kwargs = dict(
            property=prop,
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=metric)],
            limit=PAGE, offset=offset,
        )
        if dimension_filter is not None:
            kwargs["dimension_filter"] = dimension_filter
        resp = client.run_report(RunReportRequest(**kwargs))
        for row in resp.rows:
            rows.append(([v.value for v in row.dimension_values], int(row.metric_values[0].value)))
        offset += len(resp.rows)
        if not resp.rows or offset >= resp.row_count:
            return rows


def web_by_date_host(client, prop, start, end, metric):
    """{날짜: {호스트: 사용자 수}} — 개발/테스트 호스트 제외, 봇 출처 제외."""
    args = (client, prop, start, end, ["date", "hostName"], metric)
    try:
        rows = read_rows(*args, exclude_sources=main.GA_EXCLUDE_SOURCES or None)
    except Exception as e:
        if not main.GA_EXCLUDE_SOURCES:
            raise
        rows = read_rows(*args)
        FALLBACKS.add(metric)
        print(f"   ⚠️ 출처 제외 필터 미적용({metric}): {str(e)[:160]}")
    out = defaultdict(dict)
    for (day, host), cnt in rows:
        if any(k in host for k in main.GA_CONFIG['host_exclude']):
            continue
        out[day][host] = out[day].get(host, 0) + cnt
    return out


def merge_max(merged, part):
    for day, hosts in part.items():
        for host, cnt in hosts.items():
            merged[day][host] = max(merged[day].get(host, 0), cnt)


def is_arreo(host):
    return not main._is_a015_host(host) and any(k in host for k in main.GA_CONFIG['host_arreo'])


def collect(start, end):
    client = BetaAnalyticsDataClient.from_service_account_json(main.JSON_KEY_FILE)
    dau, mau_015, mau_all = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for web_id in main.GA4_WEB_IDS:
        prop = f"properties/{web_id}"
        print(f"📊 웹 속성 {web_id} 조회 중...")
        merge_max(dau, web_by_date_host(client, prop, start, end, "activeUsers"))
        part = web_by_date_host(client, prop, start, end, "active28DayUsers")
        merge_max(mau_all, part)
        if web_id in main.GA4_MAU_WEB_IDS:      # main.py와 같이 015 계열 MAU는 015 속성에서만
            merge_max(mau_015, part)

    print(f"📊 앱 속성 {main.GA4_APP_ID} 조회 중...")
    app = {}
    for key, metric in (("dau", "activeUsers"), ("mau", "active28DayUsers")):
        app[key] = defaultdict(int)
        for (day, platform), cnt in read_rows(
                client, f"properties/{main.GA4_APP_ID}", start, end, ["date", "platform"], metric):
            if platform in main.GA_APP_PLATFORMS:
                app[key][day] += cnt

    def total(table, day, pick):
        return sum(c for h, c in table.get(day, {}).items() if pick(h))

    out = []
    day = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while day <= last:
        d = day.strftime("%Y%m%d")
        out.append([
            day.isoformat(),
            total(mau_015, d, lambda h: 'a015.com' in h), total(dau, d, lambda h: 'a015.com' in h),
            total(mau_015, d, lambda h: 'new.arreo.com' in h), total(dau, d, lambda h: 'new.arreo.com' in h),
            app["mau"].get(d, 0), app["dau"].get(d, 0),
            total(mau_all, d, is_arreo), total(dau, d, is_arreo),
        ])
        day += timedelta(days=1)
    return out


HEADER = ["날짜"] + [f"{g} {m}" for g in main.GA_USAGE_GROUPS for _, m in main.GA_USAGE_METRICS]

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("사용법: python3 test/ga4_usage_history.py YYYYMMDD YYYYMMDD")
    start, end = (datetime.strptime(a, "%Y%m%d").strftime("%Y-%m-%d") for a in sys.argv[1:3])
    table = collect(start, end)
    path = ROOT / f"ga4_usage_{sys.argv[1]}_{sys.argv[2]}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(table)
    print(f"\n✅ {len(table)}일치 저장: {path} (시트는 건드리지 않았습니다)")
    if FALLBACKS:
        print(f"   ⚠️ 봇 출처 제외가 적용되지 않은 지표: {sorted(FALLBACKS)}")
    print("   마지막 3일:", *table[-3:], sep="\n   ")
