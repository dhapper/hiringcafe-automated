import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from application_store import register_job
from scripts.config_old import (
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def now_iso():
    """Return the current local time as an ISO 8601 timestamp."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def mcp_request(payload):
    """Send a JSON-RPC request and handle JSON or SSE responses."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    logger.info("Sending MCP request: %s", payload.get("method"))
    response = requests.post(
        HIRINGCAFE_MCP_URL,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.text.strip()

    # Some MCP endpoints return JSON-RPC messages as SSE data lines.
    if "text/event-stream" in response.headers.get("Content-Type", ""):
        messages = []
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[len("data:") :].strip()
                if data and data != "[DONE]":
                    try:
                        messages.append(json.loads(data))
                    except json.JSONDecodeError:
                        continue

        for message in reversed(messages):
            if message.get("id") == payload.get("id"):
                return message
        if messages:
            return messages[-1]
        raise RuntimeError("MCP returned an SSE response without a JSON message.")

    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        # Accommodate servers that return SSE without a content-type header.
        messages = []
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[len("data:") :].strip()))
                except json.JSONDecodeError:
                    continue

        for message in reversed(messages):
            if message.get("id") == payload.get("id"):
                return message

        raise RuntimeError(
            f"Could not parse MCP response. Response begins: {body[:500]}"
        )


def validate_mcp_connection():
    """Confirm that the endpoint exposes the expected search tool."""
    response = mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        }
    )

    if "error" in response:
        raise RuntimeError(f"MCP tools/list failed: {response['error']}")

    tools = response.get("result", {}).get("tools", [])
    tool_names = {tool.get("name") for tool in tools}
    logger.info("MCP tools discovered: %s", ", ".join(sorted(tool_names)))

    if "search_jobs" not in tool_names:
        raise RuntimeError(
            "HiringCafe MCP does not expose 'search_jobs'. "
            "Inspect the available tools before proceeding."
        )
    return tools


def extract_text(response):
    """Extract readable text from an MCP tool result."""
    if "error" in response:
        raise RuntimeError(f"MCP request failed: {response['error']}")

    result = response.get("result", {})
    if result.get("isError"):
        contents = result.get("content", [])
        message = "\n".join(
            item.get("text", "") for item in contents if item.get("type") == "text"
        )
        raise RuntimeError(f"search_jobs returned an error: {message}")

    contents = result.get("content", [])
    text_parts = [
        item.get("text", "") for item in contents if item.get("type") == "text"
    ]
    if text_parts:
        return "\n".join(text_parts)

    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)

    raise RuntimeError(
        "MCP search response contained no readable text or structured content."
    )


def search_jobs(query):
    """Search HiringCafe using the verified MCP tool."""
    arguments = {
        "search": query,
        "location": LOCATION,
        "workplace_types": WORKPLACE_TYPES,
        "employment_types": EMPLOYMENT_TYPES,
        "seniority_levels": SENIORITY_LEVELS,
        "max_years_experience": MAX_YEARS_EXPERIENCE,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "limit": RESULTS_PER_QUERY,
    }

    logger.info("Searching HiringCafe for: %s", query)
    response = mcp_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "search_jobs",
                "arguments": arguments,
            },
        }
    )

    result_text = extract_text(response)
    logger.info(
        "Received %d characters of results for query '%s'.",
        len(result_text),
        query,
    )
    return result_text


def extract_hiringcafe_job_id(job_url):
    """Extract the canonical job ID from the final segment of a HiringCafe URL."""
    path = urlparse(job_url).path.rstrip("/")
    match = re.search(r"-([a-z0-9]{12,})$", path, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_labeled_value(block, labels):
    """Find a value on a line such as '- **Location:** Toronto, ON'."""
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?(?:{label_pattern})"
        rf"(?:\*\*)?\s*:\s*(.+?)\s*$"
    )
    match = pattern.search(block)
    if not match:
        return ""

    value = match.group(1).strip()
    value = re.sub(r"\*\*", "", value).strip()
    return value


def parse_job_blocks(result_text):
    """
    Parse HiringCafe Markdown listings whose headings look like:
    ### [Software Developer](https://hiringcafe.com/job/...)(**Ford**)
    """
    heading_pattern = re.compile(r"(?m)^###\s+\[([^\]]+)\]\((https?://[^)]+)\)(.*)$")
    headings = list(heading_pattern.finditer(result_text))
    jobs = []

    for index, heading in enumerate(headings):
        start = heading.start()
        end = (
            headings[index + 1].start()
            if index + 1 < len(headings)
            else len(result_text)
        )
        block = result_text[start:end]

        title = heading.group(1).strip()
        url = heading.group(2).strip()
        heading_tail = heading.group(3).strip()

        # Company is often bolded on the same line as the title.
        company_match = re.search(r"\*\*(?:Company:?\s*)?(.+?)\*\*", heading_tail)
        if not company_match:
            company_match = re.search(
                r"(?im)^\s*(?:[-*]\s*)?\*\*(?:Company:?\s*)?(.+?)\*\*\s*$",
                block,
            )
        company = company_match.group(1).strip() if company_match else ""

        # Prefer the canonical ID embedded in the URL. The textual ID is a fallback.
        job_id = extract_hiringcafe_job_id(url)
        if not job_id:
            id_match = re.search(
                r"(?im)\bJob\s*ID\s*:\s*`?([A-Za-z0-9_-]+)`?",
                block,
            )
            job_id = id_match.group(1) if id_match else ""

        if not job_id:
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
            job_id = f"url_{digest}"
            logger.warning(
                "No canonical Job ID found for '%s'; using URL-derived key %s.",
                title,
                job_id,
            )

        location = _extract_labeled_value(
            block, ["Location", "Job location", "Work location"]
        )
        posted_at = _extract_labeled_value(
            block, ["Posted", "Date posted", "Posted date"]
        )

        jobs.append(
            {
                "job_id": job_id,
                "title": title,
                "company": company,
                "location": location,
                "job_url": url,
                "posted_at": posted_at,
                "discovered_at": now_iso(),
                "source": "HiringCafe",
            }
        )

    return jobs


def load_existing_jobs():
    """Load the previous queue, preserving jobs across discovery runs."""
    if not JOBS_JSON.exists():
        return {}

    try:
        with JOBS_JSON.open("r", encoding="utf-8-sig") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError(f"Could not safely read {JOBS_JSON}: {error}") from error

    if isinstance(data, list):
        old_jobs = data
    elif isinstance(data, dict):
        old_jobs = data.get("jobs", [])
    else:
        raise RuntimeError("jobs.json has an unexpected format.")

    jobs_by_id = {}
    for job in old_jobs:
        job_id = str(job.get("job_id") or "").strip()
        if job_id:
            jobs_by_id[job_id] = job
    return jobs_by_id


def save_jobs(jobs_by_id):
    """Atomically save the merged discovery queue."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": now_iso(),
        "source": "HiringCafe MCP",
        "job_count": len(jobs_by_id),
        "jobs": sorted(
            jobs_by_id.values(),
            key=lambda job: job.get("posted_at") or "",
        ),
    }

    temp_path = Path(str(JOBS_JSON) + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
        temp_path.replace(JOBS_JSON)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    logger.info("Saved %d unique jobs to %s.", len(jobs_by_id), JOBS_JSON)


def main():
    logger.info("Starting HiringCafe job discovery.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    validate_mcp_connection()
    jobs_by_id = load_existing_jobs()
    original_count = len(jobs_by_id)
    discovered_ids = set()
    successful_queries = 0

    for query in SEARCH_QUERIES:
        try:
            result_text = search_jobs(query)
            parsed_jobs = parse_job_blocks(result_text)
            successful_queries += 1

            if not parsed_jobs:
                logger.warning(
                    "No job listings were parsed for '%s'. Result preview: %s",
                    query,
                    result_text[:500].replace("\n", " "),
                )
                continue

            new_count = 0
            for job in parsed_jobs:
                job_id = job["job_id"]
                discovered_ids.add(job_id)

                if job_id not in jobs_by_id:
                    new_count += 1
                    jobs_by_id[job_id] = job
                else:
                    # Refresh non-empty listing data while preserving first discovery time.
                    previous = jobs_by_id[job_id]
                    for key, value in job.items():
                        if key == "discovered_at":
                            continue
                        if value not in (None, ""):
                            previous[key] = value

                # Refresh the CSV ledger without resetting its status.
                register_job(job)

            logger.info(
                "Query '%s': parsed %d jobs, %d newly discovered.",
                query,
                len(parsed_jobs),
                new_count,
            )
        except Exception:
            logger.exception("Search failed for query '%s'.", query)

    if successful_queries == 0:
        raise RuntimeError(
            "Every HiringCafe search failed. Existing jobs.json was not changed."
        )

    if not discovered_ids and original_count == 0:
        raise RuntimeError(
            "Searches returned no parseable jobs. "
            "Inspect the response format before saving an empty queue."
        )

    save_jobs(jobs_by_id)
    logger.info("Discovery finished.")
    logger.info("Jobs in queue before this run: %d", original_count)
    logger.info("Unique jobs returned this run: %d", len(discovered_ids))
    logger.info("Jobs in queue after this run: %d", len(jobs_by_id))


if __name__ == "__main__":
    main()
