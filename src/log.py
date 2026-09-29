"""Run logging: console + per-run file in logs/, screenshot-on-failure helper."""
import logging
from datetime import datetime

from . import config


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOGS_DIR / f"run-{datetime.now():%Y-%m-%d}.log"
    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def screenshot_on_failure(page, label: str) -> str | None:
    """Best-effort failure screenshot; returns the path or None."""
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:80]
        path = config.LOGS_DIR / f"fail-{datetime.now():%Y%m%d-%H%M%S}-{safe}.png"
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None
