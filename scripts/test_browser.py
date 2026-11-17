"""Test browser_test.py against one hardcoded job URL."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BROWSER_SCRIPT = PROJECT_ROOT / "scripts" / "browser_test.py"

# Change this URL whenever you want to test a different job.
TEST_URL = "https://jobs.workable.com/view/3SAt5nWNWeHxLgNKcRpgHZ/remote-software-engineer-(4-month-contract)-in-toronto-at-fable"

subprocess.run(
    [
        sys.executable,
        str(BROWSER_SCRIPT),
        "--url",
        TEST_URL,
        "--wait-for-review",
    ],
    cwd=str(PROJECT_ROOT),
    check=True,
)
