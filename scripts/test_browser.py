"""Test browser_test.py against one hardcoded job URL."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BROWSER_SCRIPT = PROJECT_ROOT / "scripts" / "browser_test.py"

# Change this URL whenever you want to test a different job.
TEST_URL = "https://hiringcafe.com/job/qa-automation-tester-flynn-group-of-companies-mississauga-ontario-0emj38xyd6tl6ksh?utm_source=claude&utm_medium=llm-app&utm_campaign=mcp"

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
