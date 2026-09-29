"""ONE-TIME override: post specific rows (by sheet row number) a short gap
apart instead of the normal 10-15 min gap. Not wired into any reusable
pacing default — just for this run.
Usage: python scripts/post_fast_onetime.py 404 405 406 407 408
"""
import sys
sys.path.insert(0, r"c:\Users\Shrey\OneDrive - Ken Research Private Limited\Desktop\GBP automation")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import time
from playwright.sync_api import sync_playwright
from src import config
from src.sheets_client import SheetsClient
from src.posting_agent import post_row, SEARCH_URL
from src.browser import ensure_and_connect, close_if_owned

TARGET_ROW_NUMBERS = [int(x) for x in sys.argv[1:]]
if not TARGET_ROW_NUMBERS:
    print("Usage: python scripts/post_fast_onetime.py <row_number> [<row_number> ...]")
    sys.exit(1)

ONE_TIME_GAP_SECONDS = 10

sheets = SheetsClient()
rows = sheets.read_all_rows()
by_row = {r.row_number: r for r in rows}

with sync_playwright() as pw:
    proc, ctx = ensure_and_connect(pw, config.GBP_PROFILE_DIR, config.GBP_CDP_PORT, start_url=SEARCH_URL)
    try:
        for i, rn in enumerate(TARGET_ROW_NUMBERS):
            if i > 0:
                print(f"--- waiting {ONE_TIME_GAP_SECONDS}s before next post ---")
                time.sleep(ONE_TIME_GAP_SECONDS)
            row = by_row[rn]
            print(f"--- row {rn} ({row.get('Market Name')}): posting to GBP ---")
            result = post_row(ctx, row, publish=True)
            if result.success:
                sheets.update_row(row.row_number, **{
                    "Post Status": "Posted",
                    "Post Timestamp": result.posted_at,
                    "Error / Notes": "",
                })
                print(f"row {rn}: POSTED at {result.posted_at}")
            else:
                sheets.mark_post_failed(row.row_number, f"Posting error: {result.error}")
                print(f"row {rn}: FAILED - {result.error}")
    finally:
        close_if_owned(proc)
