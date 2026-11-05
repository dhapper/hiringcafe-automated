"""Workday My Experience page: fill Work Experience sections from a data file.

Workday autofills this page from the resume but does not always get every job
right, and it SAVES your draft, so a wrong value written by an earlier run is
still there next time. The generic filler cannot fix that: a field labelled
"Company" looks like a question about the employer being applied to.

This handler treats the page as data entry instead:

    1. Read resume/experience.json (your real work history).
    2. Find every Work Experience section by its element ids
       (workExperience-<n>--jobTitle, --companyName, ...).
    3. Match each job to a section: first by company name, then by dates (so a
       section Workday saved with the WRONG company is still recognised), then
       by taking a blank section. If none is free, click Add.
    4. Fill blank fields. When a section was matched by dates but holds the
       wrong company or job title, correct it from your file.
    5. Re-read the page and report anything still missing or wrong.

It never clicks Save and Continue and never submits anything.
"""

import json
import logging
import re
import time
from pathlib import Path

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "my_experience.py"},
)

# my_experience.py is in scripts/portals/workday/.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPERIENCE_PATH = PROJECT_ROOT / "resume" / "experience.json"

# Controls whose id starts with this belong to this handler. application_pages
# keeps the generic filler away from them.
HANDLED_ID_PREFIXES = ("workExperience-",)

SECTION_WAIT_SECONDS = 10
MAX_SECTIONS = 10

_DATE_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{4})\s*$")


# --- reading experience.json ---------------------------------------------------


def _parse_month_year(text, field, job_name):
    match = _DATE_RE.match(text or "")

    if not match:
        raise ValueError(f"{job_name}: '{field}' must look like MM/YYYY, got {text!r}.")

    month, year = int(match.group(1)), int(match.group(2))

    if not 1 <= month <= 12:
        raise ValueError(f"{job_name}: '{field}' has an invalid month: {text!r}.")

    return month, year


def load_experience(path=EXPERIENCE_PATH):
    """Load and validate the work history. Raises ValueError/FileNotFoundError."""
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Work history file not found: {path}. "
            "Copy experience.example.json to resume/experience.json and fill it in."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse {path}: {exc}") from exc

    entries = data.get("work_experience") if isinstance(data, dict) else None

    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path} needs a non-empty 'work_experience' list.")

    jobs = []

    for number, entry in enumerate(entries, start=1):
        name = f"work_experience[{number}]"

        for key in ("job_title", "company", "start"):
            if not str(entry.get(key) or "").strip():
                raise ValueError(f"{name}: '{key}' is required.")

        for key in ("job_title", "company"):
            if "FILL IN" in str(entry[key]):
                raise ValueError(f"{name}: '{key}' still contains the template text.")

        currently = bool(entry.get("currently_work_here"))
        start = _parse_month_year(entry["start"], "start", name)
        end = None

        if not currently:
            if not str(entry.get("end") or "").strip():
                raise ValueError(
                    f"{name}: 'end' is required unless currently_work_here is true."
                )

            end = _parse_month_year(entry["end"], "end", name)

        description = entry.get("description") or ""

        if isinstance(description, list):
            description = "\n".join(
                str(line).strip() for line in description if str(line).strip()
            )

        description = str(description).strip()

        if not description:
            raise ValueError(
                f"{name}: 'description' is required (Workday marks it required)."
            )

        if "FILL IN" in description:
            raise ValueError(f"{name}: 'description' still contains the template text.")

        jobs.append(
            {
                "job_title": str(entry["job_title"]).strip(),
                "company": str(entry["company"]).strip(),
                "location": str(entry.get("location") or "").strip(),
                "currently": currently,
                "start": start,
                "end": end,
                "description": description,
            }
        )

    return jobs


# --- matching jobs to sections (pure logic) --------------------------------------


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def same_company(first, second):
    """True if two company names are the same, ignoring case and punctuation."""
    a, b = _norm(first), _norm(second)

    if not a or not b:
        return False

    if a == b:
        return True

    shorter = min(a, b, key=len)
    return len(shorter) >= 3 and (a in b or b in a)


def dates_match(job, summary):
    """True if a section's From/To dates are this job's dates."""
    if summary["start"] is None or summary["start"] != job["start"]:
        return False

    if job["currently"]:
        return summary["end"] is None

    return summary["end"] == job["end"]


def assign_jobs(jobs, summaries):
    """Decide which section receives which job.

    summaries: one dict per existing section with company, title, start, end.
    Returns (assignments {job_index: (section_index, how)}, missing [job_index],
    leftovers [section_index]). `how` is "company", "dates" or "blank".
    """
    assignments = {}
    claimed = set()

    # 1. Same company (preferring the one whose dates also match).
    for job_index, job in enumerate(jobs):
        candidates = [
            index
            for index, summary in enumerate(summaries)
            if index not in claimed and same_company(summary["company"], job["company"])
        ]

        if candidates:
            best = next(
                (index for index in candidates if dates_match(job, summaries[index])),
                candidates[0],
            )
            assignments[job_index] = (best, "company")
            claimed.add(best)

    # 2. Same dates: Workday saved this job but with the wrong employer.
    for job_index, job in enumerate(jobs):
        if job_index in assignments:
            continue

        for index, summary in enumerate(summaries):
            if index not in claimed and dates_match(job, summary):
                assignments[job_index] = (index, "dates")
                claimed.add(index)
                break

    # 3. A blank section.
    blank_sections = [
        index
        for index, summary in enumerate(summaries)
        if index not in claimed and not summary["company"] and not summary["title"]
    ]

    for job_index in range(len(jobs)):
        if job_index not in assignments and blank_sections:
            index = blank_sections.pop(0)
            assignments[job_index] = (index, "blank")
            claimed.add(index)

    missing = [index for index in range(len(jobs)) if index not in assignments]
    leftovers = [index for index in range(len(summaries)) if index not in claimed]

    return assignments, missing, leftovers


# --- reading the page ---------------------------------------------------------------

LIST_SECTION_IDS_SCRIPT = r"""
const ids = [];
for (const element of document.querySelectorAll('[id^="workExperience-"]')) {
    const match = /^workExperience-(\d+)--/.exec(element.id);
    if (match && !ids.includes(match[1])) ids.push(match[1]);
}
return ids;
"""

FIND_ADD_BUTTON_SCRIPT = r"""
const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
const isWorkHeading = (element) =>
    clean(element.textContent).toLowerCase() === "work experience" &&
    !Array.from(element.children).some(
        (child) => clean(child.textContent).toLowerCase() === "work experience"
    );

const headings = Array.from(document.querySelectorAll("*")).filter(isWorkHeading);
for (const heading of headings) {
    let node = heading.parentElement;
    for (let level = 0; node && level < 8; level++, node = node.parentElement) {
        const button = Array.from(node.querySelectorAll("button")).find((candidate) =>
            /^add( another)?$/i.test(clean(candidate.innerText || candidate.textContent))
        );
        if (button) return button;
    }
}
return null;
"""


def _click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.2)

    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def _element(driver, section_id, suffix):
    found = driver.find_elements(
        By.CSS_SELECTOR, f'[id="workExperience-{section_id}--{suffix}"]'
    )
    return found[0] if found else None


def _value(element):
    return (element.get_attribute("value") or "").strip() if element is not None else ""


def _month_year(driver, section_id, part):
    month = _value(_element(driver, section_id, f"{part}-dateSectionMonth-input"))
    year = _value(_element(driver, section_id, f"{part}-dateSectionYear-input"))

    if month.isdigit() and year.isdigit():
        return int(month), int(year)

    return None


def read_section(driver, section_id):
    """Read one section's current values straight from the page."""
    return {
        "id": section_id,
        "title": _value(_element(driver, section_id, "jobTitle")),
        "company": _value(_element(driver, section_id, "companyName")),
        "location": _value(_element(driver, section_id, "location")),
        "description": _value(_element(driver, section_id, "roleDescription")),
        "start": _month_year(driver, section_id, "startDate"),
        "end": _month_year(driver, section_id, "endDate"),
    }


def section_ids(driver):
    try:
        return list(driver.execute_script(LIST_SECTION_IDS_SCRIPT) or [])
    except WebDriverException:
        logger.exception("Could not list the Work Experience sections.")
        return []


def read_sections(driver):
    return [read_section(driver, section_id) for section_id in section_ids(driver)]


def _summary(section):
    return {key: section[key] for key in ("company", "title", "start", "end")}


# --- filling -----------------------------------------------------------------------


def _same_text(first, second):
    return " ".join((first or "").split()) == " ".join((second or "").split())


def _set_text(driver, element, label, value, replace=False):
    _click(driver, element)

    if replace:
        element.send_keys(Keys.CONTROL, "a")  # typing then replaces the selection

    element.send_keys(value)
    time.sleep(0.2)

    if not _same_text(element.get_attribute("value"), value):
        logger.warning("'%s' may not have kept the value.", label)
        return False

    logger.info("%s %s.", "Corrected" if replace else "Filled", label)
    return True


def _set_date(driver, month_element, year_element, label, month_year):
    month, year = month_year

    for element, text in ((month_element, f"{month:02d}"), (year_element, str(year))):
        _click(driver, element)
        element.send_keys(text)
        time.sleep(0.2)

    shown_month, shown_year = _value(month_element), _value(year_element)

    if not (shown_month.isdigit() and shown_year.isdigit()) or (
        int(shown_month),
        int(shown_year),
    ) != (month, year):
        logger.warning(
            "'%s' shows %s/%s, expected %02d/%s.",
            label,
            shown_month,
            shown_year,
            month,
            year,
        )
        return False

    logger.info("Filled %s with %02d/%s.", label, month, year)
    return True


def _set_currently_working(driver, section_id, wanted):
    boxes = driver.find_elements(
        By.CSS_SELECTOR, f'input[type="checkbox"][id^="workExperience-{section_id}--"]'
    )

    if not boxes:
        logger.warning("No 'I currently work here' checkbox found.")
        return False

    if boxes[0].is_selected() == wanted:
        return True

    _click(driver, boxes[0])
    time.sleep(0.5)

    boxes = driver.find_elements(
        By.CSS_SELECTOR, f'input[type="checkbox"][id^="workExperience-{section_id}--"]'
    )

    if not boxes or boxes[0].is_selected() != wanted:
        logger.warning("'I currently work here' did not change.")
        return False

    logger.info("Set 'I currently work here' to %s.", wanted)
    return True


def _fill_section(driver, job, section_id, how):
    """Fill/correct one section from a job. Returns a list of problems."""
    problems = []

    # Matched by dates only: Workday saved the job with the wrong employer, so
    # the company and title are corrected from the data file.
    correct_existing = how == "dates"

    # Toggling "currently work here" changes which date fields exist: do it first.
    if job["currently"] and not _set_currently_working(driver, section_id, True):
        problems.append("I currently work here")

    for suffix, key, label, may_correct in (
        ("jobTitle", "job_title", "job title", True),
        ("companyName", "company", "company", True),
        ("location", "location", "location", False),
    ):
        wanted = job[key]
        element = _element(driver, section_id, suffix)

        if element is None:
            if label != "location":
                problems.append(label)
            continue

        if not wanted:
            continue

        current = _value(element)

        if not current:
            if not _set_text(driver, element, label, wanted):
                problems.append(label)

        elif correct_existing and may_correct and _norm(current) != _norm(wanted):
            logger.info(
                "Section has %s %r; the data file says %r.", label, current, wanted
            )

            if not _set_text(driver, element, label, wanted, replace=True):
                problems.append(label)

    for part, key in (("startDate", "start"), ("endDate", "end")):
        value = job[key]

        if value is None:
            continue

        month_element = _element(driver, section_id, f"{part}-dateSectionMonth-input")
        year_element = _element(driver, section_id, f"{part}-dateSectionYear-input")

        if month_element is None or year_element is None:
            problems.append(part)
            continue

        if not (_value(month_element) and _value(year_element)):
            if not _set_date(driver, month_element, year_element, part, value):
                problems.append(part)

    description = _element(driver, section_id, "roleDescription")

    if description is None:
        problems.append("role description")
    elif not _value(description):
        if not _set_text(driver, description, "role description", job["description"]):
            problems.append("role description")

    return problems


def _click_add(driver):
    """Click Add in the Work Experience panel. Returns True if clicked."""
    try:
        button = driver.execute_script(FIND_ADD_BUTTON_SCRIPT)
    except WebDriverException:
        logger.exception("Could not search for the Add button.")
        return False

    if button is None:
        logger.warning("The Work Experience Add button was not found.")
        return False

    _click(driver, button)
    return True


def _wait_for_new_section(driver, known_ids):
    """Wait for a section id that was not there before. Returns it or None."""
    try:
        return WebDriverWait(driver, SECTION_WAIT_SECONDS, poll_frequency=0.5).until(
            lambda d: next((i for i in section_ids(d) if i not in known_ids), None)
        )
    except TimeoutException:
        return None


def _verify(driver, jobs):
    """Re-read the page and list anything still missing or wrong."""
    problems = []
    sections = read_sections(driver)
    assignments, missing, leftovers = assign_jobs(jobs, [_summary(s) for s in sections])

    for job_index in missing:
        problems.append(f"{jobs[job_index]['company']}: no section for this job")

    for job_index, (section_index, _how) in assignments.items():
        job = jobs[job_index]
        section = sections[section_index]
        name = job["company"]

        if not section["title"]:
            problems.append(f"{name}: job title is blank")

        if not same_company(section["company"], job["company"]):
            problems.append(
                f"{name}: company reads {section['company']!r}, expected {job['company']!r}"
            )

        if not section["description"]:
            problems.append(f"{name}: role description is blank")

        if section["start"] is None:
            problems.append(f"{name}: start date is blank")

        if not job["currently"] and section["end"] is None:
            problems.append(f"{name}: end date is blank")

    for section_index in leftovers:
        section = sections[section_index]
        shown = section["company"] or section["title"] or "(blank)"
        problems.append(
            f"Work Experience {section_index + 1} ({shown}) is not in experience.json"
        )

    return problems


def fill_my_experience(driver):
    """Fill the Work Experience sections. Returns a list of problems (empty = done)."""
    logger.info("=" * 72)
    logger.info("Starting Workday My Experience fill (no submission).")

    try:
        jobs = load_experience()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return [str(exc)]

    sections = read_sections(driver)
    logger.info(
        "Found %s Work Experience section(s) on the page; %s job(s) in experience.json.",
        len(sections),
        len(jobs),
    )

    for number, section in enumerate(sections, start=1):
        logger.info(
            "  Section %s (id %s): title=%r company=%r start=%s end=%s",
            number,
            section["id"],
            section["title"],
            section["company"],
            section["start"],
            section["end"],
        )

    assignments, missing, leftovers = assign_jobs(jobs, [_summary(s) for s in sections])
    section_id_for = {}

    for job_index, (section_index, how) in assignments.items():
        section_id_for[job_index] = (sections[section_index]["id"], how)
        logger.info(
            "Job %r -> section %s (matched by %s).",
            jobs[job_index]["company"],
            section_index + 1,
            how,
        )

    # Add a section for every job that has nowhere to go.
    for job_index in missing:
        known_ids = set(section_ids(driver))

        if len(known_ids) >= MAX_SECTIONS:
            break

        logger.info("No free section for %s; clicking Add.", jobs[job_index]["company"])

        new_id = _click_add(driver) and _wait_for_new_section(driver, known_ids)

        if not new_id:
            logger.warning(
                "Could not add a section for %s.", jobs[job_index]["company"]
            )
            continue

        section_id_for[job_index] = (new_id, "blank")

    for job_index in sorted(section_id_for):
        section_id, how = section_id_for[job_index]
        job = jobs[job_index]

        logger.info("Filling section id %s with %s.", section_id, job["company"])

        try:
            problems = _fill_section(driver, job, section_id, how)
        except WebDriverException:
            logger.exception("Could not fill section id %s.", section_id)
            continue

        if problems:
            logger.warning(
                "Section id %s: could not fill %s.", section_id, ", ".join(problems)
            )

    for section_index in leftovers:
        section = sections[section_index]
        logger.warning(
            "Work Experience %s (%r) matches none of your jobs and was left unchanged.",
            section_index + 1,
            section["company"] or section["title"] or "blank",
        )

    time.sleep(1)
    problems = _verify(driver, jobs)

    if problems:
        logger.warning("My Experience is not complete:")

        for problem in problems:
            logger.warning("  - %s", problem)
    else:
        logger.info("All Work Experience sections match experience.json.")

    logger.info("My Experience fill finished. Nothing was submitted.")
    logger.info("=" * 72)

    return problems
