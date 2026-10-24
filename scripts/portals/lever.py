"""Upload a resume and fill unanswered Lever application fields using Ollama."""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
INFO_PATH = PROJECT_ROOT / "resume" / "my_info.txt"

APPLY_BUTTON_SELECTOR = "a.postings-btn.template-btn-submit"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:4b"

# Enabled as requested. The script still never submits the application.
AUTO_FILL_FIELDS = True

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

PLACEHOLDER_OPTIONS = {
    "",
    "select",
    "select an option",
    "choose",
    "choose an option",
    "-- select --",
    "please select",
}


def load_candidate_information():
    """Read personal information and extract text from the resume PDF."""

    if not INFO_PATH.is_file():
        raise FileNotFoundError(f"Personal information file not found: {INFO_PATH}")

    if not RESUME_PATH.is_file():
        raise FileNotFoundError(f"Resume PDF not found: {RESUME_PATH}")

    personal_info = INFO_PATH.read_text(encoding="utf-8").strip()

    import pymupdf

    with pymupdf.open(RESUME_PATH) as pdf:
        resume_text = "\n".join(page.get_text() for page in pdf).strip()

    if not resume_text:
        logger.warning("No selectable text was extracted from the resume.")

    logger.info("Loaded candidate information from %s", INFO_PATH)
    logger.info("Extracted %d characters from the resume.", len(resume_text))

    return personal_info, resume_text


def upload_resume(driver, wait):
    """Upload the resume through Lever's file input."""

    logger.info("Uploading resume: %s", RESUME_PATH)

    if not RESUME_PATH.is_file():
        raise FileNotFoundError(f"Resume not found: {RESUME_PATH}")

    resume_input = wait.until(
        EC.presence_of_element_located((By.ID, "resume-upload-input"))
    )
    resume_input.send_keys(str(RESUME_PATH))
    time.sleep(2)

    if resume_input.get_attribute("value"):
        logger.info("Resume file selected.")
    else:
        logger.warning("Resume selection could not be confirmed. Check Chrome.")


def read_question_metadata(driver):
    """
    Read Lever's hidden baseTemplate fields.

    Lever custom questions often use names such as:
        cards[question-id][field0]
        cards[question-id][baseTemplate]

    The hidden baseTemplate contains the actual question and field definitions.
    """

    metadata_by_prefix = {}

    hidden_fields = driver.find_elements(
        By.CSS_SELECTOR,
        'input[type="hidden"][name$="[baseTemplate]"]',
    )

    for hidden in hidden_fields:
        name = hidden.get_attribute("name") or ""
        value = hidden.get_attribute("value") or ""

        match = re.match(r"^(.*)\[baseTemplate\]$", name)
        if not match or not value:
            continue

        prefix = match.group(1)

        try:
            metadata = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Could not parse Lever metadata for %s", prefix)
            continue

        metadata_by_prefix[prefix] = metadata

        logger.info(
            "Found custom question metadata: %s",
            metadata.get("text", prefix),
        )

    return metadata_by_prefix


def get_metadata_for_element(element, metadata_by_prefix):
    """Find metadata belonging to a custom question field."""

    name = element.get_attribute("name") or ""

    # Match the card/question prefix, not just a generic field label.
    for prefix, metadata in metadata_by_prefix.items():
        if name.startswith(prefix + "["):
            return prefix, metadata

    return None, None


def get_field_definition(element, metadata):
    """Map a custom field such as field0 to its metadata definition."""

    if not metadata:
        return {}

    name = element.get_attribute("name") or ""
    match = re.search(r"\[field(\d+)\]$", name)

    definitions = metadata.get("fields", [])

    if match:
        index = int(match.group(1))
        if isinstance(definitions, list) and index < len(definitions):
            definition = definitions[index]
            if isinstance(definition, dict):
                return definition

    # Some templates have only one field and do not use a fieldN name.
    if isinstance(definitions, list) and len(definitions) == 1:
        if isinstance(definitions[0], dict):
            return definitions[0]

    return {}


def get_element_label(driver, element):
    """Find an associated HTML label."""

    element_id = element.get_attribute("id") or ""

    if element_id:
        labels = driver.find_elements(
            By.XPATH,
            f"//label[@for={json.dumps(element_id)}]",
        )
        for label in labels:
            if label.text.strip():
                return label.text.strip()

    try:
        label = element.find_element(By.XPATH, "./ancestor::label[1]")
        if label.text.strip():
            return label.text.strip()
    except WebDriverException:
        pass

    return (
        element.get_attribute("aria-label")
        or element.get_attribute("placeholder")
        or ""
    ).strip()


def get_nearby_text(element):
    """Collect a small amount of nearby visible text."""

    try:
        fieldset = element.find_element(By.XPATH, "./ancestor::fieldset[1]")
        legend = fieldset.find_element(By.TAG_NAME, "legend").text.strip()
        if legend:
            return legend
    except WebDriverException:
        pass

    try:
        container = element.find_element(
            By.XPATH,
            "./ancestor::*[self::fieldset or self::li or self::div][1]",
        )
        return " ".join(container.text.split())[:500]
    except WebDriverException:
        return ""


def build_question(element, metadata_by_prefix):
    """
    Build the most accurate question possible.

    Prefer Lever's hidden metadata, then HTML labels and nearby visible text.
    """

    prefix, metadata = get_metadata_for_element(element, metadata_by_prefix)
    definition = get_field_definition(element, metadata)

    question_title = ""
    if metadata:
        question_title = str(metadata.get("text", "")).strip()

    field_title = str(definition.get("text", "")).strip()
    label = get_element_label_from_element(element)
    nearby = get_nearby_text(element)

    parts = []

    if question_title:
        parts.append(question_title)

    if field_title and field_title.casefold() != question_title.casefold():
        parts.append(field_title)

    if label and label.casefold() not in {part.casefold() for part in parts}:
        parts.append(label)

    # Nearby text is a fallback, not the preferred source: it can include
    # unrelated fields when a form uses large nested div containers.
    if not parts and nearby:
        parts.append(nearby)

    if not parts:
        parts.append(
            element.get_attribute("name")
            or element.get_attribute("id")
            or "Unlabelled application question"
        )

    question = " | ".join(parts)

    return question, prefix, metadata, definition


def get_element_label_from_element(element):
    """Find a label without needing a separate driver argument."""

    element_id = element.get_attribute("id") or ""

    if element_id:
        try:
            label = element.find_element(
                By.XPATH,
                f"//label[@for={json.dumps(element_id)}]",
            )
            if label.text.strip():
                return label.text.strip()
        except WebDriverException:
            pass

    try:
        label = element.find_element(By.XPATH, "./ancestor::label[1]")
        if label.text.strip():
            return label.text.strip()
    except WebDriverException:
        pass

    return (
        element.get_attribute("aria-label")
        or element.get_attribute("placeholder")
        or ""
    ).strip()


def get_option_labels(element, metadata_definition=None):
    """Get available choices from HTML or Lever metadata."""

    labels = []

    if metadata_definition:
        options = metadata_definition.get("options", [])
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict):
                    text = str(
                        option.get("text")
                        or option.get("label")
                        or option.get("value")
                        or ""
                    ).strip()
                else:
                    text = str(option).strip()

                if text and text not in labels:
                    labels.append(text)

    # Native dropdown options are read from the actual page later.
    if element.tag_name.lower() == "select":
        try:
            for option in Select(element).options:
                text = option.text.strip()
                if text and text not in labels:
                    labels.append(text)
        except WebDriverException:
            pass

    return labels


def ask_ollama(
    question,
    field_type,
    personal_info,
    resume_text,
    options=None,
    page_title="",
):
    """Ask Qwen for a structured answer and retry empty/invalid responses."""

    options = options or []

    prompt = f"""
You are helping fill a job application for the candidate.

Answer the exact question below using the candidate information and resume.
Use related, relevant experience when appropriate, but never claim experience,
certifications, tools, or qualifications that the candidate does not have.
If the answer cannot be responsibly determined from the provided information,
return action "skip".

CANDIDATE INFORMATION:
{personal_info}

RESUME:
{resume_text}

JOB TITLE / PAGE:
{page_title}

APPLICATION QUESTION:
{question}

FIELD TYPE:
{field_type}

AVAILABLE OPTIONS:
{json.dumps(options, ensure_ascii=False)}

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
- For experience questions, describe related experience honestly. Do not
  claim direct QA automation experience if the sources do not support it.
- For checkboxes about willingness to work at a location or work authorization,
  answer only when candidate information clearly supports the answer.
- Never accept consent, legal terms, certifications, or declarations.
- If options are provided, match the answer to one of them exactly.
- Return JSON only, without markdown or surrounding commentary.
""".strip()

    for attempt in range(1, 4):
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
            },
        }

        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            logger.error("Ollama HTTP %s: %s", exc.code, details[:500])
            if attempt < 3:
                time.sleep(2)
                continue
            return {"action": "skip", "value": "", "reason": "Ollama HTTP error"}

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning("Ollama request failed on attempt %d: %s", attempt, exc)
            if attempt < 3:
                time.sleep(2)
                continue
            return {"action": "skip", "value": "", "reason": str(exc)}

        raw = (result.get("response") or "").strip()

        if not raw:
            logger.warning(
                "Ollama returned empty response on attempt %d/3. "
                "done=%s, done_reason=%s, thinking=%s",
                attempt,
                result.get("done"),
                result.get("done_reason"),
                (result.get("thinking") or "")[:200],
            )
            if attempt < 3:
                time.sleep(2)
                continue
            return {
                "action": "skip",
                "value": "",
                "reason": "Ollama returned an empty response",
            }

        try:
            answer = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            try:
                if not match:
                    raise ValueError("No JSON object found")
                answer = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Invalid Ollama JSON on attempt %d/3: %s",
                    attempt,
                    raw[:500],
                )
                if attempt < 3:
                    time.sleep(2)
                    continue
                return {"action": "skip", "value": "", "reason": "Invalid JSON"}

        if not isinstance(answer, dict):
            continue

        action = str(answer.get("action", "skip")).strip().lower()
        value = str(answer.get("value", "")).strip()
        reason = str(answer.get("reason", "")).strip()

        if action not in {"fill", "select", "check", "uncheck", "skip"}:
            action = "skip"

        return {"action": action, "value": value, "reason": reason}

    return {"action": "skip", "value": "", "reason": "Retries exhausted"}


def is_sensitive(question):
    lowered = question.lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def field_is_empty(element):
    """Check whether a field has no real answer yet."""

    tag = element.tag_name.lower()
    input_type = (element.get_attribute("type") or "").lower()

    if input_type in {"checkbox", "radio"}:
        return not element.is_selected()

    if tag == "select":
        try:
            selected = Select(element).first_selected_option
            text = selected.text.strip().casefold()
            value = (selected.get_attribute("value") or "").strip()
            return not value or text in PLACEHOLDER_OPTIONS
        except (WebDriverException, IndexError):
            return True

    return not (element.get_attribute("value") or "").strip()


def log_suggestion(question, answer):
    logger.info(
        "Question: %s | Action: %s | Answer: %s | Reason: %s",
        question,
        answer["action"],
        answer["value"],
        answer["reason"],
    )


def process_text_field(
    element,
    question,
    personal_info,
    resume_text,
    page_title,
):
    """Ask Ollama to answer a blank text field or textarea."""

    if not field_is_empty(element):
        logger.info("Skipping populated field: %s", question)
        return

    if is_sensitive(question):
        logger.info("Skipping sensitive field: %s", question)
        return

    answer = ask_ollama(
        question=question,
        field_type=element.tag_name.lower(),
        personal_info=personal_info,
        resume_text=resume_text,
        page_title=page_title,
    )
    log_suggestion(question, answer)

    if answer["action"] != "fill" or not answer["value"]:
        return

    if AUTO_FILL_FIELDS and field_is_empty(element):
        element.send_keys(answer["value"])
        logger.info("Filled text field: %s", question)


def process_dropdown(
    element,
    question,
    metadata_definition,
    personal_info,
    resume_text,
    page_title,
):
    """Ask Ollama to choose a real available dropdown option."""

    if not field_is_empty(element):
        logger.info("Skipping selected dropdown: %s", question)
        return

    if is_sensitive(question):
        logger.info("Skipping sensitive dropdown: %s", question)
        return

    options = get_option_labels(element, metadata_definition)

    answer = ask_ollama(
        question=question,
        field_type="dropdown",
        personal_info=personal_info,
        resume_text=resume_text,
        options=options,
        page_title=page_title,
    )
    log_suggestion(question, answer)

    if answer["action"] not in {"select", "fill"} or not answer["value"]:
        return

    target = next(
        (
            option
            for option in Select(element).options
            if option.text.strip().casefold() == answer["value"].casefold()
        ),
        None,
    )

    if target is None:
        logger.warning(
            "Answer did not match a dropdown option for '%s': %s",
            question,
            answer["value"],
        )
        return

    if AUTO_FILL_FIELDS and field_is_empty(element):
        Select(element).select_by_visible_text(target.text.strip())
        logger.info("Selected dropdown option for: %s", question)


def process_radio_groups(
    groups,
    metadata_by_prefix,
    personal_info,
    resume_text,
    page_title,
):
    """Treat all radio choices with the same name as one question."""

    for name, elements in groups.items():
        try:
            if any(element.is_selected() for element in elements):
                logger.info("Skipping answered radio group: %s", name)
                continue

            question, _, metadata, definition = build_question(
                elements[0], metadata_by_prefix
            )

            options = []
            for element in elements:
                label = get_element_label_from_element(element)
                if label:
                    options.append(label)

            if not options:
                options = get_option_labels(elements[0], definition)

            if is_sensitive(question):
                logger.info("Skipping sensitive radio group: %s", question)
                continue

            answer = ask_ollama(
                question=question,
                field_type="radio group",
                personal_info=personal_info,
                resume_text=resume_text,
                options=options,
                page_title=page_title,
            )
            log_suggestion(question, answer)

            if answer["action"] not in {"select", "fill"} or not answer["value"]:
                continue

            target = next(
                (
                    element
                    for element in elements
                    if get_element_label_from_element(element).strip().casefold()
                    == answer["value"].casefold()
                ),
                None,
            )

            if target is None:
                logger.warning(
                    "Radio answer did not match an option for '%s': %s",
                    question,
                    answer["value"],
                )
                continue

            if AUTO_FILL_FIELDS and not target.is_selected():
                target.click()
                logger.info("Selected radio answer for: %s", question)

        except WebDriverException:
            logger.exception("Could not process radio group %s", name)


def process_checkbox_groups(
    groups,
    metadata_by_prefix,
    personal_info,
    resume_text,
    page_title,
):
    """
    Handle checkbox groups together.

    A group of Yes/No choices should be treated as a single-choice question,
    not as two unrelated checkboxes.
    """

    for name, elements in groups.items():
        try:
            if any(element.is_selected() for element in elements):
                logger.info("Skipping answered checkbox group: %s", name)
                continue

            question, _, metadata, definition = build_question(
                elements[0], metadata_by_prefix
            )

            labels = [get_element_label_from_element(element) for element in elements]
            normalized = {label.strip().casefold() for label in labels if label.strip()}

            options = labels[:]

            if not options:
                options = get_option_labels(elements[0], definition)

            if is_sensitive(question):
                logger.info("Skipping sensitive checkbox group: %s", question)
                continue

            # Don't treat every Yes/No input as an independent question.
            # Ask about the group and select at most one answer.
            is_yes_no = normalized.issubset(
                {"yes", "no", "y", "n", "true", "false"}
            ) and bool(normalized)

            answer = ask_ollama(
                question=question,
                field_type=(
                    "single-choice checkbox group" if is_yes_no else "checkbox group"
                ),
                personal_info=personal_info,
                resume_text=resume_text,
                options=options,
                page_title=page_title,
            )
            log_suggestion(question, answer)

            if answer["action"] not in {"select", "fill", "check"}:
                continue

            target = next(
                (
                    element
                    for element in elements
                    if get_element_label_from_element(element).strip().casefold()
                    == answer["value"].strip().casefold()
                ),
                None,
            )

            if target is None:
                # A simple "check" response can be used for a single checkbox.
                if len(elements) == 1 and answer["action"] == "check":
                    target = elements[0]
                else:
                    logger.warning(
                        "Checkbox answer did not match an option for '%s': %s",
                        question,
                        answer["value"],
                    )
                    continue

            if AUTO_FILL_FIELDS and not target.is_selected():
                # For a yes/no group, ensure only the chosen answer is checked.
                if is_yes_no:
                    for element in elements:
                        if element.is_selected():
                            element.click()

                target.click()
                logger.info("Selected checkbox answer for: %s", question)

        except WebDriverException:
            logger.exception("Could not process checkbox group %s", name)


def get_control_groups(fields, control_type):
    """Group radio buttons or checkboxes by their HTML name."""

    groups = defaultdict(list)

    for element in fields:
        try:
            if element.tag_name.lower() != "input":
                continue

            if (element.get_attribute("type") or "").lower() != control_type:
                continue

            if not element.is_displayed() or not element.is_enabled():
                continue

            name = element.get_attribute("name") or ""

            # Lever usually gives options for one custom question the same name.
            # If names are absent, use the nearby parent element as a fallback.
            if not name:
                try:
                    parent = element.find_element(
                        By.XPATH,
                        "./ancestor::*[self::fieldset or self::li][1]",
                    )
                    key = f"{control_type}-parent-{parent.id}"
                except WebDriverException:
                    key = f"{control_type}-element-{element.id}"
            else:
                key = name

            groups[key].append(element)

        except WebDriverException:
            continue

    return groups


def inspect_and_fill_form(driver, personal_info, resume_text):
    """Read Lever question metadata and process blank form controls."""

    logger.info("=" * 65)
    logger.info("PROCESSING LEVER APPLICATION")
    logger.info("Page: %s", driver.title)
    logger.info("URL: %s", driver.current_url)
    logger.info("Automatic filling enabled: %s", AUTO_FILL_FIELDS)
    logger.info("=" * 65)

    page_title = driver.title

    # Important: read hidden metadata before inspecting visible controls.
    metadata_by_prefix = read_question_metadata(driver)

    fields = driver.find_elements(
        By.CSS_SELECTOR,
        "input, select, textarea, button",
    )

    radio_groups = get_control_groups(fields, "radio")
    checkbox_groups = get_control_groups(fields, "checkbox")

    grouped_ids = {
        element.id
        for groups in (radio_groups, checkbox_groups)
        for group in groups.values()
        for element in group
    }

    process_radio_groups(
        radio_groups,
        metadata_by_prefix,
        personal_info,
        resume_text,
        page_title,
    )

    process_checkbox_groups(
        checkbox_groups,
        metadata_by_prefix,
        personal_info,
        resume_text,
        page_title,
    )

    processed = 0

    for element in fields:
        try:
            tag = element.tag_name.lower()
            input_type = (element.get_attribute("type") or tag).lower()

            if tag == "button" or input_type in {
                "hidden",
                "file",
                "submit",
                "button",
                "reset",
                "image",
                "password",
                "radio",
                "checkbox",
            }:
                continue

            if not element.is_displayed() or not element.is_enabled():
                continue

            question, _, metadata, definition = build_question(
                element, metadata_by_prefix
            )

            if tag == "select":
                process_dropdown(
                    element,
                    question,
                    definition,
                    personal_info,
                    resume_text,
                    page_title,
                )
                processed += 1
                continue

            if tag == "textarea" or (
                tag == "input"
                and input_type in {"text", "email", "tel", "url", "number", "search"}
            ):
                process_text_field(
                    element,
                    question,
                    personal_info,
                    resume_text,
                    page_title,
                )
                processed += 1

        except WebDriverException:
            logger.exception("Could not process a form control.")

    logger.info("=" * 65)
    logger.info("Processed %d text/dropdown fields.", processed)
    logger.info("Application has NOT been submitted.")
    logger.info("=" * 65)


def run(driver):
    """Navigate to Lever's application form, upload resume, and fill blanks."""

    logger.info("Lever handler started. Current URL: %s", driver.current_url)

    try:
        personal_info, resume_text = load_candidate_information()
        wait = WebDriverWait(driver, 25)

        time.sleep(2)

        apply_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, APPLY_BUTTON_SELECTOR))
        )

        old_url = driver.current_url
        logger.info("Clicking 'Apply for this job'...")
        apply_button.click()

        try:
            WebDriverWait(driver, 20).until(
                lambda current_driver: current_driver.current_url != old_url
            )
        except TimeoutException:
            logger.info("URL did not change; continuing.")

        wait.until(
            lambda current_driver: current_driver.execute_script(
                "return document.readyState"
            )
            in ("interactive", "complete")
        )

        time.sleep(2)
        upload_resume(driver, wait)

        inspect_and_fill_form(driver, personal_info, resume_text)

        logger.info(
            "Finished processing. Review the form carefully in Chrome. "
            "No application was submitted."
        )

    except (TimeoutException, WebDriverException):
        logger.exception("Browser error in Lever handler.")
        raise
