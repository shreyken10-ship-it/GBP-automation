"""Stage 2 — Posting Agent.

Publishes each Generated row to the Ken Research Google Business Profile
through the Google Search "Business Profile" panel — confirmed live, step by
step, with the account that manages the listing:

    search "ken research" -> click "Posts" tile -> click "Add post"
    -> fill Description -> attach image -> "Add more details" -> "+ Button"
    -> button-type dropdown -> "Learn more" -> paste CTA link -> "Post"

This is NOT the separate business.google.com dashboard — that path was never
used. The composer itself renders inside an iframe at
google.com/local/business/{id}/promote/updates, so every element lookup
after the initial click targets that frame, not the top-level page.

Posts are spread across the operating window on a jittered schedule (FR-11)
with randomized in-page action pacing (FR-12). Runs only after Stage 1 has
finished for the whole batch (FR-6 — orchestrator enforces the sequencing).
Google's DOM is not a public contract, so a broken step fails soft
(screenshot + Failed status) rather than raising past the row loop.
"""
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import requests
from playwright.sync_api import BrowserContext, Frame, Page, sync_playwright

from . import config, pacing
from .log import get_logger, screenshot_on_failure
from .models import PostResult, SheetRow
from .sheets_client import SheetsClient

logger = get_logger("posting")

SEARCH_URL = f"https://www.google.com/search?q={urllib.parse.quote(config.GBP_SEARCH_QUERY)}"


def _pause(page: Page):
    page.wait_for_timeout(int(pacing.action_pause() * 1000))


def _business_frame(page: Page, timeout_s: int = 20) -> Frame:
    """The post composer renders in an iframe at .../local/business/{id}/...
    — wait for it to appear (it loads after clicking the Posts tile)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            if "/local/business/" in f.url:
                return f
        page.wait_for_timeout(500)
    raise RuntimeError("Business profile iframe never appeared — not logged in, or GBP UI changed")


def _copy_post_frame(page: Page, timeout_s: int = 10) -> Frame | None:
    """The 'Copy post' dialog (offers to push the same update to every other
    profile you manage — required to actually reach all Ken Research
    locations, not just the primary one) renders in its OWN iframe at
    .../local/business/{id}/promote/updates/copy/{postId} — a different
    frame from the compose iframe, and never on the top-level page.

    Verified live: page.get_by_text("Select all") always failed because
    "Select all" only ever exists inside this frame. Also verified live: the
    top-level page's OWN url contains the substring "/promote/updates/copy/"
    too (Google uses a '#mpd=...' hash-routed URL that includes the same
    path), so the check must require BOTH "/local/business/" (only true
    iframes have this prefix) AND the copy-specific path — matching on the
    copy path alone silently matches the top-level page and returns text
    that's never actually visible there.

    Returns None if it never appears — this step is optional (not every
    post triggers a copy-eligible dialog), so a caller must not treat a
    timeout here as a failure."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            if "/local/business/" in f.url and "/promote/updates/copy/" in f.url:
                return f
        page.wait_for_timeout(500)
    return None


def _first_visible(root, candidates: list[str], timeout_ms: int = 10000):
    """Return the first visible locator among candidate selectors, tried
    against `root` (a Page or Frame). Google's composer uses Material
    floating-label fields without real `placeholder` attributes, so a single
    guessed selector is fragile — try a few plausible ones instead of one."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in candidates:
            loc = root.locator(sel).first
            try:
                if loc.is_visible():
                    return loc
            except Exception:
                continue
        time.sleep(0.3)
    raise RuntimeError(f"None of the candidate selectors matched: {candidates}")


def _download_to_temp(image_url: str) -> Path:
    resp = requests.get(image_url, timeout=60)
    resp.raise_for_status()
    suffix = ".png" if ".png" in image_url.lower() else ".jpg"
    tmp = tempfile.NamedTemporaryFile(prefix="gbp-post-", suffix=suffix, delete=False)
    tmp.write(resp.content)
    tmp.close()
    return Path(tmp.name)


def post_row(context: BrowserContext, row: SheetRow, publish: bool = True) -> PostResult:
    """Fill and submit one GBP post. If publish=False, everything up to the
    composer being fully ready runs, but the final "Post" click is skipped —
    useful to eyeball a post live before trusting the automation to publish
    unattended."""
    page = context.pages[0] if context.pages else context.new_page()
    image_path = None
    try:
        page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        posts_tile = page.get_by_text("Posts", exact=True).first
        try:
            posts_tile.wait_for(state="visible", timeout=20000)
        except Exception:
            # Slow page load under real conditions (verified live: a fully
            # blank reload cleared it) — same reload-and-retry fallback used
            # elsewhere for this class of issue, rather than failing the row.
            logger.warning("Row %s: 'Posts' tile didn't appear in time, reloading and retrying once", row.row_number)
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            posts_tile = page.get_by_text("Posts", exact=True).first
            posts_tile.wait_for(state="visible", timeout=20000)
        posts_tile.click()
        _pause(page)

        frame = _business_frame(page)

        add_post = frame.get_by_text("Add post", exact=True).first
        add_post.wait_for(state="visible", timeout=15000)
        add_post.click()
        _pause(page)

        # --- description, typed in chunks with human-ish cadence (FR-12) ---
        desc_box = _first_visible(frame, [
            "textarea",
            "[aria-label*='Description' i]",
            "[placeholder*='Description' i]",
        ], timeout_ms=15000)
        desc_box.click()
        _pause(page)
        description = row.get("GBP Description")
        chunk = 120
        for i in range(0, len(description), chunk):
            page.keyboard.insert_text(description[i : i + chunk])
            page.wait_for_timeout(pacing.typing_delay_ms() * 3)
        _pause(page)

        # --- image ---
        image_path = _download_to_temp(row.get("Image Link (Cloudinary)"))
        file_input = frame.locator("input[type='file']").first
        file_input.set_input_files(str(image_path))
        logger.info("Row %s: image attached", row.row_number)
        page.wait_for_timeout(int(pacing.action_pause() * 2000))  # let upload finish

        # --- CTA: Add more details -> + Button -> dropdown -> Learn more -> link ---
        more_details = frame.get_by_text("Add more details", exact=True).first
        more_details.click()
        _pause(page)

        add_button_pill = frame.get_by_text("Button", exact=True).first
        add_button_pill.click()
        _pause(page)

        dropdown = frame.get_by_text("None", exact=True).first
        dropdown.click()
        _pause(page)

        learn_more = frame.get_by_text("Learn more", exact=True).first
        learn_more.wait_for(state="visible", timeout=8000)
        # The dropdown menu is still animating open right as it becomes
        # "visible" — observed live: click retries fail for 30s with
        # "element is visible, enabled and stable" yet "intercepts pointer
        # events" the whole time, meaning something else (the animating
        # menu itself) is still covering it. A short settle wait fixes most
        # cases; force=True is the fallback for the rest (menu item itself
        # is unambiguous — no risk of clicking the wrong thing).
        page.wait_for_timeout(600)
        try:
            learn_more.click(timeout=10000)
        except Exception:
            logger.warning("Row %s: 'Learn more' click intercepted, retrying with force", row.row_number)
            learn_more.click(force=True, timeout=10000)
        _pause(page)

        link_input = _first_visible(frame, [
            "input[type='url']",
            "input[type='text']",
            "[aria-label*='link' i] input",
            "[placeholder*='google.com' i]",
        ], timeout_ms=8000)
        link_input.click()
        page.keyboard.insert_text(row.get("CTA Link (UTM-tagged)"))
        _pause(page)

        if not publish:
            logger.info("Row %s: composer ready, publish=False — leaving for manual review", row.row_number)
            return PostResult(success=True, posted_at=None, error="DRY RUN — Post not clicked")

        # --- publish ---
        post_button = frame.get_by_text("Post", exact=True).last
        post_button.wait_for(state="visible", timeout=10000)
        post_button.click()

        # Verify the submit actually went through: the composer (its
        # description box) must disappear. Blind-trusting a fixed wait risks
        # writing "Posted" to the sheet for a post that never went live —
        # a silent loss nobody would notice without checking the profile.
        deadline = time.time() + 30
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            try:
                if not desc_box.is_visible():
                    break
            except Exception:
                break  # element detached — composer is gone, submit went through
        else:
            raise RuntimeError("Composer still open 30s after clicking Post — publish likely failed")

        posted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("Row %s: posted at %s", row.row_number, posted_at)

        # --- "Copy post" dialog: pushes the same update to every other
        # profile you manage. Required to actually reach all Ken Research
        # locations (Gurugram, Indonesia, Dubai, Doha, ...) — without this,
        # only the primary profile gets the post. Renders in its own iframe
        # (see _copy_post_frame) — searching page.get_by_text(...) on the
        # top-level page, as this used to, NEVER finds it (verified live:
        # every single row logged "no dialog appeared" even when the dialog
        # was genuinely showing on screen). Still best-effort in the sense
        # that not every post is copy-eligible — a missing dialog after a
        # real wait must not fail the row, since the primary post already
        # succeeded — but when the frame IS found, every step in it must
        # actually work, not silently no-op.
        #
        # IMPORTANT: wait_for (genuinely-no-dialog) and click (dialog present
        # but intercepted mid-animation, same root cause as the 'Learn more'
        # bug) are handled in SEPARATE try blocks. A single broad try/except
        # around both was silently swallowing real click failures and
        # mislabeling them as "no dialog appeared".
        copy_frame = _copy_post_frame(page, timeout_s=10)
        if copy_frame is None:
            logger.info("Row %s: no 'Copy post' dialog appeared", row.row_number)
        else:
            try:
                select_all = copy_frame.get_by_text("Select all", exact=True).first
                select_all.wait_for(state="visible", timeout=8000)
            except Exception:
                logger.warning("Row %s: 'Copy post' frame appeared but 'Select all' never became visible in it", row.row_number)
            else:
                page.wait_for_timeout(600)  # let the dialog finish animating in
                try:
                    select_all.click(timeout=10000)
                except Exception:
                    logger.warning("Row %s: 'Select all' click intercepted, retrying with force", row.row_number)
                    select_all.click(force=True, timeout=10000)
                _pause(page)

                copy_post_button = copy_frame.get_by_role("button", name="Post", exact=True).first
                try:
                    copy_post_button.click(timeout=10000)
                except Exception:
                    logger.warning("Row %s: Copy-post 'Post' click intercepted, retrying with force", row.row_number)
                    copy_post_button.click(force=True, timeout=10000)
                _pause(page)
                logger.info("Row %s: copied post to other managed profiles", row.row_number)

        return PostResult(success=True, posted_at=posted_at)

    except Exception as exc:
        shot = screenshot_on_failure(page, f"post-row{row.row_number}")
        logger.exception("Row %s: posting failed (screenshot: %s)", row.row_number, shot)
        return PostResult(success=False, error=str(exc))
    finally:
        if image_path:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_batch(sheets: SheetsClient) -> dict:
    rows = sheets.get_generated_rows()
    logger.info("Posting stage: %d row(s) ready", len(rows))
    summary = {"total": len(rows), "posted": 0, "failed": 0}
    if not rows:
        return summary

    # Fixed 8-10 min randomized gap between consecutive posts (per user
    # instruction) — NOT spread across POSTING_WINDOW_HOURS. That older
    # "spread the whole batch across an 8h window" model is unused now;
    # pacing.post_gap_seconds() is the single source of truth for posting
    # cadence, shared with the one-off scratchpad scripts.
    logger.info("Posting %d row(s), 8-10 min apart (randomized)", len(rows))

    with sync_playwright() as pw:
        from .browser import close_if_owned, ensure_and_connect

        proc, context = ensure_and_connect(
            pw, config.GBP_PROFILE_DIR, config.GBP_CDP_PORT, start_url=SEARCH_URL
        )
        try:
            for i, row in enumerate(rows):
                if i > 0:
                    gap_s = pacing.post_gap_seconds()
                    logger.info("Row %s: sleeping %.0f min until its slot", row.row_number, gap_s / 60)
                    time.sleep(gap_s)

                result = post_row(context, row)
                # The status write must NEVER kill the loop: the post is
                # already live at this point, and an unrecorded "Posted"
                # means the next run would publish a duplicate. If the write
                # fails even after the client's own retries, log loudly and
                # keep going — one stale cell beats a duplicate live post
                # plus every remaining slot abandoned.
                try:
                    if result.success:
                        sheets.update_row(
                            row.row_number,
                            **{
                                "Post Status": "Posted",
                                "Post Timestamp": result.posted_at,
                                "Error / Notes": "",
                            },
                        )
                    else:
                        sheets.mark_post_failed(row.row_number, f"Posting error: {result.error}")
                except Exception:
                    logger.exception(
                        "Row %s: SHEET WRITE FAILED after posting (post IS live: %s) — "
                        "mark this row 'Posted' manually before the next run to avoid a duplicate",
                        row.row_number, result.success,
                    )
                summary["posted" if result.success else "failed"] += 1
        finally:
            close_if_owned(proc)

    return summary
