"""Stage 1 — Generation Agent.

Drives the existing ChatGPT image project via Playwright to produce the
infographic, uploads it to Cloudinary, then drives ChatGPT for the GBP
description, builds the UTM CTA link, validates (PRD §6.4), and writes
everything back to the sheet. One row's failure never stops the batch.

Prompts below are verbatim from the "GMB (Automation)" reference doc — this
module only automates how they get invoked (PRD §10).
"""
import base64
import re
import time
from datetime import date

from playwright.sync_api import BrowserContext, Page, sync_playwright

from . import cloudinary_client, config, pacing, utm, validators
from .log import get_logger, screenshot_on_failure
from .models import GenerationResult, SheetRow
from .sheets_client import SheetsClient

logger = get_logger("generation")

IMAGE_PROMPT_TEMPLATE = """You are a professional corporate market-research infographic designer for Ken Research.
Report URL (for background/topic context only — the market data below is already verified, do not fetch this page): {report_url}
Your task is to create one premium professional 16:9 market-size infographic image, similar in structure and style to the approved reference image.
Use the Ken Research logo image already provided by me. Do not recreate, redraw, recolor, stretch, distort, or replace the logo. Use the exact uploaded logo.
IMAGE STYLE REQUIRED
Create a clean, premium corporate infographic with:
Professional 16:9 layout
White and light grey corporate background
A realistic, subtle, high-quality background image or visual related to the market topic
A soft white transparent overlay on all text and chart areas for readability
Ken Research logo placed at the top-left
Large market title on the left
Subtitle below the title
One clean market size line graph only
CAGR callout inside the chart
Source line at the bottom: Source: Ken Research
The final image should look like a premium consulting or market research slide, not a cartoon, poster, or social media banner.
CRITICAL TEXT READABILITY AND NON-OVERLAP RULES
All text must be clearly readable and must not overlap with the background image, chart, markers, labels, logo, or any other visual element.
Do not place text directly on a busy or detailed background image.
Place every major text element inside a clean safe area with enough white space.
Use a soft translucent white layer behind the title, subtitle, chart, chart labels, CAGR callout, and source line wherever needed.
Maintain strong spacing between all elements:
Logo and title must not touch or overlap.
Title and subtitle must have clear vertical spacing.
Title area and chart card must not overlap.
Chart title, axis labels, data labels, and CAGR callout must not collide.
Source line must remain separate from the chart card and should not overlap the background image.
Keep a minimum safe margin of 6 percent from all outer edges of the image.
Keep at least 32 to 48 px spacing between the logo, title, subtitle, chart card, CAGR callout, and source line.
Use a clear visual hierarchy:
Logo at top-left
Title below or beside logo with enough breathing space
Subtitle directly below title
Chart card occupying the right or lower central area
Source at bottom-left
Before finalising the image, perform a visual collision check:
No text should touch another text element.
No text should touch the chart line or chart markers.
No market-size label should overlap another label.
No text should fall outside the safe margins.
No text should be hidden by the background image.
If any text looks crowded, reduce font size slightly, increase card size, or reposition the element. Do not compress or stack text tightly.
VERIFIED DATA — USE EXACTLY AS GIVEN, DO NOT FETCH OR RECALCULATE
Market name: {market_name}
Base year: {base_year}
Base year market size: {base_value}
Forecast year: {forecast_year}
Forecast year market size: {forecast_value}
Currency and unit: {currency_unit}
CAGR ({base_year}-{forecast_year}): {cagr}
These values are already verified from the report page. Do not open or re-read the report URL, do not re-derive, re-check, estimate, round differently, or alter any of these numbers in any way. Use them exactly as given, character for character.
Do not add extra market data, drivers, segments, regions, players, or insights unless they are explicitly needed for the background theme and visually represented without text.
MARKET SIZE GRAPH RULES
Create only one line graph.
The graph should show market size growth from the base year to the forecast year, using only the two verified values given above.
Plot exactly two points: the base year value and the forecast year value. Do not add any intermediate yearly points, and do not interpolate.
Do not add a CAGR bar graph.
Do not add key facts boxes.
Do not add growth insights boxes.
Do not add market highlights boxes.
Do not add segment cards.
Do not add any unrelated data points.
CAGR DISPLAY RULES
Display the CAGR value given above inside the chart in a clean callout box — it is already calculated and verified, do not recompute it.
CAGR (Base Year-Forecast Year)
X.XX%
Example format:
CAGR (2024-2030)
6.17%
CHART DESIGN RULES
The chart must be easy to read.
Use a clean line graph with circular markers.
Show market-size labels above each plotted point.
Use the currency and unit exactly as given above, such as USD Mn, USD Bn, INR Cr, etc.
Use a clean Y-axis label:
Market Size ([currency/unit])
Use the exact year labels from the verified data.
Use a minimal grid.
Use a professional burgundy or dark red line for the graph.
Keep the chart inside a rounded white translucent card.
Chart labels must be placed with enough spacing above markers.
If data labels collide with each other or with the line graph, reposition them slightly above, below, or beside the marker with a small leader line.
The CAGR callout should be placed inside an empty area of the chart, preferably bottom-right or mid-right, but only where it does not cover the chart line, markers, labels, or axis text.
BACKGROUND RULES
The background should change according to the report topic.
Use a realistic, professional, subtle background visual related to the market.
The background image should be softly blurred, faded, or partially covered with a white overlay so it does not compete with the text.
Crop the background image in a way that creates natural empty space for the title and chart.
Avoid placing faces, machinery details, vehicles, products, or busy objects directly behind the title, subtitle, chart labels, or source line.
Examples:
Pest Control Market: professional pest-control worker treating a modern office, hospital, commercial facility, or clean building interior. Do not show insects.
Healthcare Market: modern hospital, medical devices, doctor, lab, or clinical environment.
Agriculture Market: professional farm, greenhouse, irrigation, agri-tech, or clean crop environment.
Logistics Market: warehouse, trucks, port, supply chain, or distribution hub.
Automotive Market: EV charging, car manufacturing, service center, or road infrastructure.
Food Market: clean food processing unit, retail shelf, packaging line, or professional kitchen.
Technology Market: data center, digital interface, servers, cybersecurity, or smart devices.
The background should be subtle, not distracting.
Do not use cartoons, exaggerated illustrations, cluttered icons, or unrealistic visuals.
LAYOUT STRUCTURE
Use a balanced 16:9 layout with clear safe zones.
Top-left:
Ken Research logo with enough padding around it
Left side:
Main title: {market_name}
Subtitle: Market Size & Forecast, {base_year}-{forecast_year}
Main chart area:
Large white translucent rounded rectangle
Chart title: Market Size ([currency/unit])
Line graph: Verified market-size points only
CAGR callout: Placed inside the chart where it does not overlap with graph elements
Bottom-left:
Source: Ken Research
QUALITY REQUIREMENTS
The final image must be:
16:9 ratio
High resolution
Professional enough for LinkedIn, reports, presentations, and website use
Readable at normal screen size
Clean and uncluttered
Balanced with strong white space
Consistent with the approved reference image
STRICT DO NOT RULES
Do not hallucinate data.
Do not add values other than the verified data given above.
Do not interpolate annual values.
Do not include a CAGR graph.
Do not include extra cards or boxes.
Do not include key facts, growth insights, or market highlights sections.
Do not include insects in pest-control images.
Do not put Ken Research in the heading.
Do not use cartoon graphics.
Do not distort the Ken Research logo.
Do not place text directly over busy image areas.
Do not allow any text, labels, logo, source line, CAGR callout, or chart element to overlap.
Create the infographic image now."""

IMAGE_RETRY_MAX_ATTEMPTS = 2  # fresh chat each attempt; if GPT keeps replying with text instead of an image, skip the row

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

# #prompt-textarea id is gone as of a further ChatGPT UI update (verified
# live: 0 matches). The real composer is now identified by
# data-composer-markdown / aria-label="Ask ChatGPT" on its contenteditable
# div — deliberately NOT a bare div[contenteditable='true'], because
# ChatGPT's "Writing Block" component (see ASSISTANT_TURN below) renders its
# own contenteditable ProseMirror editor inline in the conversation
# (aria-label="Start writing", no data-composer-markdown attribute). A
# generic contenteditable selector matches THAT instead of the real composer
# whenever one is open, so a follow-up prompt gets typed into the dead
# writing-block editor and never actually sent — verified live as the cause
# of "expand" follow-ups silently landing as inert text inside a canvas
# instead of being submitted.
PROMPT_INPUT = (
    "#prompt-textarea, "
    "div[contenteditable='true'][data-composer-markdown], "
    "div[contenteditable='true'][aria-label='Ask ChatGPT']"
)
STOP_BUTTON = "button[data-testid='stop-button']"
# Multiple turn markups have existed across ChatGPT UI revisions — classic
# chat UI: div[data-message-author-role='assistant']; agent-turn UI (used by
# the "Create an image" quick-action flow): [data-turn='assistant']; current
# UI (verified live): [data-markdown-text-style='assistant-message']. Match
# all three — old ones are harmless no-ops if that markup is gone.
ASSISTANT_TURN = (
    "[data-markdown-text-style='assistant-message'], "
    "div[data-message-author-role='assistant'], [data-turn='assistant']"
)
# In the agent-turn UI the generated image is NOT inside the assistant turn:
# it lives in div#image-{uuid}.group/imagegen-image with alt="Generated
# image: ..." — verified live. Searched in addition to imgs inside turns.
GENERATED_IMAGE = "img[alt^='Generated image']"

IMAGE_TIMEOUT_S = 600   # DALL-E/gpt-image renders can take minutes; the logo-attach step adds real overhead on top
TEXT_TIMEOUT_S = 240
STABLE_S = 20           # response considered done after this long with no change
DESCRIPTION_LENGTH_RETRIES = 2
DESCRIPTION_SHRINK_RETRIES = 2
RATE_LIMIT_COOLDOWN_MS = 60000  # pause after dismissing the 'Too many requests' modal — it's tripped by our own navigation pace

# QA gate removed at the user's request — the image flow is a bare
# single-shot per attempt (see generate_for_row below). One nudge IS kept
# within an attempt: if the model replies with text instead of an image, it
# has sometimes skipped actually reading the report page (observed live:
# the resulting image had numbers that didn't match the real page). Re-
# pasting the exact URL and telling it explicitly not to approximate
# recovers this without burning a whole fresh-chat attempt.
def _currency_unit(value_str: str) -> str:
    """'USD 1,080 Mn' -> 'USD Mn'. Used to fill the image prompt's
    currency/unit field from an already-parsed size string."""
    m = re.match(r"^([A-Za-z$]+)\s*[\d,.]+\s*(.*)$", value_str.strip())
    if not m:
        return value_str.strip()
    return f"{m.group(1)} {m.group(2)}".strip()


IMAGE_URL_NUDGE_TEMPLATE = (
    "Re-fetch the data directly from this exact report URL: {report_url}. "
    "Use only the base year, base value, forecast year, forecast value, and "
    "unit exactly as stated on that page — do not approximate or estimate. "
    "Then create the infographic image now."
)

_CTA_LINE_RE = re.compile(r"^\s*CTA\s*:.*$\n?", re.IGNORECASE | re.MULTILINE)
_TITLE_LINE_RE = re.compile(r"^.*\bMarket\b.*\b(?:Size|Forecast)\b.*$", re.MULTILINE)


def _strip_preamble(text: str) -> str:
    """Reasoning models (e.g. gpt-5-5-thinking) prepend chatter above the
    actual content in the SAME message — "I'll verify the report page
    first...", "Thought for 1m 47s", "Verified data points include..." —
    all in the same turn our extraction reads. The approved prompt requires
    the description's title line to contain "Market" + "Size"/"Forecast",
    so anchor on that and drop everything before it, whatever the preamble
    phrasing happens to be. No-op if the title is already the first line."""
    match = _TITLE_LINE_RE.search(text)
    return text[match.start():].strip() if match else text.strip()


def _strip_cta_line(description: str) -> str:
    """The approved prompt has GPT literally write 'CTA: View Report' as text,
    but the CTA is a separate button+link in the actual GBP composer — having
    it duplicated in the post body is wrong. Always strip it, never rely on
    the prompt alone to omit it."""
    return _CTA_LINE_RE.sub("", description).strip()


def _truncate_to_fit(description: str) -> str:
    """Trim text over the max length down to it — deterministic and safe
    (removes text, invents nothing). Prefers cutting at a sentence boundary,
    falls back to a line boundary (bullets rarely end in periods), and
    falls back further to a hard cut at the limit — always guarantees the
    result fits, never a silent no-op when over the limit."""
    if len(description) <= config.DESCRIPTION_MAX_CHARS:
        return description
    truncated = description[: config.DESCRIPTION_MAX_CHARS]

    cut = max(truncated.rfind(". "), truncated.rfind(".\n"))
    if cut > config.DESCRIPTION_MIN_CHARS:
        return truncated[: cut + 1].strip()

    cut = truncated.rfind("\n")
    if cut > config.DESCRIPTION_MIN_CHARS:
        return truncated[:cut].strip()

    return truncated.strip()


def _shrink_to_max(page: Page, description: str, row_number) -> str:
    """If the description is over the max, ask GPT to trim it naturally
    instead of hard-truncating — a mechanical cutoff can land mid-sentence
    and read as incomplete. Mirrors the too-short expand loop below: give
    GPT a precise character target and ask it to trim the least essential
    detail while preserving structure and the closing line. Falls back to
    deterministic truncation only if GPT still overshoots after retries —
    the max-length cap must never be violated."""
    for attempt in range(DESCRIPTION_SHRINK_RETRIES):
        length = len(description)
        if length <= config.DESCRIPTION_MAX_CHARS:
            return description
        excess = length - config.DESCRIPTION_MAX_CHARS
        logger.info(
            "Row %s: description %d chars over max, asking GPT to trim (attempt %d)",
            row_number, excess, attempt + 1,
        )
        _submit_prompt(page, (
            f"Your description is {length} characters — it must be at most "
            f"{config.DESCRIPTION_MAX_CHARS}. Trim it by removing roughly {excess + 30} "
            f"characters of the least essential detail (e.g. shorten one bullet or the "
            f"intro slightly), keeping the same structure, tone, opening, closing line, "
            f"and all rules from the original instructions. Do not cut it off mid-sentence "
            f"— the result must read as complete. Output ONLY the corrected description, "
            f"nothing else."
        ))
        description = _strip_cta_line(_strip_preamble(_wait_streaming_done(page, TEXT_TIMEOUT_S).strip()))
        logger.info("Row %s: trimmed description received (%d chars)", row_number, len(description))

    if len(description) > config.DESCRIPTION_MAX_CHARS:
        before = len(description)
        description = _truncate_to_fit(description)
        logger.info("Row %s: still over max after retries, truncated %d -> %d", row_number, before, len(description))
    return description


class ChatGPTRateLimitedError(RuntimeError):
    """A 'too many requests' rate-limit modal is showing and could NOT be
    dismissed. Only raised after the dismiss-and-continue path fails —
    verified live that the 'Got it' variant closes cleanly and the account
    stays usable (an image even finished rendering WHILE the modal was up),
    so a dismissible modal no longer kills the batch. Callers should stop
    the whole batch only on this un-dismissible case."""


def _dismiss_lightbox(page: Page):
    """ChatGPT opens a fullscreen image-preview lightbox after an image
    renders (id="modal-lightbox-new") which intercepts all clicks, including
    the prompt box, until closed — observed live blocking every subsequent
    prompt in a row. Press Escape (its native close) if one is open.

    Also handles the account-level 'Too many requests' rate-limit modal.
    Two variants observed live: id="modal-conversation-history-rate-limit",
    and a text-matched dialog with a 'Got it' button. The modal is triggered
    by our navigation cadence (loading conversations too fast), NOT by image
    generation itself — generation completed fine while it was up. So:
    dismiss it ('Got it' click, then Escape as fallback), pause briefly to
    slow our cadence, and continue. Raise ChatGPTRateLimitedError only if
    it refuses to close.
    """
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
        logger.warning("Rate-limit modal showing — dismissing and pausing before continuing")
        try:
            gotit = page.get_by_role("button", name="Got it")
            if gotit.count() > 0:
                gotit.first.click(timeout=3000)
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")
        # Give the limiter breathing room — it's our own navigation pace
        # that trips it ("Please wait a few minutes before trying again").
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
        # Cold Chrome launch / heavy system load can leave the app not fully
        # loaded within budget — reload once and give it a second chance
        # rather than failing the whole row outright.
        logger.warning("Prompt box didn't appear in time, reloading and retrying once")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        box = page.locator(PROMPT_INPUT).first
        box.wait_for(state="visible", timeout=45000)
    _dismiss_lightbox(page)
    box.click()
    page.wait_for_timeout(int(pacing.action_pause() * 1000))
    # fill() instead of keystroke typing: prompts are ~4000 chars; per-key
    # typing would take minutes and adds nothing here (pacing rules apply to
    # the GBP posting flow, not ChatGPT).
    box.fill(prompt)
    page.wait_for_timeout(int(pacing.action_pause() * 1000))
    page.keyboard.press("Enter")


CREATE_IMAGE_BUTTON_TEXT = "Create image"
# data-testid='composer-plus-btn' is gone as of a further ChatGPT UI update
# (verified live: 0 matches). The button is now identified by
# data-composer-navigation-target="add-context" / aria-label="Add files and
# more" — no data-testid at all anymore.
COMPOSER_ATTACH_BUTTON = (
    "[data-composer-navigation-target='add-context'], "
    "button[aria-label='Add files and more']"
)


# ChatGPT has repeatedly changed which hidden <input type=file> is the
# right upload target — #upload-files (a plain id) is the one currently in
# use, but it has flipped to a React-generated id and back at least once
# before, and the aria-label has also changed. Try several known candidates
# in order rather than hardcoding one — whichever exists and isn't disabled
# wins, so a future rename doesn't require another live debugging session.
_UPLOAD_INPUT_CANDIDATES = (
    "input[type='file']#upload-files:not([disabled])",
    "input[type='file'][aria-label='Attach photos']:not([disabled])",
    "input[type='file']#upload-photos:not([disabled])",
)


def _attach_logo(page: Page):
    """Set the file directly on ChatGPT's hidden upload input instead of
    clicking through the '+' menu. ChatGPT now triggers 'Add photos & files'
    via the browser's File System Access API (showOpenFilePicker), which
    opens a real OS dialog that never fires Playwright's 'filechooser' event
    — this bypasses that entirely and is more robust regardless."""
    last_exc = None
    for selector in _UPLOAD_INPUT_CANDIDATES:
        upload_input = page.locator(selector).first
        try:
            upload_input.wait_for(state="attached", timeout=5000)
            upload_input.set_input_files(str(config.KEN_RESEARCH_LOGO_PATH))
            return
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"Could not find a usable logo-upload input (tried {_UPLOAD_INPUT_CANDIDATES}): {last_exc}")
    page.wait_for_timeout(3000)  # let the thumbnail attach before continuing


def _submit_image_prompt(page: Page, prompt_body: str):
    """Attach the logo file, then open the '+' attach dropdown again and click
    'Create image' — the homepage quick-action row of the same name was
    removed by OpenAI; the dropdown item is the only surviving entry point
    into image-gen mode (verified live). Clicking it turns the composer into
    a 'Create image' pill/tool-mode rather than inserting literal text, so
    prompt_body is inserted fresh after the pill, not appended to a prefix.

    Uses keyboard.insert_text (paste-like, instant) rather than box.fill() —
    per-key typing would take minutes for a ~3000+ char prompt."""
    _dismiss_lightbox(page)
    _attach_logo(page)
    _dismiss_lightbox(page)

    attach_btn = page.locator(COMPOSER_ATTACH_BUTTON).first
    attach_btn.wait_for(state="visible", timeout=15000)
    attach_btn.click()
    page.wait_for_timeout(500)
    create_img = page.get_by_text(CREATE_IMAGE_BUTTON_TEXT, exact=False).first
    create_img.wait_for(state="visible", timeout=8000)
    create_img.click()
    page.wait_for_timeout(800)
    _dismiss_lightbox(page)

    box = page.locator(PROMPT_INPUT).first
    box.click()
    page.wait_for_timeout(int(pacing.action_pause() * 1000))
    page.keyboard.insert_text(prompt_body)
    page.wait_for_timeout(int(pacing.action_pause() * 1000))
    page.keyboard.press("Enter")


_MAX_CONSECUTIVE_READ_FAILURES = 5  # ~15-40s of nothing but errors — treat as a dead page, not a flaky read


def _wait_streaming_done(page: Page, timeout_s: int) -> str:
    """Wait until the assistant reply stops changing; return its text.

    Same stability-loop idea as ken-llm-playwrightt's waitForStableResponse:
    poll the last assistant turn, done when text is non-empty and unchanged
    for STABLE_S and the stop (streaming) button is gone.
    """
    start = time.time()
    last_text = ""
    stable_since = time.time()
    consecutive_failures = 0

    while time.time() - start < timeout_s:
        page.wait_for_timeout(3000)
        try:
            turns = page.locator(ASSISTANT_TURN)
            current = turns.last.inner_text(timeout=5000) if turns.count() else ""
        except Exception as exc:
            # A single flaky read (element detached mid-reflow, etc.) is
            # harmless — the outer loop's timeout is the real deadline, so
            # normally just keep polling. But if EVERY read keeps failing,
            # the page itself is gone (Chrome crashed mid-run, verified live:
            # the debug port stopped accepting connections while this loop
            # kept silently swallowing every read for the full timeout,
            # burning ~4min/row and reporting a plain empty-description
            # failure instead of the real cause). Re-raise after repeated
            # back-to-back failures so callers' dead-browser reconnect logic
            # actually fires instead of masking it as "GPT said nothing."
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                raise RuntimeError(f"ChatGPT page stopped responding (target page, context or browser has been closed?): {exc}") from exc
            continue

        consecutive_failures = 0
        if current != last_text:
            last_text = current
            stable_since = time.time()
            continue

        streaming = page.locator(STOP_BUTTON).count() > 0
        if last_text.strip() and not streaming and time.time() - stable_since > STABLE_S:
            return last_text

    return last_text  # timeout: return whatever rendered; validators catch shortfalls


_MIN_IMAGE_DIM = 200  # px — filters out small UI icons/avatars that also render as <img>


def _find_generated_image(page: Page):
    """Return the locator for the actual generated image among all <img>
    tags inside assistant turns, filtering out small UI icons/avatars by
    rendered size (ChatGPT renders those as real <img> tags too, and a
    plain "last img in the turn" grab can land on one of those instead of
    the real artwork). Searches most-recent-first.

    Checks the agent-turn UI's imagegen container (img[alt^='Generated
    image'], rendered OUTSIDE any assistant turn) first, then falls back to
    imgs inside assistant turns for the classic chat UI."""
    turn_imgs = ", ".join(f"{sel.strip()} img" for sel in ASSISTANT_TURN.split(","))
    for selector in (GENERATED_IMAGE, turn_imgs):
        imgs = page.locator(selector)
        for i in range(imgs.count() - 1, -1, -1):
            candidate = imgs.nth(i)
            try:
                box = candidate.bounding_box()
            except Exception:
                continue
            if box and box["width"] >= _MIN_IMAGE_DIM and box["height"] >= _MIN_IMAGE_DIM:
                return candidate
    return None


def _ensure_fresh_chat(page: Page, max_attempts: int = 2):
    """Verify the page is a genuinely blank new conversation before
    submitting a prompt. page.goto("https://chatgpt.com/") alone is NOT a
    reliable guarantee of this — observed live: a prior row's conversation
    content bled into what should have been a fresh chat, producing a
    description for the wrong market entirely. If stale turns are present,
    force a hard reload and recheck rather than trusting the first load.

    Also clears any leftover DRAFT text sitting in the composer box. This is
    a separate failure mode from stale turns: if a previous row's prompt got
    typed into the box but never actually sent (e.g. the 'Create an image'
    click failed partway through), the new-chat composer restores that
    unsent draft on the next page load. ChatGPT hides the 'Create an image'
    quick-action whenever the composer isn't empty — verified live: this
    silently broke every subsequent row's image generation in a run, each
    failing with 'Create an image' never becoming visible, because the box
    still held the previous row's leftover prompt text."""
    for attempt in range(max_attempts):
        page.wait_for_timeout(1500)
        if page.locator(ASSISTANT_TURN).count() == 0:
            break
        logger.warning(
            "New chat still shows prior conversation turns — forcing reload (attempt %d)",
            attempt + 1,
        )
        page.goto("https://chatgpt.com/", wait_until="networkidle", timeout=60000)
    else:
        raise RuntimeError("Could not get a blank ChatGPT conversation — stale content persists after retries")

    box = page.locator(PROMPT_INPUT).first
    try:
        if box.count() > 0 and (box.inner_text() or "").strip():
            logger.warning("Composer had leftover draft text — clearing it")
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(300)
    except Exception:
        pass


def _last_assistant_text(page: Page) -> str:
    try:
        turns = page.locator(ASSISTANT_TURN)
        return turns.last.inner_text(timeout=5000) if turns.count() else ""
    except Exception:
        return ""


_IN_PROGRESS_PHRASES = ("hang tight", "generating", "thinking")


def _wait_for_image(page: Page) -> bytes | None:
    """Wait for the generated image to finish rendering, return its bytes.

    Returns None if the model finished its turn with a TEXT-ONLY response —
    e.g. it refused because the report page lacks the forecast value ("I
    can't create the requested infographic because..."). Callers decide
    whether to nudge or fail; waiting out the full timeout for a reply that
    already finished would burn ~8 minutes per such row for nothing.
    Raises only on hard timeout (nothing finished at all).
    """
    start = time.time()
    img_src = None
    last_text = ""
    text_stable_since = None

    while time.time() - start < IMAGE_TIMEOUT_S:
        page.wait_for_timeout(5000)
        _dismiss_lightbox(page)  # raises ChatGPTRateLimitedError immediately if the rate-limit modal shows up mid-poll, instead of burning the full timeout
        streaming = page.locator(STOP_BUTTON).count() > 0
        candidate = _find_generated_image(page)
        if candidate is None or streaming:
            # No image yet. If the turn has actually ENDED (not streaming)
            # and its text has been stable for a while, this is a completed
            # text-only reply, not a slow render — in-progress image
            # generation always shows a progressive-preview <img> which
            # _find_generated_image picks up, so "no candidate" + "stable
            # finished text" means no image is coming.
            if not streaming:
                current = _last_assistant_text(page)
                if current and current == last_text:
                    if any(p in current.lower() for p in _IN_PROGRESS_PHRASES):
                        # STOP_BUTTON's selector doesn't reliably catch the
                        # newer reasoning-model UI — verified live: it shows
                        # "Thinking / Generating a more detailed image —
                        # hang tight." which sits perfectly unchanged for
                        # well over 30s while genuinely still working, and
                        # was getting misread as a completed text-only
                        # refusal. Don't count this phrasing as "stable".
                        text_stable_since = None
                    elif text_stable_since is None:
                        text_stable_since = time.time()
                    elif time.time() - text_stable_since > 30:
                        return None
                else:
                    last_text = current
                    text_stable_since = None
            else:
                text_stable_since = None
            continue
        src = candidate.get_attribute("src") or ""
        # ChatGPT shows blurred progressive previews under the same element;
        # once streaming stops, give it a beat and re-read the final src.
        page.wait_for_timeout(8000)
        candidate = _find_generated_image(page) or candidate
        src_final = candidate.get_attribute("src") or src
        if src_final:
            img_src = src_final
            break

    if not img_src:
        raise RuntimeError("Image never appeared in ChatGPT response (timeout)")

    if img_src.startswith("http"):
        resp = page.context.request.get(img_src, timeout=60000)
        if not resp.ok:
            raise RuntimeError(f"Image fetch failed: HTTP {resp.status}")
        return resp.body()

    if img_src.startswith("data:"):
        # ChatGPT now renders the generated image as a data: URI directly
        # (verified live — no blob: URL at all anymore), often several MB of
        # base64 text. No browser round-trip needed: decode it directly.
        # The old blob: fetch-through-the-page approach below started
        # throwing "TypeError: Failed to fetch" on these — almost certainly
        # a page.evaluate() argument-size limit, not a real network error.
        _header, _, b64_data = img_src.partition(",")
        return base64.b64decode(b64_data)

    # blob: URL — pull bytes out through the page itself
    b64 = page.evaluate(
        """async (src) => {
            const r = await fetch(src);
            const buf = await r.arrayBuffer();
            let bin = "";
            const bytes = new Uint8Array(buf);
            for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
            return btoa(bin);
        }""",
        img_src,
    )
    return base64.b64decode(b64)


IMAGE_VALIDITY_CHECK_TEMPLATE = (
    "Look at the image you just generated above and check TWO things:\n"
    "1. Is it a real market-size line chart with two plotted points and a "
    "CAGR callout — not a fallback/placeholder saying data is missing, "
    "unavailable, or unverified (e.g. 'NO DATA AVAILABLE', 'NO VERIFIED "
    "MARKET SIZE DATA')?\n"
    "2. Do the chart's data labels show EXACTLY these values — "
    "{base_year} = {base_value}, {forecast_year} = {forecast_value}, "
    "CAGR = {cagr}? Any different number, even if it looks plausible, is a FAIL.\n"
    "Reply in EXACTLY this format, nothing else:\n"
    "STATUS: PASS or FAIL\n"
    "REASON: one short sentence"
)


def _check_image_valid(page: Page, *, base_year, base_value, forecast_year, forecast_value, cagr) -> tuple[bool, str]:
    """Two things this catches, verified live as real failure modes:
    (1) the model rendering a well-formed but completely different fallback
    card ('NO VERIFIED MARKET SIZE DATA AVAILABLE', no logo, no chart)
    instead of refusing in text — which passed _wait_for_image's 'is there
    an image' check and went straight to Cloudinary as if successful;
    (2) the model rendering a real-looking chart with numbers that don't
    match the verified data it was given. Intentionally narrow (one
    same-chat question right after the image renders) rather than the old
    multi-point QA gate — just enough to catch both before acceptance."""
    prompt = IMAGE_VALIDITY_CHECK_TEMPLATE.format(
        base_year=base_year, base_value=base_value,
        forecast_year=forecast_year, forecast_value=forecast_value, cagr=cagr,
    )
    _submit_prompt(page, prompt)
    response = _wait_streaming_done(page, TEXT_TIMEOUT_S)
    passed = bool(re.search(r"STATUS:\s*PASS", response, re.IGNORECASE))
    return passed, response.strip()[:300]


def _parse_description_fields(description: str) -> dict:
    """Best-effort extraction of sheet fields from the generated description.
    The prompt forces the model to state these, so plain regex is enough;
    validators reject the row if anything load-bearing is missing.
    """
    # Anchor on the intro's "valued at ... in <year> ... reach ... by <year>"
    # phrasing (required by the prompt) — the true base/forecast year, not
    # just the min/max of every year mentioned anywhere (which also catches
    # unrelated ranges like "historical coverage 2019-2024").
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


def generate_for_row(context: BrowserContext, row: SheetRow, sheets: SheetsClient) -> GenerationResult:
    page = context.pages[0] if context.pages else context.new_page()

    # --- description first (fresh chat) — this is what extracts the real,
    # verified numbers (base/forecast year, base/forecast size, CAGR). The
    # image is generated AFTER, using these exact numbers instead of asking
    # a second chat to independently fetch/recompute them — verified live
    # that letting the image chat fetch its own numbers produced wrong
    # values even when it rendered a well-formed image. ---
    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    _ensure_fresh_chat(page)
    _submit_prompt(page, DESCRIPTION_PROMPT_TEMPLATE.format(report_url=row.report_url))
    logger.info("Row %s: description prompt sent", row.row_number)
    description = _strip_cta_line(_strip_preamble(_wait_streaming_done(page, TEXT_TIMEOUT_S).strip()))
    logger.info("Row %s: description received (%d chars)", row.row_number, len(description))

    description = _shrink_to_max(page, description, row.row_number)

    for attempt in range(DESCRIPTION_LENGTH_RETRIES):
        length = len(description)
        if length >= config.DESCRIPTION_MIN_CHARS:
            break
        needed = config.DESCRIPTION_MIN_CHARS - length + 50  # small buffer over the floor
        logger.info(
            "Row %s: description %d chars below %d, asking GPT to expand (attempt %d)",
            row.row_number, length, config.DESCRIPTION_MIN_CHARS, attempt + 1,
        )
        _submit_prompt(page, (
            f"Your description is only {length} characters — it must be at least "
            f"{config.DESCRIPTION_MIN_CHARS}. Expand it by adding roughly {needed} more characters "
            f"of additional real detail from the report (more segmentation, regional, competitive, "
            f"or scope detail), keeping the same structure, tone, and all rules from the original "
            f"instructions. Do not invent facts not on the report page. Output ONLY the corrected "
            f"description, nothing else."
        ))
        description = _strip_cta_line(_strip_preamble(_wait_streaming_done(page, TEXT_TIMEOUT_S).strip()))
        logger.info("Row %s: expanded description received (%d chars)", row.row_number, len(description))
        description = _shrink_to_max(page, description, row.row_number)

    fields = _parse_description_fields(description)
    cta_link = utm.build_cta_link(row.report_url)

    # --- image (plain chat — no Project), using the verified numbers above
    # instead of asking this chat to fetch/recompute them. No QA gate: just
    # submit the prompt and take whatever comes back. Only check is "did an
    # image actually render" — if GPT replies with text instead, nudge once
    # with the exact URL restated, then retry with a fresh chat up to
    # IMAGE_RETRY_MAX_ATTEMPTS, then give up on the row. ---

    # Fail fast if description parsing didn't find the real numbers — verified
    # live: proceeding anyway feeds a prompt with blank verified-data fields,
    # which produced a garbled image (empty CAGR box, missing labels, a
    # literal "Edit" for the title) that still passed the validity check,
    # because that check's own expected values were also blank and thus
    # unfalsifiable. The row would fail overall validation regardless
    # (validators.check_generation catches missing fields) — better to catch
    # it here and skip the wasted image generation entirely.
    missing = [k for k in ("market_name", "base_year", "forecast_year", "base_size", "forecast_size", "cagr") if not fields.get(k)]
    if missing:
        return GenerationResult(
            success=False,
            error=f"Description field extraction incomplete, skipping image generation: missing {', '.join(missing)}",
            description=description,
        )

    image_prompt_kwargs = dict(
        report_url=row.report_url,
        market_name=fields["market_name"],
        base_year=fields["base_year"],
        forecast_year=fields["forecast_year"],
        base_value=fields["base_size"],
        forecast_value=fields["forecast_size"],
        currency_unit=_currency_unit(fields["base_size"]),
        cagr=fields["cagr"],
    )
    image_bytes = None
    refusal = ""
    for attempt in range(IMAGE_RETRY_MAX_ATTEMPTS):
        logger.info(
            "Row %s: opening ChatGPT for image generation (attempt %d/%d)",
            row.row_number, attempt + 1, IMAGE_RETRY_MAX_ATTEMPTS,
        )
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        _ensure_fresh_chat(page)
        _submit_image_prompt(page, IMAGE_PROMPT_TEMPLATE.format(**image_prompt_kwargs).split("\n", 1)[1])
        logger.info("Row %s: image prompt sent, waiting for render", row.row_number)

        image_bytes = _wait_for_image(page)
        if image_bytes is None:
            refusal = _last_assistant_text(page).strip()
            logger.warning(
                "Row %s: attempt %d got text instead of an image (%.150s...) — nudging with exact URL",
                row.row_number, attempt + 1, refusal,
            )
            _submit_prompt(page, IMAGE_URL_NUDGE_TEMPLATE.format(report_url=row.report_url))
            image_bytes = _wait_for_image(page)
            if image_bytes is None:
                refusal = _last_assistant_text(page).strip()
                logger.warning(
                    "Row %s: attempt %d nudge also got text instead of an image (%.150s...)",
                    row.row_number, attempt + 1, refusal,
                )
                continue

        # An <img> rendering isn't proof it's the RIGHT image, or that its
        # numbers are correct — observed live: (1) a well-formed but
        # completely different fallback card ("NO VERIFIED MARKET SIZE DATA
        # AVAILABLE", no logo, no chart), and (2) a real-looking chart with
        # numbers that didn't match the verified data. Both passed the "is
        # there an image" check above and would've gone straight to
        # Cloudinary as if successful without this.
        valid, reason = _check_image_valid(
            page,
            base_year=fields["base_year"], base_value=fields["base_size"],
            forecast_year=fields["forecast_year"], forecast_value=fields["forecast_size"],
            cagr=fields["cagr"],
        )
        if valid:
            break
        logger.warning("Row %s: attempt %d failed image validity check: %s — retrying fresh", row.row_number, attempt + 1, reason)
        image_bytes = None
        refusal = f"image failed validity check: {reason}"

    if image_bytes is None:
        raise RuntimeError(
            f"Image not generated after {IMAGE_RETRY_MAX_ATTEMPTS} attempts — model returned text only: {refusal[-250:]}"
        )

    logger.info("Row %s: image downloaded (%d bytes)", row.row_number, len(image_bytes))

    image_url, _public_id = cloudinary_client.upload_image_bytes(
        image_bytes, folder=f"gbp-posts/{date.today():%Y-%m-%d}"
    )
    logger.info("Row %s: uploaded to Cloudinary → %s", row.row_number, image_url)
    # Write it back immediately — if a later step crashes or fails validation,
    # this slow (~minutes) step's result is never silently lost.
    sheets.update_row(row.row_number, **{
        "Image Status": "Generated",
        "Image Link (Cloudinary)": image_url,
    })

    ok, reason = validators.check_generation(
        description=description,
        cagr=fields["cagr"],
        base_year=fields["base_year"],
        forecast_year=fields["forecast_year"],
        image_bytes=image_bytes,
        cta_link=cta_link,
        report_url=row.report_url,
    )
    if not ok:
        return GenerationResult(success=False, error=f"Validation failed: {reason}",
                                description=description, image_url=image_url)

    return GenerationResult(
        success=True,
        market_name=fields["market_name"],
        base_year=fields["base_year"],
        forecast_year=fields["forecast_year"],
        base_market_size=fields["base_size"],
        forecast_market_size=fields["forecast_size"],
        cagr=fields["cagr"],
        description=description,
        image_bytes=image_bytes,
        image_url=image_url,
        cta_link=cta_link,
    )


_DEAD_BROWSER_SIGNATURES = (
    "target page, context or browser has been closed",
    "targetclosederror",
    "connect econnrefused",
)


def _is_dead_browser_error(exc: Exception) -> bool:
    return any(sig in str(exc).lower() for sig in _DEAD_BROWSER_SIGNATURES)


def run_batch(sheets: SheetsClient) -> dict:
    rows = sheets.get_pending_rows()[: config.BATCH_SIZE_MAX]
    logger.info("Generation stage: %d pending row(s)", len(rows))
    summary = {"total": len(rows), "generated": 0, "failed": 0}
    if not rows:
        return summary

    with sync_playwright() as pw:
        from .browser import close_if_owned, ensure_and_connect

        proc, context = ensure_and_connect(
            pw, config.CHATGPT_PROFILE_DIR, config.CHATGPT_CDP_PORT, start_url="https://chatgpt.com/"
        )
        try:
            for row in rows:
                try:
                    result = generate_for_row(context, row, sheets)
                except ChatGPTRateLimitedError:
                    # Doesn't clear within a run — every remaining row would
                    # hit the identical wall. Stop the whole batch now rather
                    # than burning navigation + wait time on doomed attempts;
                    # this row and all after it are left untouched (still
                    # Pending) for a later run once the limit clears.
                    logger.warning(
                        "Row %s: ChatGPT rate limit hit — stopping batch, %d row(s) left untouched",
                        row.row_number, len(rows) - summary["generated"] - summary["failed"],
                    )
                    break
                except Exception as exc:  # per-row isolation (FR-9)
                    # If the shared browser/context died mid-batch (crash,
                    # stuck popup, etc.), every remaining row would otherwise
                    # fail identically since they all reuse the same dead
                    # context — reconnect once and retry this row instead of
                    # letting one bad moment take out the rest of the batch.
                    if _is_dead_browser_error(exc):
                        logger.warning(
                            "Row %s: browser/context appears dead (%s) — reconnecting and retrying once",
                            row.row_number, exc,
                        )
                        try:
                            close_if_owned(proc)
                        except Exception:
                            pass
                        try:
                            proc, context = ensure_and_connect(
                                pw, config.CHATGPT_PROFILE_DIR, config.CHATGPT_CDP_PORT,
                                start_url="https://chatgpt.com/",
                            )
                            result = generate_for_row(context, row, sheets)
                        except Exception as exc2:
                            logger.exception("Row %s: generation crashed even after reconnect", row.row_number)
                            page = context.pages[0] if context.pages else None
                            if page:
                                screenshot_on_failure(page, f"gen-row{row.row_number}")
                            try:
                                sheets.mark_generation_failed(row.row_number, f"Generation error (after reconnect): {exc2}")
                            except Exception:
                                logger.exception("Row %s: could not record failure in sheet", row.row_number)
                            summary["failed"] += 1
                            continue
                    else:
                        logger.exception("Row %s: generation crashed", row.row_number)
                        page = context.pages[0] if context.pages else None
                        if page:
                            screenshot_on_failure(page, f"gen-row{row.row_number}")
                        try:
                            sheets.mark_generation_failed(row.row_number, f"Generation error: {exc}")
                        except Exception:
                            logger.exception("Row %s: could not record failure in sheet", row.row_number)
                        summary["failed"] += 1
                        continue

                # Status writes must not kill the loop — a Sheets outage on
                # one row shouldn't abandon every remaining row (FR-9).
                try:
                    if result.success:
                        sheets.update_row(
                            row.row_number,
                            **{
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
                            },
                        )
                        logger.info("Row %s: Generated ✓", row.row_number)
                    else:
                        partial = {"Batch Status": "Failed", "Error / Notes": result.error or "Unknown failure"}
                        if result.description:
                            partial["GBP Description"] = result.description
                        sheets.update_row(row.row_number, **partial)
                        logger.warning("Row %s: Failed — %s", row.row_number, result.error)
                except Exception:
                    logger.exception("Row %s: SHEET WRITE FAILED — record row state manually", row.row_number)
                summary["generated" if result.success else "failed"] += 1
        finally:
            close_if_owned(proc)

    return summary
