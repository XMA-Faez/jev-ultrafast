"""Launch a private Chrome for headless runs and tests; Browser Harness attaches through BU_CDP_URL."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import settings

STARTUP_SECONDS = 20


class LaunchedChrome:
    def __init__(self, process, profile, temporary_profile, daemon_name):
        self.process = process
        self.profile = profile
        self.temporary_profile = temporary_profile
        self.daemon_name = daemon_name

    def close(self):
        if self.process is None:
            return
        stop_harness_daemon(self.daemon_name)
        self.process.terminate()
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
        if self.temporary_profile:
            shutil.rmtree(self.profile, ignore_errors=True)


def stop_harness_daemon(daemon_name):
    """Ask our daemon to shut down and reap it.

    The daemon is our child process; Browser Harness's own shutdown polls the pid, which a
    zombie child keeps alive for its full 15-second grace period.
    """
    if "browser_harness.helpers" not in sys.modules:
        return
    from browser_harness import _ipc as ipc

    daemon_pid = ipc.identify(daemon_name, timeout=1.0)
    try:
        connection, token = ipc.connect(daemon_name, timeout=2.0)
        try:
            ipc.request(connection, token, {"meta": "shutdown"})
        finally:
            connection.close()
    except Exception:
        pass
    deadline = time.monotonic() + 5
    while daemon_pid and time.monotonic() < deadline:
        try:
            finished_pid, _ = os.waitpid(daemon_pid, os.WNOHANG)
        except ChildProcessError:
            break
        if finished_pid:
            break
        time.sleep(0.05)
    ipc.cleanup_endpoint(daemon_name)
    Path(ipc.pid_path(daemon_name)).unlink(missing_ok=True)


def harness_already_bound_elsewhere(daemon_name):
    helpers = sys.modules.get("browser_harness.helpers")
    return helpers is not None and helpers.NAME != daemon_name


def launch_chrome(chrome_path=None, profile_dir=None, headless=True):
    """Start Chrome with its own profile and point this process's Browser Harness at it.

    Browser Harness reads BU_NAME when first imported, so this must run before any Browser is created.
    """
    executable = chrome_path or settings.chrome_path()
    if not executable:
        raise RuntimeError("No Chrome or Chromium found. Set JEV_CHROME_PATH.")
    daemon_name = f"jev-{os.getpid()}"
    if harness_already_bound_elsewhere(daemon_name):
        raise RuntimeError("Browser Harness is already attached to another browser in this process.")
    temporary_profile = profile_dir is None
    profile = Path(tempfile.mkdtemp(prefix="jev-chrome-")) if temporary_profile else Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    port_file = profile / "DevToolsActivePort"
    port_file.unlink(missing_ok=True)
    arguments = [
        executable,
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "--window-size=1120,780",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "about:blank",
    ]
    if headless:
        arguments.insert(1, "--headless=new")
    process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if port_file.exists() and (lines := port_file.read_text().splitlines()):
            break
        if process.poll() is not None:
            raise RuntimeError(f"Chrome exited during startup with code {process.returncode}")
        time.sleep(0.05)
    else:
        process.kill()
        raise RuntimeError("Chrome did not publish a DevTools port")
    os.environ.update(
        BU_CDP_URL=f"http://127.0.0.1:{lines[0]}",
        BU_NAME=daemon_name,
        BH_TELEMETRY="0",
    )
    return LaunchedChrome(process, profile, temporary_profile, daemon_name)
