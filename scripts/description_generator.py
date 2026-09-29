"""Standalone ChatGPT + Playwright description generator (no image generation).

Extracted from src/generation_agent.py — drives a real Chrome instance
(attached via CDP, not Playwright's bundled Chromium) to a fresh ChatGPT
chat, submits the GBP description prompt for a given report URL, waits for
the reply to finish streaming, cleans it up, and parses the key fields back
out of it (market name, base/forecast year, base/forecast size, CAGR).

Usage:
    python scripts/description_generator.py "https://www.kenresearch.com/industry-reports/..."

Requires:
    - Google Chrome installed (see CHROME_CANDIDATES below)
    - A Chrome profile already logged into chatgpt.com at CHATGPT_PROFILE_DIR
      (first run will open a blank Chrome window — log in manually once,
      the session persists in that profile dir for every run after)
"""
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

# ─────────────────────────────────────────────────────────────────────────
# Config — adjust these for your machine/profile
# ─────────────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
CHATGPT_PROFILE_DIR = ROOT_DIR / "auth" / "chatgpt_profile"
CHATGPT_CDP_PORT = 9333

DESCRIPTION_MIN_CHARS = 1400
DESCRIPTION_MAX_CHARS = 1500
DESCRIPTION_LENGTH_RETRIES = 2

TEXT_TIMEOUT_S = 240     # max time to wait for one reply to finish streaming
STABLE_S = 20            # reply considered "done" after this long unchanged
RATE_LIMIT_COOLDOWN_MS = 60000

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# ─────────────────────────────────────────────────────────────────────────
# Real-Chrome-via-CDP launcher (not Playwright's bundled test Chromium —
# this looks/behaves like a normal Chrome window, no automation banner)
# ─────────────────────────────────────────────────────────────────────────

def find_chrome_exe() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        return found
    raise RuntimeError("Could not locate chrome.exe — install Chrome or edit CHROME_CANDIDATES.")


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


_DETACHED_FLAGS = (
    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "DETACHED_PROCESS")
    else 0
)


def launch_chrome(profile_dir: Path, port: int, start_url: str = "about:blank") -> subprocess.Popen:
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        find_chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    proc = subprocess.Popen(
        args, creationflags=_DETACHED_FLAGS, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_open(port):
            return proc
        time.sleep(0.3)
    raise RuntimeError(f"Chrome did not open debug port {port} within 20s")


def ensure_and_connect(pw: Playwright, profile_dir: Path, port: int, start_url: str) -> tuple[subprocess.Popen | None, BrowserContext]:
    proc = None
    if not _port_open(port):
        proc = launch_chrome(profile_dir, port, start_url)
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return proc, context


def close_if_owned(proc: subprocess.Popen | None):
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────
# The approved GBP description prompt, verbatim
# ─────────────────────────────────────────────────────────────────────────
DESCRIPTION_PROMPT_TEMPLATE = """You are generating a Google Business Profile (GBP) post for a Ken Research report.

Report URL:
{report_url}

Your task:

1. Carefully read the entire report page.
2. Extract ONLY factual data mentioned on the page, including:
   - Market size (current value)
   - Forecast value (if available)
   - CAGR (%)
   - Base year and forecast period
   - Key numerical statistics
3. Do NOT invent numbers.
4. If specific numbers are not available, do NOT fabricate them.
5. Do NOT repeat the company name in the description.
6. Maintain institutional, analytical, B2B tone.
7. Keep total length between 1400–1500 characters.
8. Include bullet-point "Scope of Work".
9. CTA must be: View Report

Formatting Requirements:

TITLE
Include:
- Market name
- Forecast years
- "Market Size" and "Forecast" keywords
- If CAGR is available, include it in title

Then:

Short analytical intro paragraph (2–3 lines) including:
- Current market size (if available)
- Forecast value (if available)
- CAGR (if available)

Then:

The scope of work includes:

• Market size estimation and CAGR analysis
• Segmentation breakdown (as mentioned on page)
• Competitive landscape assessment
• Pricing and revenue trends (if mentioned)
• Distribution channel analysis (if mentioned)
• Regional or country-level insights (if mentioned)
• Import/export or supply chain insights (if mentioned)
• Growth drivers, challenges, and opportunity mapping

Then:

One professional closing line focused on strategic use.

CTA: View Report

Do NOT include:
- Emojis
- Marketing language
- Hashtags
- Pricing unless explicitly mentioned
- Assumptions

Now generate the GBP-ready description strictly based on this URL:

{report_url}"""

# ─────────────────────────────────────────────────────────────────────────
# ChatGPT page selectors
# ─────────────────────────────────────────────────────────────────────────
PROMPT_INPUT = "#prompt-textarea, div[contenteditable='true']"
STOP_BUTTON = "button[data-testid='stop-button']"
# Two turn markups exist: the classic chat UI uses
# div[data-message-author-role='assistant']; a newer agent-turn UI renders
# [data-turn='assistant'] with no data-message-author-role at all.
ASSISTANT_TURN = "div[data-message-author-role='assistant'], [data-turn='assistant']"

_CTA_LINE_RE = re.compile(r"^\s*CTA\s*:.*$\n?", re.IGNORECASE | re.MULTILINE)
_TITLE_LINE_RE = re.compile(r"^.*\bMarket\b.*\b(?:Size|Forecast)\b.*$", re.MULTILINE)


class ChatGPTRateLimitedError(RuntimeError):
    """ChatGPT's 'too many requests' modal is showing and won't dismiss."""


def _strip_preamble(text: str) -> str:
    """Reasoning models sometimes prepend chatter above the actual content
    in the same message ("I'll verify the report page first...", "Thought
    for 1m 47s", ...). The prompt requires the title line to contain
    "Market" + "Size"/"Forecast", so anchor on that and drop everything
    before it. No-op if the title is already the first line."""
    match = _TITLE_LINE_RE.search(text)
    return text[match.start():].strip() if match else text.strip()


def _strip_cta_line(description: str) -> str:
    """The prompt has GPT literally write 'CTA: View Report' as text, but
    the CTA is a separate button+link in the real GBP composer — strip it
    from the body."""
    return _CTA_LINE_RE.sub("", description).strip()


def _truncate_to_fit(description: str, max_chars: int = DESCRIPTION_MAX_CHARS, min_chars: int = DESCRIPTION_MIN_CHARS) -> str:
    """Deterministically trim text over the max length — prefers a sentence
    boundary, falls back to a line boundary, falls back to a hard cut."""
    if len(description) <= max_chars:
        return description
    truncated = description[:max_chars]

    cut = max(truncated.rfind(". "), truncated.rfind(".\n"))
    if cut > min_chars:
        return truncated[: cut + 1].strip()

    cut = truncated.rfind("\n")
    if cut > min_chars:
        return truncated[:cut].strip()

    return truncated.strip()


def _dismiss_lightbox(page: Page):
    """Closes ChatGPT's fullscreen image-preview lightbox if one is open
    (it intercepts all clicks until closed), and handles the account-level
    'Too many requests' rate-limit modal — dismiss, pause, and continue;
    raise ChatGPTRateLimitedError only if it refuses to close."""
    try:
        if page.locator("#modal-lightbox-new").count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

        rate_limited = (
            page.locator("#modal-conversation-history-rate-limit").count() > 0
            or page.get_by_text("Too many requests", exact=False).count() > 0
        )
        if not rate_limited:
            return
        print("Rate-limit modal showing — dismissing and pausing before continuing")
        try:
            gotit = page.get_by_role("button", name="Got it")
            if gotit.count() > 0:
                gotit.first.click(timeout=3000)
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")
        page.wait_for_timeout(RATE_LIMIT_COOLDOWN_MS)
        still_limited = (
            page.locator("#modal-conversation-history-rate-limit").count() > 0
            or page.get_by_text("Too many requests", exact=False).count() > 0
        )
        if still_limited:
            raise ChatGPTRateLimitedError(
                "ChatGPT 'too many requests' modal would not dismiss — stop and wait before retrying."
            )
    except ChatGPTRateLimitedError:
        raise
    except Exception:
        pass


def _submit_prompt(page: Page, prompt: str):
    _dismiss_lightbox(page)
    box = page.locator(PROMPT_INPUT).first
    try:
        box.wait_for(state="visible", timeout=45000)
    except Exception:
        print("Prompt box didn't appear in time, reloading and retrying once")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        box = page.locator(PROMPT_INPUT).first
        box.wait_for(state="visible", timeout=45000)
    _dismiss_lightbox(page)
    box.click()
    page.wait_for_timeout(1500)
    box.fill(prompt)
    page.wait_for_timeout(1500)
    page.keyboard.press("Enter")


def _wait_streaming_done(page: Page, timeout_s: int) -> str:
    """Poll the last assistant turn; done when its text is non-empty and
    unchanged for STABLE_S and the stop (streaming) button is gone."""
    start = time.time()
    last_text = ""
    stable_since = time.time()

    while time.time() - start < timeout_s:
        page.wait_for_timeout(3000)
        try:
            turns = page.locator(ASSISTANT_TURN)
            current = turns.last.inner_text(timeout=5000) if turns.count() else ""
        except Exception:
            continue

        if current != last_text:
            last_text = current
            stable_since = time.time()
            continue

        streaming = page.locator(STOP_BUTTON).count() > 0
        if last_text.strip() and not streaming and time.time() - stable_since > STABLE_S:
            return last_text

    return last_text  # timeout: return whatever rendered


def _ensure_fresh_chat(page: Page, max_attempts: int = 2):
    """Verify the page is a genuinely blank new conversation before
    submitting a prompt — a plain page.goto() is NOT a reliable guarantee
    of this (a prior conversation's content can bleed through)."""
    for attempt in range(max_attempts):
        page.wait_for_timeout(1500)
        if page.locator(ASSISTANT_TURN).count() == 0:
            break
        print(f"New chat still shows prior conversation turns — forcing reload (attempt {attempt + 1})")
        page.goto("https://chatgpt.com/", wait_until="networkidle", timeout=60000)
    else:
        raise RuntimeError("Could not get a blank ChatGPT conversation — stale content persists after retries")

    box = page.locator(PROMPT_INPUT).first
    try:
        if box.count() > 0 and (box.inner_text() or "").strip():
            print("Composer had leftover draft text — clearing it")
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(300)
    except Exception:
        pass


def _parse_description_fields(description: str) -> dict:
    """Best-effort extraction of key fields from the generated description."""
    range_match = re.search(r"\bin (20\d{2}|19\d{2})\b.{0,80}?\bby (20\d{2}|19\d{2})\b", description, re.DOTALL)
    if range_match:
        base_year, forecast_year = range_match.group(1), range_match.group(2)
    else:
        years = re.findall(r"\b(20\d{2}|19\d{2})\b", description)
        base_year = min(years) if years else ""
        forecast_year = max(years) if years else ""

    cagr_match = re.search(r"CAGR[^0-9%]{0,40}?([\d.]+)\s*%", description, re.IGNORECASE) \
        or re.search(r"([\d.]+)\s*%[^.\n]{0,30}CAGR", description, re.IGNORECASE)
    cagr = cagr_match.group(1) + "%" if cagr_match else ""

    size_pattern = re.compile(
        r"(?:USD|US\$|INR|EUR|GBP|AED|SAR|JPY|CNY)\s?[\d,.]+\s?"
        r"(?:Trillion|Billion|Million|Thousand|Crore|Lakh|Tn|Bn|Mn|Cr)\b",
        re.IGNORECASE,
    )
    sizes = [m.group(0) for m in size_pattern.finditer(description)]
    base_size = sizes[0] if sizes else ""
    forecast_size = sizes[1] if len(sizes) > 1 else ""

    first_line = next((ln.strip() for ln in description.splitlines() if ln.strip()), "")
    name_match = re.match(r"^(.*?\bMarket\b)", first_line, re.IGNORECASE)
    market_name = name_match.group(1).strip() if name_match else first_line[:80]

    return {
        "market_name": market_name,
        "base_year": base_year,
        "forecast_year": forecast_year,
        "base_size": base_size,
        "forecast_size": forecast_size,
        "cagr": cagr,
    }


# ─────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────

def generate_description(context: BrowserContext, report_url: str) -> dict:
    """Drive ChatGPT to produce a GBP description for report_url on a fresh
    chat, clean it up, and return {description, fields}."""
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    _ensure_fresh_chat(page)

    _submit_prompt(page, DESCRIPTION_PROMPT_TEMPLATE.format(report_url=report_url))
    print("Description prompt sent")
    description = _strip_cta_line(_strip_preamble(_wait_streaming_done(page, TEXT_TIMEOUT_S).strip()))
    print(f"Description received ({len(description)} chars)")

    # Too long: truncate deterministically (LLMs overshoot wildly if asked
    # to "shorten" — safer to trim mechanically, invents nothing).
    if len(description) > DESCRIPTION_MAX_CHARS:
        before = len(description)
        description = _truncate_to_fit(description)
        print(f"{before} chars over max, truncated to {len(description)}")

    # Too short: ask GPT to genuinely expand with more real content.
    for attempt in range(DESCRIPTION_LENGTH_RETRIES):
        length = len(description)
        if length >= DESCRIPTION_MIN_CHARS:
            break
        needed = DESCRIPTION_MIN_CHARS - length + 50
        print(f"{length} chars below {DESCRIPTION_MIN_CHARS}, asking GPT to expand (attempt {attempt + 1})")
        _submit_prompt(page, (
            f"Your description is only {length} characters — it must be at least "
            f"{DESCRIPTION_MIN_CHARS}. Expand it by adding roughly {needed} more characters "
            f"of additional real detail from the report (more segmentation, regional, competitive, "
            f"or scope detail), keeping the same structure, tone, and all rules from the original "
            f"instructions. Do not invent facts not on the report page. Output ONLY the corrected "
            f"description, nothing else."
        ))
        description = _strip_cta_line(_strip_preamble(_wait_streaming_done(page, TEXT_TIMEOUT_S).strip()))
        print(f"Expanded description received ({len(description)} chars)")
        if len(description) > DESCRIPTION_MAX_CHARS:
            description = _truncate_to_fit(description)
            print(f"Expansion overshot, truncated to {len(description)}")

    fields = _parse_description_fields(description)
    return {"description": description, "fields": fields}


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/description_generator.py <report_url>")
        sys.exit(1)
    report_url = sys.argv[1]

    with sync_playwright() as pw:
        proc, context = ensure_and_connect(
            pw, CHATGPT_PROFILE_DIR, CHATGPT_CDP_PORT, start_url="https://chatgpt.com/"
        )
        try:
            result = generate_description(context, report_url)
        finally:
            close_if_owned(proc)

    print("\n=== DESCRIPTION ===")
    print(result["description"])
    print("\n=== PARSED FIELDS ===")
    for k, v in result["fields"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
