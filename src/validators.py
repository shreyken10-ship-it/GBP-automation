"""Pre-publish safety checks (PRD §6.4) — the stand-in for manual approval.

Pure functions, no I/O except the optional CTA reachability check.
"""
from urllib.parse import urlparse

import requests

from . import config

# Minimal magic-byte sniffing: PNG / JPEG / WEBP (RIFF)
_IMAGE_SIGNATURES = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")

# GBP's own composer rejects uploads below this — confirmed live via its
# "File size must be at least 10 KB" error. Catch it here so a bad image
# fails at generation time, not mid-batch during posting.
_IMAGE_MIN_BYTES = 10 * 1024


def check_description(description: str) -> tuple[bool, str]:
    if not description or not description.strip():
        return False, "Description is empty"
    length = len(description)
    if length < config.DESCRIPTION_MIN_CHARS or length > config.DESCRIPTION_MAX_CHARS:
        return False, (
            f"Description length {length} outside "
            f"{config.DESCRIPTION_MIN_CHARS}-{config.DESCRIPTION_MAX_CHARS} chars"
        )
    return True, ""


def check_market_fields(cagr: str, base_year: str, forecast_year: str) -> tuple[bool, str]:
    missing = [
        name
        for name, value in (("CAGR", cagr), ("base year", base_year), ("forecast year", forecast_year))
        if not (value or "").strip()
    ]
    if missing:
        return False, "Missing extracted fields: " + ", ".join(missing)
    return True, ""


def check_image(image_bytes: bytes) -> tuple[bool, str]:
    if not image_bytes:
        return False, "Image download produced zero bytes"
    if len(image_bytes) < _IMAGE_MIN_BYTES:
        return False, f"Image is only {len(image_bytes)} bytes — GBP requires at least {_IMAGE_MIN_BYTES}"
    if not any(image_bytes.startswith(sig) for sig in _IMAGE_SIGNATURES):
        return False, "Downloaded file is not a recognised image (PNG/JPEG/WEBP)"
    return True, ""


_SLUG_STOPWORDS = {"market", "vietnam", "and", "the", "for", "outlook"}


def check_market_match(report_url: str, description: str) -> tuple[bool, str]:
    """Catch a generated description for the WRONG market entirely — observed
    live (a stale/reused ChatGPT conversation produced a completely
    unrelated market's content). Compares significant words in the report
    URL's slug against the description's title line; requires at least one
    real overlap. This is a blunt but effective guard against publishing a
    factually wrong post — cheap insurance against a rare but serious
    failure mode.
    """
    slug = report_url.rstrip("/").split("/")[-1]
    # >=3 (not >3): short industry acronyms like "ICT", "IoT", "3PL" are
    # exactly the kind of distinctive word this check needs — a >3 cutoff
    # silently dropped them, producing a false-positive mismatch on a
    # genuinely correct row (observed live: "vietnam-ict-market...").
    slug_words = {w for w in slug.split("-") if len(w) >= 3 and w not in _SLUG_STOPWORDS}
    if not slug_words:
        return True, ""  # nothing distinctive to check against — don't block

    first_line = description.splitlines()[0] if description else ""
    title_words = {w.lower().strip("|,.:;&") for w in first_line.split()}
    if slug_words & title_words:
        return True, ""
    return False, (
        f"Description title {first_line!r} shares no significant words with "
        f"report URL slug {slug!r} — likely wrong-market content"
    )


def check_cta_link(cta_link: str, verify_reachable: bool = True) -> tuple[bool, str]:
    parsed = urlparse(cta_link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, f"CTA link malformed: {cta_link!r}"
    if verify_reachable:
        try:
            resp = requests.head(cta_link, timeout=15, allow_redirects=True)
            if resp.status_code >= 400:
                # Some servers reject HEAD; retry with GET before failing.
                resp = requests.get(cta_link, timeout=15, allow_redirects=True, stream=True)
                resp.close()
            if resp.status_code >= 400:
                return False, f"CTA link returned HTTP {resp.status_code}"
        except requests.RequestException as exc:
            return False, f"CTA link unreachable: {exc}"
    return True, ""


def check_generation(
    description: str,
    cagr: str,
    base_year: str,
    forecast_year: str,
    image_bytes: bytes,
    cta_link: str,
    report_url: str = "",
    verify_cta_reachable: bool = True,
) -> tuple[bool, str]:
    """Run all §6.4 checks; return (ok, reason). First failure wins."""
    for ok, reason in (
        check_description(description),
        check_market_fields(cagr, base_year, forecast_year),
        check_image(image_bytes),
        check_cta_link(cta_link, verify_cta_reachable),
        check_market_match(report_url, description) if report_url else (True, ""),
    ):
        if not ok:
            return False, reason
    return True, ""
