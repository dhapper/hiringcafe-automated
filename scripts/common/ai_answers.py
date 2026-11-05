"""Reusable Ollama answering component shared by every application handler.

This module knows nothing about Selenium, Workday, or Lever. A handler scrapes
a field, wraps it in a `Question`, calls `answer_question`, and applies the
returned `Answer` to its own page however it needs to.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace

from .candidate_context import CandidateContext
from .choices import match_option
from .logging_utils import get_logger

logger = get_logger("ai_answers.py")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:4b"
OLLAMA_NUM_CTX = 8192  # notes + resume + job posting can exceed Ollama's default
OLLAMA_TIMEOUT_SECONDS = 240
MAX_ATTEMPTS = 3

VALID_ACTIONS = {"fill", "select", "check", "uncheck", "skip"}

# Legal agreements, declarations, and consent must be reviewed manually.
SENSITIVE_WORDS = (
    "consent",
    "terms and conditions",
    "privacy policy",
    "electronic signature",
    "i certify",
    "i hereby certify",
    "under penalty of perjury",
    "background check authorization",
    "drug test authorization",
    "signature",
    "agree to the",
    "acknowledge that",
)


@dataclass(frozen=True)
class Question:
    """One scraped application question, independent of the site it came from."""

    text: str
    field_type: str = "text input"
    options: list = field(default_factory=list)
    page_title: str = ""


@dataclass(frozen=True)
class Answer:
    """The model's answer. `action` is one of VALID_ACTIONS."""

    action: str
    value: str = ""
    reason: str = ""

    @property
    def is_skip(self):
        return self.action == "skip" or (
            self.action in {"fill", "select"} and not self.value
        )

    @classmethod
    def skip(cls, reason=""):
        return cls(action="skip", value="", reason=reason)


RETRY_GUIDANCE = """
RETRY INSTRUCTIONS:
Your previous attempt chose skip. Reconsider the question using all the
candidate information, resume, and current job posting already provided.

Before choosing skip:
- Look for transferable skills, related responsibilities, projects, tools,
  and practical experience that could support a useful answer.
- For experience questions, consider relevant adjacent experience and give
  the most defensible answer supported by the available dates and facts.
- For written answers, explain how the candidate's related experience applies
  without claiming that adjacent experience was direct experience.
- For salary questions, use an explicitly configured salary expectation if
  available in the candidate information.
- For dropdowns and radio groups, choose the closest defensible available
  option, matching its text exactly.
- For standalone checkboxes, use check or uncheck only when the candidate
  information supports that choice.

Do not skip merely because the candidate has not used the exact tool or held
the exact job title. Use related experience where reasonable.
Do not invent dates, years, qualifications, personal facts, or direct
experience that the candidate does not have.
Continue to skip legal declarations, consent, certifications, and unknown
personal facts that cannot reasonably be established.

Return exactly one JSON object in the original required format.
""".strip()


def is_sensitive(question_text):
    """Return True for legal/consent wording that must be handled manually."""
    lowered = (question_text or "").lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def build_prompt(question, context):
    """Build the prompt. Wording matches the original Lever prompt."""
    return f"""
You are helping fill a job application for the candidate.
Answer the exact question below using the candidate information and resume.
Use related, relevant experience when appropriate.
If the answer cannot be responsibly determined from the provided information,
return action "skip".

CANDIDATE INFORMATION:
{context.personal_info}
RESUME:
{context.resume_text}
CURRENT JOB POSTING (JSON):
{context.job_posting}
JOB TITLE / PAGE:
{question.page_title}
APPLICATION QUESTION:
{question.text}
FIELD TYPE:
{question.field_type}
AVAILABLE OPTIONS:
{json.dumps(question.options, ensure_ascii=False)}
Return exactly one JSON object:
{{"action":"fill","value":"answer","reason":"brief reason"}}
Allowed actions:
- fill: text input or textarea
- select: select one dropdown, radio, or single-choice option
- check: check a checkbox when supported by the candidate information
- uncheck: uncheck a checkbox only when clearly appropriate
- skip: unknown, unsupported, sensitive, or uncertain answer
Important rules:
- For salary expectation, use a figure or range only if candidate information
  explicitly provides an appropriate expectation. Otherwise skip.
- If the current job posting states a salary range, answer with the top of that
  posted range instead of the candidate's default expectation.
- For experience questions, describe related experience honestly. Do not
  claim direct QA automation experience if the sources do not support it.
- For checkboxes about willingness to work at a location or work authorization,
  answer only when candidate information clearly supports the answer.
- Never accept consent, legal terms, certifications, or declarations.
- If options are provided, match the answer to one of them exactly.
- Return JSON only, without markdown or surrounding commentary.
""".strip()


def _call_ollama(prompt):
    """POST one prompt to Ollama and return the decoded response dict."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "num_predict": 300,
            "num_ctx": OLLAMA_NUM_CTX,
        },
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_json_object(raw):
    """Parse the model output, tolerating text around a single JSON object."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)

        if not match:
            raise ValueError("No JSON object found")

        return json.loads(match.group(0))


def ask_ollama(question, context):
    """Ask the model for a structured answer.

    Retries transient failures, and retries once with transferable-experience
    guidance if the first answer is a skip. Always returns an Answer; it never
    raises for model or network problems.
    """
    prompt = build_prompt(question, context)
    retry_after_skip = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        current_prompt = prompt

        if retry_after_skip:
            current_prompt = prompt + "\n\n" + RETRY_GUIDANCE

        error = None
        result = {}

        try:
            result = _call_ollama(current_prompt)

        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            logger.error("Ollama HTTP %s: %s", exc.code, details[:500])
            error = "Ollama HTTP error"

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("Ollama request failed on attempt %d: %s", attempt, exc)
            error = str(exc)

        raw = (result.get("response") or "").strip() if not error else ""

        if not error and not raw:
            logger.warning(
                "Ollama returned empty response on attempt %d/%d. "
                "done=%s, done_reason=%s, thinking=%s",
                attempt,
                MAX_ATTEMPTS,
                result.get("done"),
                result.get("done_reason"),
                (result.get("thinking") or "")[:200],
            )
            error = "Ollama returned an empty response"

        parsed = None

        if not error:
            try:
                parsed = _parse_json_object(raw)
            except ValueError:
                logger.warning(
                    "Invalid Ollama JSON on attempt %d/%d: %s",
                    attempt,
                    MAX_ATTEMPTS,
                    raw[:500],
                )
                error = "Invalid JSON"

        if not error and not isinstance(parsed, dict):
            logger.warning(
                "Ollama returned a JSON value that was not an object "
                "on attempt %d/%d.",
                attempt,
                MAX_ATTEMPTS,
            )
            error = "Response was not a JSON object"

        if error:
            if attempt < MAX_ATTEMPTS:
                time.sleep(2)
                continue

            return Answer.skip(error)

        action = str(parsed.get("action", "skip")).strip().lower()
        value = str(parsed.get("value", "")).strip()
        reason = str(parsed.get("reason", "")).strip()

        if action not in VALID_ACTIONS:
            action = "skip"

        answer = Answer(action=action, value=value, reason=reason)

        # A skip, or a fill/select with no value, is not accepted as an answer.
        if answer.is_skip:
            if not retry_after_skip and attempt < MAX_ATTEMPTS:
                logger.warning(
                    "Ollama chose skip or returned an empty answer for '%s'. "
                    "Retrying with transferable-experience guidance. "
                    "Original reason: %s",
                    question.text,
                    reason,
                )
                retry_after_skip = True
                time.sleep(1)
                continue

            logger.warning(
                "Ollama still could not answer '%s' after reconsideration. "
                "Leaving unanswered. Reason: %s",
                question.text,
                reason or "No defensible answer produced",
            )

            return Answer.skip(reason or "No defensible answer produced after retry")

        logger.info(
            "Ollama answered '%s' on attempt %d/%d: action=%s, reason=%s",
            question.text,
            attempt,
            MAX_ATTEMPTS,
            action,
            reason,
        )

        return answer

    return Answer.skip("Retries exhausted")


def answer_question(question, context):
    """Main entry point for handlers.

    1. Skips sensitive/legal questions without calling the model.
    2. Asks the model.
    3. If the question has options, forces the answer onto exactly one real
       option; an answer that cannot be matched uniquely becomes a skip.
    """
    if is_sensitive(question.text):
        logger.info("Skipping sensitive question: %s", question.text)
        return Answer.skip("Sensitive question; manual review required")

    answer = ask_ollama(question, context)

    if answer.is_skip:
        log_answer(question, answer)
        return answer

    if question.options and answer.action in {"select", "fill"}:
        matched = match_option(question.options, answer.value)

        if not matched:
            logger.warning(
                "Answer '%s' did not match one unique option for '%s'. "
                "Available options: %s",
                answer.value,
                question.text,
                question.options,
            )
            answer = Answer.skip(f"Answer '{answer.value}' matched no option")
        else:
            answer = replace(answer, value=matched)

    log_answer(question, answer)
    return answer


def log_answer(question, answer):
    logger.info(
        "Question: %s | Action: %s | Answer: %s | Reason: %s",
        question.text,
        answer.action,
        answer.value,
        answer.reason,
    )
