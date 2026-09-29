"""One-time login setup for the dedicated Chrome CDP profiles.

Launches real Chrome (not a Playwright test browser) pointed at a dedicated
profile folder under auth/. You log in by hand, then close the window —
Chrome writes the session to disk continuously, so every future run reuses
it. No credentials ever live in code or .env.

Usage:
    python scripts/login_setup.py chatgpt   # log into chatgpt.com
    python scripts/login_setup.py gbp       # log into business.google.com
    python scripts/login_setup.py           # both, one after the other
"""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.browser import launch_chrome

GBP_SEARCH_URL = f"https://www.google.com/search?q={urllib.parse.quote(config.GBP_SEARCH_QUERY)}"

TARGETS = {
    "chatgpt": (config.CHATGPT_PROFILE_DIR, config.CHATGPT_CDP_PORT, "https://chatgpt.com/"),
    "gbp": (config.GBP_PROFILE_DIR, config.GBP_CDP_PORT, GBP_SEARCH_URL),
}


def setup(name: str):
    profile_dir, port, url = TARGETS[name]
    print(f"\n=== {name} ===")
    print(f"Launching real Chrome (dedicated profile) -> {url}")
    proc = launch_chrome(profile_dir, port, start_url=url)
    print("Log in manually in the window that just opened, verify you land")
    print("on the authenticated page, then CLOSE the browser window to finish.")
    proc.wait()  # blocks until the Chrome window is closed
    print(f"[{name}] Session saved to {profile_dir}")


def main():
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "both"
    if which == "both":
        setup("chatgpt")
        setup("gbp")
    elif which in TARGETS:
        setup(which)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
