"""Find a running native browser that Browser Harness's Linux profile scan would miss.

Browser Harness binds BU_CDP_WS when first imported, so call use_native_profile_endpoint() before any Browser.
"""

import os
import platform
import socket
import sys
from pathlib import Path

HARNESS_SCANNED_LINUX_PROFILES = (
    ".config/google-chrome",
    ".config/chromium",
    ".config/chromium-browser",
    ".config/microsoft-edge",
    ".config/microsoft-edge-beta",
    ".config/microsoft-edge-dev",
    ".var/app/org.chromium.Chromium/config/chromium",
    ".var/app/com.google.Chrome/config/google-chrome",
    ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    ".var/app/com.microsoft.Edge/config/microsoft-edge",
)
UNSCANNED_NATIVE_LINUX_PROFILES = (
    ".config/BraveSoftware/Brave-Browser",
    ".config/BraveSoftware/Brave-Browser-Beta",
    ".config/BraveSoftware/Brave-Browser-Nightly",
    ".config/google-chrome-beta",
    ".config/google-chrome-unstable",
    "snap/chromium/common/chromium",
)
PORT_PROBE_SECONDS = 0.3


def active_port(profile: Path) -> tuple[int, str] | None:
    """The (port, websocket path) a running browser wrote to DevToolsActivePort, if it is still listening."""
    try:
        lines = (profile / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()
        port, ws_path = int(lines[0].strip()), lines[1].strip()
    except (OSError, ValueError, IndexError):
        return None
    if not ws_path.startswith("/"):
        return None
    try:
        socket.create_connection(("127.0.0.1", port), timeout=PORT_PROBE_SECONDS).close()
    except OSError:
        return None
    return port, ws_path


def same_directory(first: Path, second: Path) -> bool:
    try:
        return first.samefile(second)
    except OSError:
        return False


def devtools_endpoint(home: Path | None = None, system: str | None = None) -> str | None:
    """The ws:// endpoint of a live native Brave/Chrome/Chromium the harness scan cannot see, else None.

    Returns None when an endpoint is already configured, off Linux, or when a scanned profile is live,
    so the harness keeps its own local mode (which waits patiently for the Allow prompt) whenever it can.
    """
    if os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL"):
        return None
    if (system or platform.system()) != "Linux":
        return None
    home = home or Path.home()
    scanned = [home / relative for relative in HARNESS_SCANNED_LINUX_PROFILES]
    if any(active_port(profile) for profile in scanned):
        return None
    for relative in UNSCANNED_NATIVE_LINUX_PROFILES:
        profile = home / relative
        if any(same_directory(profile, alias) for alias in scanned):
            continue
        if found := active_port(profile):
            port, ws_path = found
            return f"ws://127.0.0.1:{port}{ws_path}"
    return None


def use_native_profile_endpoint(home: Path | None = None, system: str | None = None) -> str | None:
    """Point this process's Browser Harness at an unscanned native browser; returns the endpoint it set."""
    if "browser_harness" in sys.modules:
        return None
    endpoint = devtools_endpoint(home, system)
    if endpoint:
        os.environ["BU_CDP_WS"] = endpoint
    return endpoint
