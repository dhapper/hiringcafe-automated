"""
Discover jobs posted today on HiringCafe and save them to data/jobs.json.

Run from the project root:
    python scripts/discover_jobs.py

Requires:
    pip install requests
"""

import json
import logging
import re
import time
import hashlib
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
    raise SystemExit(f"Could not import config.py. Actual error: {exc}") from exc

try:
    from application_store import register_job
except ImportError as exc:
    raise SystemExit(
        "Could not import application_store.py. "
        "Ensure it is in scripts/ beside discover_jobs.py."
    ) from exc


TZ = ZoneInfo("America/Toronto")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hiringcafe_discovery")


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def mcp_request(payload):
    """Send an MCP request using the format used by the working script."""
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

    # Standard JSON response.
    try:
        data = response.json()
        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(f"MCP error: {data['error']}")
            return data
    except ValueError:
        pass

    # Server-Sent Events response.
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue

        text = line[5:].strip()
        if not text or text == "[DONE]":
            continue

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            if data.get("error"):
                raise RuntimeError(f"MCP error: {data['error']}")
            if "id" not in payload or data.get("id") == payload.get("id"):
                return data

    raise RuntimeError(f"Could not parse MCP response: {body[:800]}")


def validate_mcp_connection():
    """Check that the MCP endpoint exposes the expected tools."""
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

    return names


def extract_text(response):
    """Extract the Markdown text returned by the search tool."""
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

    raise RuntimeError(
        "MCP response contained no readable content. "
        f"Response excerpt: {str(result)[:800]}"
    )


def search_jobs(query):
    """Search using the argument names from the previously working script."""
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
    """Extract a job ID from the final URL path segment."""
    if not job_url:
        return None

    path = job_url.rstrip("/").split("?")[0]
    candidate = path.rsplit("/", 1)[-1]
    return candidate or None


def extract_labeled_value(block, labels):
    """Extract Markdown labels such as '- **Posted:** 2026-10-26'."""
    for label in labels:
        pattern = (
            r"^\s*(?:[-*]\s*)?" r"\**\s*" + re.escape(label) + r"\s*\**\s*:\s*(.*?)\s*$"
        )

        match = re.search(
            pattern,
            block,
            re.IGNORECASE | re.MULTILINE,
        )

        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            value = value.strip("* ").strip()
            value = re.sub(r"\s+\[.*?\]\(.*?\)\s*$", "", value)

            if value:
                return value

    return None


def parse_job_blocks(result_text):
    """
    Parse HiringCafe's Markdown job listings.

    Expected format resembles:
        ### [Software Developer](https://hiringcafe.com/job/123)(**Company**)
    """
    heading_pattern = re.compile(
        r"(?m)^#{2,4}\s+\[(?P<title>[^\]]+)\]"
        r"\((?P<url>https?://[^)\s]+)\)"
        r"(?P<tail>[^\n]*)"
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
            block, ["Location", "Job Location", "Locations"]
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
    """
    Convert a posting date to YYYY-MM-DD.

    Relative labels that explicitly mean today are accepted. Ambiguous
    labels such as '1 day ago' are not assumed to mean today.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    normalized = re.sub(r"[\s,]+", " ", text.casefold()).strip()

    today_labels = {
        "today",
        "posted today",
        "just posted",
        "just now",
        "moments ago",
    }
    if normalized in today_labels:
        return today.isoformat()

    # Relative times shorter than a day can safely be assigned to today.
    if re.fullmatch(
        r"(?:posted\s+)?(?:\d+\s*)?"
        r"(?:minute|minutes|min|mins|hour|hours|hr|hrs)"
        r"\s+ago",
        normalized,
    ):
        return today.isoformat()

    # Explicit date and time, including ISO timestamps.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(TZ)
        return parsed.date().isoformat()
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
                # Avoid assigning a future month/day to the current year.
                if parsed_date > today:
                    parsed_date = parsed_date.replace(year=today.year - 1)
            return parsed_date.isoformat()
        except ValueError:
            continue

    return None


def load_existing(path):
    """Load the current jobs.json without discarding prior records."""
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read {path}: {exc}. Back up or repair the file first."
        ) from exc

    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    existing = {}

    for item in jobs:
        if isinstance(item, dict) and item.get("job_id"):
            existing[str(item["job_id"])] = item

    return existing


def merge_job(old, new):
    """Refresh listing details while preserving existing tracking fields."""
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


def save_jobs(path, output):
    """Save atomically so an interrupted run does not truncate jobs.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(output, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    now = datetime.now(TZ)
    today = now.date()
    discovered_at = now.isoformat(timespec="seconds")
    output_path = Path(JOBS_JSON)

    log.info("Toronto date: %s", today.isoformat())
    log.info("Search location: %s", LOCATION)
    log.info("Search window: %s day(s)", POSTED_WITHIN_DAYS)
    log.info("Search terms: %s", ", ".join(SEARCH_QUERIES))
    log.info("JSON output: %s", output_path)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        available_tools = validate_mcp_connection()
    except Exception as exc:
        log.error("HiringCafe MCP connection failed: %s", exc)
        return 1

    try:
        existing = load_existing(output_path)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    log.info("Loaded %d existing job record(s).", len(existing))

    candidates = {}
    failed_searches = 0
    parsed_listing_count = 0

    for index, query in enumerate(SEARCH_QUERIES, start=1):
        log.info("Searching %d/%d: %s", index, len(SEARCH_QUERIES), query)

        try:
            result_text = search_jobs(query)

            if index == 1:
                log.info("DEBUG: First HiringCafe response excerpt:")
                print(result_text[:7000])

            parsed = parse_job_blocks(result_text)
            parsed_listing_count += len(parsed)

            log.info("Parsed %d listing(s) from Markdown.", len(parsed))

            if not parsed:
                log.warning(
                    "No job headings parsed for '%s'. Response excerpt:\n%s",
                    query,
                    result_text[:1200],
                )

            for job in parsed:
                raw_posted = job.pop("posted_at_text", None)
                posted_date = parse_posted_date(raw_posted, today)

                if raw_posted:
                    log.info(
                        "DEBUG DATE: title=%r | raw=%r | parsed=%r | today=%r",
                        job.get("title"),
                        raw_posted,
                        posted_date,
                        today.isoformat(),
                    )

                job["posted_at_text"] = raw_posted
                job["posted_at"] = posted_date
                job["discovered_at"] = discovered_at
                job["source"] = "HiringCafe"

                # Keep only verifiable dates that are actually today.
                if posted_date != today.isoformat():
                    continue

                job_id = job["job_id"]
                candidates[job_id] = merge_job(candidates.get(job_id), job)

        except Exception as exc:
            failed_searches += 1
            log.error("Search failed for '%s': %s", query, exc)

        if index < len(SEARCH_QUERIES):
            time.sleep(0.5)

    log.info("Total parsed listings across searches: %d", parsed_listing_count)
    log.info("Unique jobs verified as posted today: %d", len(candidates))

    # Do not overwrite a useful queue if every search failed or the
    # expected Markdown listing format was not found at all.
    if failed_searches == len(SEARCH_QUERIES):
        log.error("Every search failed. Existing data was left unchanged.")
        return 1

    if parsed_listing_count == 0 and not existing:
        log.error(
            "No listings could be parsed and the existing queue is empty. "
            "Existing files were left unchanged. Inspect the response excerpts "
            "above to diagnose the actual HiringCafe response format."
        )
        return 1

    combined = dict(existing)
    added = 0
    refreshed = 0

    for job_id, fresh in candidates.items():
        if job_id in combined:
            combined[job_id] = merge_job(combined[job_id], fresh)
            refreshed += 1
        else:
            combined[job_id] = merge_job(None, fresh)
            added += 1

        # Update the CSV ledger without resetting existing application status.
        try:
            register_job(fresh)
        except Exception as exc:
            log.error(
                "Could not register %s in application ledger: %s",
                job_id,
                exc,
            )
            return 1

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
        "today_count": len(candidates),
        "new_jobs_added_today": added,
        "existing_jobs_refreshed_today": refreshed,
        "jobs": sorted_jobs,
    }

    try:
        save_jobs(output_path, output)
    except OSError as exc:
        log.error("Could not save %s: %s", output_path, exc)
        return 1

    log.info("Saved %d total record(s) to %s.", len(sorted_jobs), output_path)
    log.info("New today: %d", added)
    log.info("Existing records refreshed today: %d", refreshed)
    log.info("Finished discovery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
