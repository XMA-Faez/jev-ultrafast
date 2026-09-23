"""Run the browser guard and snapshot tests against the user's running Chrome. No model calls."""

import sys
from pathlib import Path

import pytest

BROWSER_TESTS = Path(__file__).resolve().parent.parent / "tests" / "browser"

if __name__ == "__main__":
    sys.exit(pytest.main([str(BROWSER_TESTS), "--real-browser", "-q", *sys.argv[1:]]))
