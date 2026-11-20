"""GA4 집계 미리보기. 시트에는 쓰지 않고, 날짜별 값을 JSON 파일로만 남긴다.

    python3 test/ga4_preview.py 20261001 20261031
    -> ga4_preview_20261001_20261031.json
"""
import json
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.simplefilter("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402


def run(start, end):
    out = {}
    day = start
    while day <= end:
        out[day.isoformat()] = main.get_ga4_detailed_data(day.strftime("%Y%m%d"))
        day += timedelta(days=1)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("사용법: python3 test/ga4_preview.py YYYYMMDD YYYYMMDD")
    start, end = (datetime.strptime(a, "%Y%m%d").date() for a in sys.argv[1:3])
    path = ROOT / f"ga4_preview_{sys.argv[1]}_{sys.argv[2]}.json"
    path.write_text(json.dumps(run(start, end), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✅ 저장: {path} (시트는 건드리지 않았습니다)")
