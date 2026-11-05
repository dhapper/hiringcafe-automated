"""
Reset the HiringCafe automation's generated job queue.

What this does:
- Creates a timestamped backup of data/jobs.json, if present.
- Replaces jobs.json with an empty queue.
- Does NOT access, modify, or delete applications.csv.
- Does NOT delete scripts, configuration, .env, or any other project files.

Run from the project root:
    python scripts/reset_data.py
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
JOBS_JSON = DATA_DIR / "jobs.json"
BACKUP_DIR = DATA_DIR / "backups"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def backup_if_exists(path: Path, timestamp: str) -> None:
    """Copy the jobs file to the backup folder before resetting it."""
    if not path.exists():
        logger.info("No existing file to back up: %s", path)
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{path.stem}_{timestamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    logger.info("Backed up %s -> %s", path, backup_path)


def main() -> None:
    logger.info("Starting reset of HiringCafe job queue.")
    logger.info("Project root: %s", PROJECT_ROOT)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_if_exists(JOBS_JSON, timestamp)

    jobs_payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "HiringCafe MCP",
        "job_count": 0,
        "jobs": [],
    }

    with JOBS_JSON.open("w", encoding="utf-8") as file:
        json.dump(jobs_payload, file, indent=2, ensure_ascii=False)
        file.write("\n")

    logger.info("Reset job queue: %s", JOBS_JSON)
    logger.info("Application tracker was NOT accessed or modified.")
    logger.info("Reset complete. Backup, if needed, is in: %s", BACKUP_DIR)
    logger.info(
        "Next step: run `python scripts/discover_jobs.py` to rebuild the queue."
    )


if __name__ == "__main__":
    main()
