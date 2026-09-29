"""Generate (description+image, no posting) specific S.No rows:
python scripts/generate_sno.py 93 106 107 124 127 128 156 477 480 483 485 486 487 488
"""
import sys
sys.path.insert(0, r"c:\Users\Shrey\OneDrive - Ken Research Private Limited\Desktop\GBP automation")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright
from src import config
from src.sheets_client import SheetsClient
from src.generation_agent import generate_for_row, _is_dead_browser_error
from src.browser import ensure_and_connect, close_if_owned

TARGET_SNO = [int(x) for x in sys.argv[1:]]
if not TARGET_SNO:
    print("Usage: python scripts/generate_sno.py <S.No> [<S.No> ...]")
    sys.exit(1)

sheets = SheetsClient()
rows = sheets.read_all_rows()
by_sno = {int(r.get("S.No")): r for r in rows if r.get("S.No")}

with sync_playwright() as pw:
    proc, ctx = ensure_and_connect(pw, config.CHATGPT_PROFILE_DIR, config.CHATGPT_CDP_PORT, start_url="https://chatgpt.com/")
    try:
        for sno in TARGET_SNO:
            row = by_sno[sno]
            print(f"--- S.No {sno}: generating ---")
            try:
                result = generate_for_row(ctx, row, sheets)
            except Exception as exc:
                if _is_dead_browser_error(exc):
                    print(f"S.No {sno}: browser dead ({exc}) — reconnecting and retrying once")
                    try:
                        close_if_owned(proc)
                    except Exception:
                        pass
                    try:
                        proc, ctx = ensure_and_connect(
                            pw, config.CHATGPT_PROFILE_DIR, config.CHATGPT_CDP_PORT,
                            start_url="https://chatgpt.com/",
                        )
                        result = generate_for_row(ctx, row, sheets)
                    except Exception as exc2:
                        sheets.mark_generation_failed(row.row_number, f"Generation error (after reconnect): {exc2}")
                        print(f"S.No {sno}: CRASHED after reconnect - {exc2}")
                        continue
                else:
                    sheets.mark_generation_failed(row.row_number, f"Generation error: {exc}")
                    print(f"S.No {sno}: CRASHED - {exc}")
                    continue
            if result.success:
                sheets.update_row(row.row_number, **{
                    "Market Name": result.market_name,
                    "Base Year / Forecast Year": f"{result.base_year} / {result.forecast_year}",
                    "Base / Forecast Market Size": f"{result.base_market_size} / {result.forecast_market_size}".strip(" /"),
                    "CAGR (%)": result.cagr,
                    "GBP Description": result.description,
                    "Image Status": "Generated",
                    "Image Link (Cloudinary)": result.image_url,
                    "CTA Link (UTM-tagged)": result.cta_link,
                    "Batch Status": "Generated",
                    "Error / Notes": "",
                })
                print(f"S.No {sno}: OK - {result.image_url}")
            else:
                sheets.mark_generation_failed(row.row_number, result.error or "Unknown failure")
                print(f"S.No {sno}: FAILED - {result.error}")
    finally:
        close_if_owned(proc)
