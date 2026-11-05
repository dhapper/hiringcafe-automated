"""
Refresh HiringCafe job listings within the configured posting window.

- Uses POSTED_WITHIN_DAYS from config.py.
- Removes existing jobs outside the configured posting window.
- Adds new matching jobs and refreshes existing listings.
- Preserves application-tracking fields for retained jobs.
- Creates timestamped backups before saving.
- Does NOT access applications.csv.

Run from the project root:
    python scripts/refresh_daily_jobs.py
"""

import hashlib
import json
import logging
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from config import (
        DATA_DIR,
        JOBS_JSON,
        HIRINGCAFE_MCP_URL,
        SEARCH_QUERIES,
        LOCATION,
        WORKPLACE_TYPES,
        EMPLOYMENT_TYPES,
        SENIORITY_LEVELS,
        MAX_YEARS_EXPERIENCE,
        POSTED_WITHIN_DAYS,
        RESULTS_PER_QUERY,
        REQUEST_TIMEOUT_SECONDS,
    )
except ImportError as exc:
    raise SystemExit(f"Could not import config.py: {exc}") from exc


TZ = ZoneInfo("America/Toronto")
BACKUP_DIR = DATA_DIR / "backups"
DEBUG_RESPONSE_PATH = DATA_DIR / "debug_hiringcafe_response.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("hiringcafe_daily_refresh")


def validate_config():
    if not isinstance(POSTED_WITHIN_DAYS, int) or POSTED_WITHIN_DAYS < 1:
        raise ValueError("POSTED_WITHIN_DAYS must be an integer of at least 1.")

    if not SEARCH_QUERIES:
        raise ValueError("SEARCH_QUERIES cannot be empty.")


def mcp_request(payload):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    response = requests.post(
        HIRINGCAFE_MCP_URL,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    response.raise_for_status()
    body = response.text.strip()

    if not body:
        raise RuntimeError("HiringCafe MCP returned an empty response.")

    try:
        data = response.json()

        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(f"MCP error: {data['error']}")
            return data

    except ValueError:
        pass

    for line in body.splitlines():
        if not line.startswith("data:"):
            continue

        content = line[5:].strip()

        if not content or content == "[DONE]":
            continue

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(f"MCP error: {data['error']}")

            if "id" not in payload or data.get("id") == payload.get("id"):
                return data

    raise RuntimeError(f"Could not parse MCP response: {body[:800]}")


def validate_mcp_connection():
    response = mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        }
    )

    tools = response.get("result", {}).get("tools", [])
    names = {tool.get("name") for tool in tools}

    log.info("Available tools: %s", ", ".join(sorted(names)))

    if "search_jobs" not in names:
        raise RuntimeError("HiringCafe MCP does not expose search_jobs.")


def extract_text(response):
    result = response.get("result", {})

    if result.get("isError"):
        raise RuntimeError(f"MCP search failed: {result}")

    content = result.get("content", [])

    text_parts = [
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    ]

    if text_parts:
        return "\n".join(text_parts)

    structured = result.get("structuredContent")

    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)

    raise RuntimeError("MCP response contained no readable content.")


def search_jobs(query):
    arguments = {
        "search": query,
        "location": LOCATION,
        "workplace_types": WORKPLACE_TYPES,
        "employment_types": EMPLOYMENT_TYPES,
        "seniority_levels": SENIORITY_LEVELS,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "limit": RESULTS_PER_QUERY,
    }

    if MAX_YEARS_EXPERIENCE is not None:
        arguments["max_years_experience"] = MAX_YEARS_EXPERIENCE

    response = mcp_request(
        {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 2_000_000_000,
            "method": "tools/call",
            "params": {
                "name": "search_jobs",
                "arguments": arguments,
            },
        }
    )

    return extract_text(response)


def extract_hiringcafe_job_id(job_url):
    if not job_url:
        return None

    path = job_url.split("?", 1)[0].rstrip("/")
    candidate = path.rsplit("/", 1)[-1]

    match = re.search(r"-([a-zA-Z0-9]{12,})$", candidate)

    if match:
        return match.group(1)

    return candidate or None


def extract_labeled_value(block, labels):
    label_pattern = "|".join(re.escape(label) for label in labels)

    pattern = re.compile(
        rf"(?im)^\s*(?:[-*]\s*)?"
        rf"(?:\*\*)?(?:{label_pattern})(?:\*\*)?"
        rf"\s*:?\s*(?:\*\*)?\s*"
        rf"(.+?)\s*$"
    )

    match = pattern.search(block)

    if not match:
        return None

    value = re.sub(r"\s+", " ", match.group(1).strip())
    return value.strip("* ").strip() or None


def parse_job_blocks(result_text):
    heading_pattern = re.compile(
        r"(?m)^#{2,4}\s+"
        r"\[(?P<title>[^\]]+)\]"
        r"\((?P<url>https?://[^)\s]+)\)"
        r"(?P<tail>.*)$"
    )

    matches = list(heading_pattern.finditer(result_text))
    jobs = []

    for index, match in enumerate(matches):
        block_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(result_text)
        )

        block = result_text[match.start() : block_end]

        title = match.group("title").strip()
        url = match.group("url").strip().rstrip(".,")

        tail = match.group("tail")
        company_match = re.search(r"\*\*(.*?)\*\*", tail)

        company = company_match.group(1).strip() if company_match else None

        job_id = extract_hiringcafe_job_id(url)

        if not job_id:
            job_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

        location = extract_labeled_value(
            block,
            ["Location", "Job Location", "Locations"],
        )

        posted_text = extract_labeled_value(
            block,
            [
                "Posted",
                "Date Posted",
                "Posted At",
                "Posting Date",
                "Date",
            ],
        )

        jobs.append(
            {
                "job_id": str(job_id),
                "title": title,
                "company": company or "Unknown",
                "location": location or "Not specified",
                "job_url": url,
                "posted_at_text": posted_text,
            }
        )

    return jobs


def parse_posted_date(value, today):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    normalized = re.sub(
        r"[\s,]+",
        " ",
        text.casefold(),
    ).strip()

    if normalized in {
        "today",
        "posted today",
        "just posted",
        "just now",
        "moments ago",
    }:
        return today

    if normalized in {
        "yesterday",
        "posted yesterday",
    }:
        return today - timedelta(days=1)

    if re.fullmatch(
        r"(?:posted\s+)?(?:\d+\s*)?"
        r"(?:minute|minutes|min|mins|hour|hours|hr|hrs)"
        r"\s+ago",
        normalized,
    ):
        return today

    relative_match = re.fullmatch(
        r"(?:posted\s+)?(\d+)\s+days?\s+ago",
        normalized,
    )

    if relative_match:
        days_ago = int(relative_match.group(1))
        return today - timedelta(days=days_ago)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

        if parsed.tzinfo:
            parsed = parsed.astimezone(TZ)

        return parsed.date()

    except ValueError:
        pass

    formats = (
        "%b %d, %Y",
        "%B %d, %Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%b %d",
        "%B %d",
    )

    for fmt in formats:
        try:
            parsed_date = datetime.strptime(text, fmt).date()

            if "%Y" not in fmt:
                parsed_date = parsed_date.replace(year=today.year)

                if parsed_date > today:
                    parsed_date = parsed_date.replace(year=today.year - 1)

            return parsed_date

        except ValueError:
            continue

    return None


def is_within_posting_window(posted_date, today):
    if posted_date is None:
        return False

    cutoff_date = today - timedelta(days=POSTED_WITHIN_DAYS - 1)

    return cutoff_date <= posted_date <= today


def load_existing(path):
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)

    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read {path}: {exc}. " "Existing data was left unchanged."
        ) from exc

    jobs = data.get("jobs", []) if isinstance(data, dict) else []

    existing = {}

    for item in jobs:
        if isinstance(item, dict) and item.get("job_id"):
            existing[str(item["job_id"])] = item

    return existing


def merge_job(old, new):
    if old is None:
        result = dict(new)

        result.update(
            {
                "status": "discovered",
                "application_status": "not_started",
                "last_attempt_at": None,
                "submitted_at": None,
                "notes": [],
            }
        )

        return result

    result = dict(old)

    for key, value in new.items():
        if value not in (None, "", [], {}):
            result[key] = value

    result.setdefault("status", "discovered")
    result.setdefault("application_status", "not_started")
    result.setdefault("last_attempt_at", None)
    result.setdefault("submitted_at", None)
    result.setdefault("notes", [])

    return result


def backup_jobs(path):
    if not path.exists():
        log.info("No existing jobs.json to back up.")
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"jobs_{timestamp}.json"

    shutil.copy2(path, backup_path)

    log.info("Backed up existing jobs: %s", backup_path)


def save_jobs(path, output):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as file:
            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")

        temp_path.replace(path)

    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    validate_config()

    now = datetime.now(TZ)
    today = now.date()
    discovered_at = now.isoformat(timespec="seconds")

    cutoff_date = today - timedelta(days=POSTED_WITHIN_DAYS - 1)

    output_path = Path(JOBS_JSON)

    log.info("=" * 65)
    log.info("Starting HiringCafe job refresh.")
    log.info("Toronto date: %s", today.isoformat())
    log.info("Search location: %s", LOCATION)
    log.info("Configured posting window: %d day(s)", POSTED_WITHIN_DAYS)
    log.info(
        "Accepted posting dates: %s through %s",
        cutoff_date.isoformat(),
        today.isoformat(),
    )
    log.info("Search terms: %s", ", ".join(SEARCH_QUERIES))
    log.info("JSON output: %s", output_path)
    log.info("Application tracker will NOT be accessed.")
    log.info("=" * 65)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        validate_mcp_connection()
        existing = load_existing(output_path)

    except Exception as exc:
        log.error("Initialization failed: %s", exc)
        return 1

    log.info("Loaded %d existing job record(s).", len(existing))

    candidates = {}
    failed_searches = 0
    parsed_listing_count = 0
    rejected_date_count = 0
    unknown_date_count = 0

    for index, query in enumerate(SEARCH_QUERIES, start=1):
        log.info(
            "Searching %d/%d: %s",
            index,
            len(SEARCH_QUERIES),
            query,
        )

        try:
            result_text = search_jobs(query)

            if index == 1:
                DEBUG_RESPONSE_PATH.write_text(
                    result_text,
                    encoding="utf-8",
                )

                log.info(
                    "Saved raw response: %s",
                    DEBUG_RESPONSE_PATH,
                )

                log.info(
                    "Raw response preview:\n%s",
                    result_text[:5000],
                )

            parsed = parse_job_blocks(result_text)
            parsed_listing_count += len(parsed)

            log.info(
                "Parsed %d listing(s) from Markdown.",
                len(parsed),
            )

            if not parsed:
                log.warning(
                    "No job headings parsed for '%s'. Response:\n%s",
                    query,
                    result_text[:1200],
                )

            for job in parsed:
                raw_posted = job.pop("posted_at_text", None)
                posted_date = parse_posted_date(raw_posted, today)

                job["posted_at_text"] = raw_posted
                job["posted_at"] = posted_date.isoformat() if posted_date else None
                job["discovered_at"] = discovered_at
                job["source"] = "HiringCafe"

                if posted_date is None:
                    unknown_date_count += 1

                    log.warning(
                        "Skipping unknown posting date: %s | %r",
                        job["title"],
                        raw_posted,
                    )
                    continue

                if not is_within_posting_window(posted_date, today):
                    rejected_date_count += 1

                    log.debug(
                        "Outside posting window: %s | %s",
                        job["title"],
                        posted_date.isoformat(),
                    )
                    continue

                job_id = job["job_id"]

                candidates[job_id] = merge_job(
                    candidates.get(job_id),
                    job,
                )

        except Exception as exc:
            failed_searches += 1
            log.exception(
                "Search failed for '%s': %s",
                query,
                exc,
            )

        if index < len(SEARCH_QUERIES):
            time.sleep(0.5)

    log.info("=" * 65)
    log.info("SEARCH SUMMARY")
    log.info("Total parsed listings: %d", parsed_listing_count)
    log.info("Rejected outside date window: %d", rejected_date_count)
    log.info("Skipped unknown posting dates: %d", unknown_date_count)
    log.info("Unique jobs within configured window: %d", len(candidates))
    log.info("Failed searches: %d", failed_searches)
    log.info("=" * 65)

    if failed_searches == len(SEARCH_QUERIES):
        log.error("Every search failed. Existing jobs.json was unchanged.")
        return 1

    if parsed_listing_count == 0:
        log.error("No listings could be parsed. Existing jobs.json was unchanged.")
        return 1

    if not candidates:
        log.warning(
            "No jobs matched the configured %d-day posting window.",
            POSTED_WITHIN_DAYS,
        )
        log.warning("Existing jobs.json was left unchanged.")
        return 0

    # Retain only existing jobs within the configured posting window.
    # Unknown or missing posting dates are excluded.
    combined = {}

    removed = 0

    for job_id, job in existing.items():
        existing_date = parse_posted_date(
            job.get("posted_at"),
            today,
        )

        if is_within_posting_window(existing_date, today):
            combined[job_id] = job
        else:
            removed += 1

    log.info(
        "Removed %d existing job(s) outside the posting window.",
        removed,
    )

    added = 0
    refreshed = 0

    for job_id, fresh in candidates.items():
        if job_id in combined:
            combined[job_id] = merge_job(
                combined[job_id],
                fresh,
            )
            refreshed += 1
        else:
            combined[job_id] = merge_job(None, fresh)
            added += 1

    sorted_jobs = sorted(
        combined.values(),
        key=lambda item: (
            str(item.get("posted_at") or ""),
            str(item.get("discovered_at") or ""),
            str(item.get("company") or "").casefold(),
            str(item.get("title") or "").casefold(),
        ),
        reverse=True,
    )

    output = {
        "updated_at": discovered_at,
        "source": "HiringCafe MCP",
        "job_count": len(sorted_jobs),
        "posting_window_days": POSTED_WITHIN_DAYS,
        "posting_window_start": cutoff_date.isoformat(),
        "posting_window_end": today.isoformat(),
        "matching_jobs_count": len(candidates),
        "new_jobs_added": added,
        "existing_jobs_refreshed": refreshed,
        "jobs": sorted_jobs,
    }

    try:
        backup_jobs(output_path)
        save_jobs(output_path, output)

    except OSError as exc:
        log.error("Could not save jobs.json: %s", exc)
        return 1

    log.info("=" * 65)
    log.info("REFRESH COMPLETE")
    log.info("Total jobs in queue: %d", len(sorted_jobs))
    log.info("Existing jobs removed: %d", removed)
    log.info("New jobs added: %d", added)
    log.info("Existing jobs refreshed: %d", refreshed)
    log.info("Jobs matching configured window: %d", len(candidates))
    log.info("Saved to: %s", output_path)
    log.info("applications.csv was NOT modified.")
    log.info("=" * 65)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
