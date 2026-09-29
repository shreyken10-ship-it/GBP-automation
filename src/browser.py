"""Real Chrome via CDP attach — not Playwright's bundled test Chromium.

We launch the actual chrome.exe as a plain subprocess (dedicated profile dir
under auth/, own debug port), then Playwright attaches to it over the Chrome
DevTools Protocol. Chrome wasn't started with Playwright's automation
switches, so it looks and behaves like a normal Chrome window — you can
watch it work, and it doesn't carry the "Chrome is being controlled by
automated test software" fingerprint a launch_persistent_context() session
would.

Session data (cookies, login) is written continuously to the profile dir by
Chrome itself, not just on clean exit, so killing the process is safe.
"""
import shutil
import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Playwright

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome_exe() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        return found
    raise RuntimeError(
        "Could not locate chrome.exe in standard install locations or PATH. "
        "Install Google Chrome, or hardcode its path in src/browser.py CHROME_CANDIDATES."
    )


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
    """Start a real Chrome process with a dedicated profile + debug port.
    Blocks until the debug port is accepting connections.

    Launched fully detached from our own process/job tree (Windows) so it
    keeps running independently of whatever shell or task wrapper started it
    — otherwise a background-task wrapper tearing down its process group can
    take Chrome down with it even though the user never closed the window.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        find_chrome_exe(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--remote-allow-origins=*",  # required on recent Chrome for CDP WS handshake
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    proc = subprocess.Popen(
        args,
        creationflags=_DETACHED_FLAGS,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 20
    while time.time() < deadline:
        if _port_open(port):
            return proc
        time.sleep(0.3)
    raise RuntimeError(f"Chrome did not open debug port {port} within 20s")


def connect(pw: Playwright, port: int) -> BrowserContext:
    """Attach to an already-running Chrome on the given debug port."""
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    return browser.contexts[0] if browser.contexts else browser.new_context()


def _port_owned_by_profile(profile_dir: Path, port: int) -> bool:
    """Is the listener on `port` OUR profile's Chrome (vs. some other
    automation tool squatting the port)?

    Primary: Chrome writes its active debug port to
    <user-data-dir>/DevToolsActivePort. Fallback: that file can go missing
    on OneDrive-synced profile dirs (observed live), so also scan running
    chrome.exe command lines for our exact port + profile-dir combination.
    """
    marker = profile_dir / "DevToolsActivePort"
    try:
        if int(marker.read_text().splitlines()[0].strip()) == port:
            return True
    except (OSError, ValueError, IndexError):
        pass

    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name = 'chrome.exe'\" "
                "| ForEach-Object { $_.CommandLine }",
            ],
            capture_output=True, text=True, timeout=20,
        ).stdout
        needle_port = f"--remote-debugging-port={port}"
        needle_profile = str(profile_dir)
        for line in out.splitlines():
            if needle_port in line and needle_profile in line:
                return True
    except Exception:
        pass
    return False


def ensure_and_connect(
    pw: Playwright, profile_dir: Path, port: int, start_url: str = "about:blank"
) -> tuple[subprocess.Popen | None, BrowserContext]:
    """Connect to Chrome on `port`, launching it first if nothing is listening
    there yet. Returns (proc, context) — proc is None if we attached to a
    Chrome we didn't start (so callers know not to kill it on cleanup).

    Refuses to attach to a listener that isn't our own profile's Chrome —
    other automation tools on this machine also run Chrome with CDP ports,
    and blindly attaching sends our prompts into the wrong browser
    (observed live on port 9222).
    """
    proc = None
    if not _port_open(port):
        proc = launch_chrome(profile_dir, port, start_url)
    elif not _port_owned_by_profile(profile_dir, port):
        raise RuntimeError(
            f"Port {port} is in use by a DIFFERENT Chrome (not profile {profile_dir.name}). "
            f"Another tool is squatting this port — close it, or change the port in .env "
            f"(CHATGPT_CDP_PORT / GBP_CDP_PORT)."
        )
    return proc, connect(pw, port)


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
