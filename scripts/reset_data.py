"""
Reset the HiringCafe automation's generated job queue and application tracker.

What this does:
- Creates timestamped backups of data/jobs.json and data/applications.csv, if present.
- Replaces jobs.json with an empty queue.
- Replaces applications.csv with an empty CSV containing the expected headers.
- Does NOT delete scripts, configuration, .env, or any other project files.

Run from the project root:
    python scripts/reset_data.py
"""

import csv
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
JOBS_JSON = DATA_DIR / "jobs.json"
APPLICATIONS_CSV = DATA_DIR / "applications.csv"
BACKUP_DIR = DATA_DIR / "backups"

CSV_FIELDS = [
    "job_id",
    "title",
    "company",
    "location",
    "job_url",
    "posted_at",
    "discovered_at",
    "status",
    "attempt_count",
    "started_at",
    "last_attempt_at",
    "applied_at",
    "updated_at",
    "last_error",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def backup_if_exists(path: Path, timestamp: str) -> None:
    """Copy a file to the backup folder before resetting it."""
    if not path.exists():
        logger.info("No existing file to back up: %s", path)
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{path.stem}_{timestamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    logger.info("Backed up %s -> %s", path, backup_path)


def main() -> None:
    logger.info("Starting reset of generated HiringCafe data.")
    logger.info("Project root: %s", PROJECT_ROOT)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_if_exists(JOBS_JSON, timestamp)
    backup_if_exists(APPLICATIONS_CSV, timestamp)

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

    with APPLICATIONS_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()

    logger.info("Reset application tracker: %s", APPLICATIONS_CSV)
    logger.info("Reset complete. Backups, if needed, are in: %s", BACKUP_DIR)
    logger.info("Next step: run `python scripts/discover_jobs.py` to rebuild the queue.")


if __name__ == "__main__":
    main()
