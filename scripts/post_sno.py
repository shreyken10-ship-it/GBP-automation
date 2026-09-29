"""Post specific S.No rows to GBP by number: python scripts/post_sno.py 245 247"""
import sys
sys.path.insert(0, r"c:\Users\Shrey\OneDrive - Ken Research Private Limited\Desktop\GBP automation")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright
from src import config, pacing
from src.sheets_client import SheetsClient
from src.posting_agent import post_row, SEARCH_URL
from src.browser import ensure_and_connect, close_if_owned
import time

TARGET_SNO = [int(x) for x in sys.argv[1:]]
if not TARGET_SNO:
    print("Usage: python scripts/post_sno.py <S.No> [<S.No> ...]")
    sys.exit(1)

sheets = SheetsClient()
rows = sheets.read_all_rows()
by_sno = {int(r.get("S.No")): r for r in rows if r.get("S.No")}

with sync_playwright() as pw:
    proc, ctx = ensure_and_connect(pw, config.GBP_PROFILE_DIR, config.GBP_CDP_PORT, start_url=SEARCH_URL)
    try:
        for i, sno in enumerate(TARGET_SNO):
            if i > 0:
                gap = pacing.post_gap_seconds()
                print(f"--- waiting {gap / 60:.1f} min before next post ---")
                time.sleep(gap)
            row = by_sno[sno]
            print(f"--- S.No {sno}: posting to GBP ---")
            result = post_row(ctx, row, publish=True)
            if result.success:
                sheets.update_row(row.row_number, **{
                    "Post Status": "Posted",
                    "Post Timestamp": result.posted_at,
                    "Error / Notes": "",
                })
                print(f"S.No {sno}: POSTED at {result.posted_at}")
            else:
                sheets.mark_post_failed(row.row_number, f"Posting error: {result.error}")
                print(f"S.No {sno}: FAILED - {result.error}")
    finally:
        close_if_owned(proc)
