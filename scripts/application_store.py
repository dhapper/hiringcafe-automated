
import csv
import logging
import os
from datetime import datetime
from pathlib import Path

# Resolve paths from the project root, not the terminal's working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CSV_PATH = DATA_DIR / "applications.csv"
TEMP_PATH = DATA_DIR / "applications.csv.tmp"

logger = logging.getLogger(__name__)

FIELDS = [
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

FINAL_STATUSES = {"applied", "skipped"}
VALID_STATUSES = {
    "pending",
    "in_progress",
    "applied",
    "failed",
    "skipped",
}


def now_iso():
    """Return the current local time with its UTC offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_store():
    """Create the data directory and CSV ledger if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()

        logger.info("Created application ledger: %s", CSV_PATH)
    else:
        logger.info("Application ledger found: %s", CSV_PATH)


def load_applications():
    """Load the ledger as a dictionary keyed by job_id."""
    ensure_store()

    applications = {}

    with CSV_PATH.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        for row in reader:
            job_id = (row.get("job_id") or "").strip()

            if not job_id:
                logger.warning("Ignoring ledger row with no job_id.")
                continue

            if job_id in applications:
                raise ValueError(
                    f"Duplicate job_id {job_id!r} in {CSV_PATH}. "
                    "Fix the ledger before continuing."
                )

            applications[job_id] = row

    logger.info("Loaded %d application records.", len(applications))
    return applications


def save_applications(applications):
    """Write the complete ledger and replace the old file atomically."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with TEMP_PATH.open(
            "w", newline="", encoding="utf-8"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=FIELDS)
            writer.writeheader()

            for job_id in sorted(applications):
                row = applications[job_id]
                writer.writerow({
                    field: row.get(field, "")
                    for field in FIELDS
                })

            file.flush()
            os.fsync(file.fileno())

        os.replace(TEMP_PATH, CSV_PATH)
        logger.info("Saved %d records to the application ledger.",
                    len(applications))

    finally:
        if TEMP_PATH.exists():
            TEMP_PATH.unlink()


def register_job(job):
    """
    Add a discovered job as pending.

    If the job already exists, refresh its listing details without
    resetting its current status or application history.
    """
    job_id = str(job.get("job_id") or "").strip()

    if not job_id:
        raise ValueError("Cannot register a job without job_id.")

    applications = load_applications()
    current = applications.get(job_id)

    if current:
        logger.info(
            "Job already in ledger: %s - %s [%s]",
            current.get("company", ""),
            current.get("title", ""),
            job_id,
        )
        row = current.copy()
    else:
        row = {field: "" for field in FIELDS}
        row["job_id"] = job_id
        row["status"] = "pending"
        row["attempt_count"] = "0"
        row["discovered_at"] = job.get("discovered_at") or now_iso()

        logger.info("Registering new job: %s [%s]",
                    job.get("title", "Unknown title"), job_id)

    # Refresh listing metadata, preserving existing values when a
    # newly discovered listing does not provide a field.
    for field in (
        "title",
        "company",
        "location",
        "job_url",
        "posted_at",
        "discovered_at",
    ):
        value = job.get(field)
        if value not in (None, ""):
            row[field] = str(value)

    row["updated_at"] = now_iso()
    applications[job_id] = row
    save_applications(applications)

    return row.copy()


def get_application(job_id):
    """Return a single ledger record, or None if it is not present."""
    return load_applications().get(str(job_id))


def is_already_applied(job_id):
    """Check whether an application has been confirmed."""
    record = get_application(job_id)
    return bool(record and record.get("status") == "applied")


def should_skip_job(job_id):
    """
    Return True for jobs that must not be automatically attempted again.

    Failed jobs are not permanently excluded; retry policy will be
    controlled by the main pipeline.
    """
    record = get_application(job_id)

    return bool(
        record
        and record.get("status") in {"applied", "in_progress", "skipped"}
    )


def update_status(job_id, status, error=""):
    """Update a job's status and persist the change immediately."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")

    applications = load_applications()
    job_id = str(job_id)

    if job_id not in applications:
        raise KeyError(f"Job {job_id!r} is not registered in the ledger.")

    row = applications[job_id]
    timestamp = now_iso()

    row["status"] = status
    row["updated_at"] = timestamp
    row["last_error"] = str(error) if error else ""

    if status == "in_progress":
        try:
            attempts = int(row.get("attempt_count") or "0")
        except ValueError:
            attempts = 0

        row["attempt_count"] = str(attempts + 1)
        row["started_at"] = timestamp
        row["last_attempt_at"] = timestamp

    elif status == "applied":
        row["applied_at"] = timestamp

    applications[job_id] = row
    save_applications(applications)

    logger.info("Job %s status updated to '%s'.", job_id, status)


def mark_in_progress(job):
    """Register a job if needed, then record the start of an attempt."""
    row = register_job(job)

    if should_skip_job(row["job_id"]):
        logger.warning(
            "Not starting job %s because its current status is '%s'.",
            row["job_id"],
            row["status"],
        )
        return False

    update_status(row["job_id"], "in_progress")
    return True


def mark_applied(job_id):
    """Record a confirmed successful application."""
    update_status(job_id, "applied")
    logger.info("Application confirmed for job %s.", job_id)


def mark_failed(job_id, error):
    """Record a failed attempt without claiming the job was applied to."""
    update_status(job_id, "failed", error=error)
    logger.error("Application attempt failed for job %s: %s",
                 job_id, error)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    ensure_store()
    records = load_applications()

    print(f"\nLedger location: {CSV_PATH}")
    print(f"Existing records: {len(records)}")
    print("Application store initialized successfully.")
