"""CTA link builder — appends the one fixed UTM string to every report URL."""
from . import config


def build_cta_link(report_url: str) -> str:
    """Strip any existing query string from the report URL, append the
    fixed campaign UTM (config.UTM_SUFFIX) — same for every post.
    """
    base_url = report_url.split("?")[0].split("#")[0].rstrip("/")
    return base_url + config.UTM_SUFFIX
