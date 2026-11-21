"""Test browser_test.py against one hardcoded job URL."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BROWSER_SCRIPT = PROJECT_ROOT / "scripts" / "browser_test.py"

# Change this URL whenever you want to test a different job.
TEST_URL = "https://hiringcafe.com/job/solutions-engineer-blue-j-toronto-ontario-ik2t9uiws8g1nr8l?utm_source=claude&utm_medium=llm-app&utm_campaign=mcp"

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
