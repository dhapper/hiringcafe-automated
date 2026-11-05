"""Load the candidate information that is fed to the AI answering component."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .logging_utils import get_logger

logger = get_logger("candidate_context.py")

# common/ is expected at <project>/scripts/common/. Adjust parents[2] if it moves.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
NOTES_PATH = PROJECT_ROOT / "resume" / "my_info.txt"
JOB_POSTING_PATH = PROJECT_ROOT / "data" / "current_job_posting.json"

# Lines such as "password: ..." must never be sent to the model, because the
# model could echo them into an application answer.
_SECRET_LINE = re.compile(
    r"^[ \t]*[-*•]?[ \t]*(password|passcode)[ \t]*:.*$\n?",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class CandidateContext:
    """Everything the model may use when answering a question."""

    personal_info: str
    resume_text: str
    job_posting: str


def redact_secrets(text):
    """Remove credential lines from the notes before they reach the model."""
    return _SECRET_LINE.sub("", text)


def load_candidate_context(
    notes_path=NOTES_PATH,
    resume_path=RESUME_PATH,
    job_posting_path=JOB_POSTING_PATH,
):
    """Load notes, resume text, and the current job posting.

    Call this once per application, not once at import time, because
    current_job_posting.json is rewritten for every job.
    """
    notes_path = Path(notes_path)
    resume_path = Path(resume_path)
    job_posting_path = Path(job_posting_path)

    for label, path in (
        ("Personal information file", notes_path),
        ("Resume PDF", resume_path),
        ("Current job posting", job_posting_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")

    personal_info = redact_secrets(notes_path.read_text(encoding="utf-8")).strip()

    import pymupdf

    with pymupdf.open(resume_path) as pdf:
        resume_text = "\n".join(page.get_text() for page in pdf).strip()

    try:
        job_data = json.loads(job_posting_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"Could not parse job posting JSON at {job_posting_path}: {exc}"
        ) from exc

    if not isinstance(job_data, dict):
        raise ValueError(f"Expected a JSON object in {job_posting_path}")

    job_posting = json.dumps(job_data, ensure_ascii=False, indent=2)

    if not resume_text:
        logger.warning("No selectable text was extracted from the resume.")

    logger.info("Loaded candidate information from %s", notes_path)
    logger.info("Extracted %d characters from the resume.", len(resume_text))
    logger.info(
        "Loaded current job posting from %s (%d characters).",
        job_posting_path,
        len(job_posting),
    )

    return CandidateContext(
        personal_info=personal_info,
        resume_text=resume_text,
        job_posting=job_posting,
    )


def _normalize_key(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def get_explicit_profile_value(personal_info, aliases):
    """Find a value explicitly written in the notes file.

    Two formats count as explicit:
        key: value                     (e.g. "Gender: Male")
        # Heading                      (e.g. "# LinkedIn URL")
        value-on-the-next-line         (only when that value is one token, such
                                        as a URL, with no spaces)

    Longer text under a heading (the "Common Application Answers" guidance) is
    deliberately NOT returned here. It is context for the AI, and typing a
    sentence like "Default to LinkedIn or Indeed" into a field would be wrong.
    """
    alias_set = {_normalize_key(alias) for alias in aliases}
    lines = personal_info.splitlines()

    for raw_line in lines:
        line = raw_line.strip().lstrip("-*• ").strip()

        if not line or line.startswith("#") or ":" not in line:
            continue

        key, value = line.split(":", 1)

        if _normalize_key(key) in alias_set and value.strip():
            return value.strip()

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()

        if not stripped.startswith("#"):
            continue

        if _normalize_key(stripped.lstrip("#")) not in alias_set:
            continue

        body = []

        for following in lines[index + 1 :]:
            text = following.strip()

            if text.startswith("#"):
                break

            if text:
                body.append(text)

        if len(body) == 1 and not re.search(r"\s", body[0]):
            return body[0]

    return ""
