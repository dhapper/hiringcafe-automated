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
    # When a custom field has its own real prompt, prefer that prompt over
    # metadata inherited from a neighbouring/custom card. This prevents logs
    # such as "Work Authorization | What are your salary requirements?".
    field_looks_like_prompt = bool(
        field_title
        and field_title.casefold() != question_title.casefold()
        and ("?" in field_title or len(field_title.split()) >= 5)
    )
    if field_looks_like_prompt:
        parts.append(field_title)
    elif question_title:
        parts.append(question_title)
        if field_title and field_title.casefold() != question_title.casefold():
            parts.append(field_title)
    elif field_title:
        parts.append(field_title)
    if label:
        normalized_label = label.casefold()
        # Nearby label text can include a parent card's unrelated metadata.
        if not any(part.casefold() in normalized_label for part in parts):
            if len(label) <= 180 and not any(
                marker in normalized_label
                for marker in (
                    "work authorization (canada)",
                    "onsite requirement - toronto office",
                )
            ):
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
    """Return the human-readable label for a form control."""
    element_id = element.get_attribute("id") or ""
    if element_id:
        try:
            labels = element.parent.find_elements(
                By.XPATH, f"//label[@for={json.dumps(element_id)}]"
            )
            for label in labels:
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
    # Lever sometimes renders labels beside the input rather than wrapping it.
    for xpath in (
        "./following-sibling::label[1]",
        "./following-sibling::*[1]",
        "./parent::*",
        "./ancestor::li[1]",
    ):
        try:
            node = element.find_element(By.XPATH, xpath)
            text = " ".join((node.text or "").split())
            value = (element.get_attribute("value") or "").strip()
            if text and text.casefold() != value.casefold() and len(text) <= 180:
                # Prefer the exact option text over a container containing many choices.
                if len(text.splitlines()) <= 2:
                    return text
        except WebDriverException:
            continue
    return (
        element.get_attribute("aria-label")
        or element.get_attribute("title")
        or element.get_attribute("value")
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
Use related, relevant experience when appropriate.
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


def known_text_answer(question, personal_info):
    """Return explicit profile URLs for fields that ask for a personal website."""
    q = (question or "").casefold()
    if any(
        term in q
        for term in (
            "other website",
            "portfolio",
            "personal website",
            "website url",
            "personal url",
        )
    ):
        portfolio = get_explicit_profile_value(
            personal_info,
            ["portfolio url", "portfolio", "personal website", "website url"],
        )
        if portfolio:
            return portfolio
    if "linkedin" in q:
        value = get_explicit_profile_value(personal_info, ["linkedin url", "linkedin"])
        if value:
            return value
    if "github" in q:
        value = get_explicit_profile_value(personal_info, ["github url", "github"])
        if value:
            return value
    return ""


def process_text_field(
    element,
    question,
    personal_info,
    resume_text,
    page_title,
):
    """Answer blank fields from explicit profile data first, then ask Ollama."""
    if not field_is_empty(element):
        logger.info("Skipping populated field: %s", question)
        return
    if is_sensitive(question):
        logger.info("Skipping sensitive field: %s", question)
        return
    explicit_value = known_text_answer(question, personal_info)
    if explicit_value:
        if AUTO_FILL_FIELDS and field_is_empty(element):
            element.send_keys(explicit_value)
            logger.info("Filled field from explicit profile URL: %s", question)
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


def get_explicit_profile_value(personal_info, aliases):
    """Find a value explicitly written in my_info.txt for one of the given keys."""
    alias_set = {re.sub(r"[^a-z0-9]+", "", alias.casefold()) for alias in aliases}
    for raw_line in personal_info.splitlines():
        line = raw_line.strip().lstrip("-*• ").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
        if normalized_key in alias_set and value.strip():
            return value.strip()
    return ""


def normalize_choice(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def match_option(option_texts, explicit_value):
    """Match a value to one unique available option, tolerating harmless wording differences."""
    wanted = normalize_choice(explicit_value)
    if not wanted:
        return None
    normalized_options = [
        (option, normalize_choice(option)) for option in option_texts if option
    ]
    exact = [
        option for option, normalized in normalized_options if normalized == wanted
    ]
    if len(exact) == 1:
        return exact[0]
    # Match only when the configured value is a complete prefix or substring of
    # exactly one option. This supports e.g. "Asian" -> "Asian (Not Hispanic or Latino)".
    matches = [
        option
        for option, normalized in normalized_options
        if normalized and (normalized.startswith(wanted) or wanted in normalized)
    ]
    if len(matches) == 1:
        return matches[0]
    # Handle common EEO wording differences while still requiring one unique option.
    aliases = {
        "asian": ("asian",),
        "black": ("black", "africanamerican"),
        "blackafricanamerican": ("black", "africanamerican"),
        "white": ("white",),
        "hispanic": ("hispanic", "latino", "latina", "latinx"),
        "hispanicorlatino": ("hispanic", "latino", "latina", "latinx"),
        "nativeamerican": ("nativeamerican", "americanindian", "alaskanative"),
        "americanindianoralaskanative": (
            "nativeamerican",
            "americanindian",
            "alaskanative",
        ),
        "pacificislander": ("pacificislander", "nativehawaiian"),
        "nativehawaiianorotherpacificislander": ("pacificislander", "nativehawaiian"),
    }
    tokens = aliases.get(wanted)
    if tokens:
        matches = [
            option
            for option, normalized in normalized_options
            if any(token in normalized for token in tokens)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def build_group_question(elements, metadata_by_prefix):
    """Identify each radio/checkbox group's actual prompt, not just its parent card title."""
    if not elements:
        return "Unlabelled application question", None, None

    element = elements[0]
    _, metadata = get_metadata_for_element(element, metadata_by_prefix)
    definition = get_field_definition(element, metadata)
    metadata_title = str(metadata.get("text", "")).strip() if metadata else ""
    field_title = str(definition.get("text", "")).strip() if definition else ""

    # Lever may put a broad card title in metadata.text (e.g. "Work Authorization
    # (Canada)") while each individual field has its own question in fields[].text.
    # Prefer that specific prompt when it is not just the card title or an option.
    if (
        field_title
        and field_title.casefold() != metadata_title.casefold()
        and ("?" in field_title or len(field_title.split()) >= 5)
    ):
        return " ".join(field_title.split()), metadata, definition

    # The visible fieldset legend is often the most faithful source for the
    # question shown to the applicant.
    try:
        fieldset = element.find_element(By.XPATH, "./ancestor::fieldset[1]")
        legend = " ".join(fieldset.find_element(By.TAG_NAME, "legend").text.split())
        option_labels = {normalize_choice(get_choice_label(item)) for item in elements}
        if legend and normalize_choice(legend) not in option_labels:
            if legend.casefold() != metadata_title.casefold():
                return legend, metadata, definition
    except WebDriverException:
        pass

    # Try nearby visible text before falling back to the generic metadata title.
    # Remove individual option labels so "Yes" and "No" aren't treated as prompts.
    nearby = get_nearby_text(element)
    option_labels = {normalize_choice(get_choice_label(item)) for item in elements}
    pieces = []
    for line in re.split(r"[\n|]", nearby):
        line = " ".join(line.split()).strip()
        if not line or normalize_choice(line) in option_labels:
            continue
        if metadata_title and line.casefold() == metadata_title.casefold():
            continue
        if len(line) <= 180:
            pieces.append(line)
    visible_question = " | ".join(dict.fromkeys(pieces)).strip()
    if visible_question and (
        "?" in visible_question or len(visible_question.split()) >= 5
    ):
        return visible_question, metadata, definition

    if field_title:
        return field_title, metadata, definition
    if metadata_title:
        return metadata_title, metadata, definition

    fallback = (
        element.get_attribute("name")
        or element.get_attribute("id")
        or "Unlabelled application question"
    ).strip()
    return fallback, metadata, definition


def get_demographic_profile_value(question, elements, personal_info):
    """Return an explicitly configured EEO demographic answer; never infer demographics."""
    text = (question or "").casefold()
    names = " ".join(
        (element.get_attribute("name") or "") for element in elements
    ).casefold()
    identity = f"{text} {names}"
    if "race" in identity or "ethnicity" in identity:
        return "Race", get_explicit_profile_value(
            personal_info,
            ["race", "race/ethnicity", "ethnicity", "eeo race", "eeo race/ethnicity"],
        )
    if "gender" in identity:
        return "Gender", get_explicit_profile_value(
            personal_info, ["gender", "gender identity", "eeo gender"]
        )
    if "veteran" in identity or "military status" in identity:
        return "Veteran status", get_explicit_profile_value(
            personal_info,
            ["veteran status", "veteran", "military veteran status", "eeo veteran"],
        )
    return "", ""


def process_dropdown(
    element, question, metadata_definition, personal_info, resume_text, page_title
):
    """Select dropdowns safely; EEO demographics must be explicitly configured in my_info.txt."""
    if not field_is_empty(element):
        logger.info("Skipping selected dropdown: %s", question)
        return
    if is_sensitive(question):
        logger.info("Skipping sensitive dropdown: %s", question)
        return
    options = [
        option.text.strip() for option in Select(element).options if option.text.strip()
    ]
    field_name = (element.get_attribute("name") or "").casefold()
    explicit_key, explicit_value = get_demographic_profile_value(
        question, [element], personal_info
    )
    # Use the field name too because some EEO controls have little or no visible label.
    if not explicit_key:
        if field_name.endswith("eeo[race]"):
            explicit_key = "Race"
            explicit_value = get_explicit_profile_value(
                personal_info,
                [
                    "race",
                    "race/ethnicity",
                    "ethnicity",
                    "eeo race",
                    "eeo race/ethnicity",
                ],
            )
        elif field_name.endswith("eeo[gender]"):
            explicit_key = "Gender"
            explicit_value = get_explicit_profile_value(
                personal_info, ["gender", "gender identity", "eeo gender"]
            )
        elif field_name.endswith("eeo[veteran]"):
            explicit_key = "Veteran status"
            explicit_value = get_explicit_profile_value(
                personal_info,
                ["veteran status", "veteran", "military veteran status", "eeo veteran"],
            )
    if explicit_key:
        if not explicit_value:
            logger.info(
                "Skipping %s dropdown: no explicit value found in my_info.txt.",
                explicit_key,
            )
            return
        matched = match_option(options, explicit_value)
        if not matched and explicit_key == "Race":
            # Support profile instructions like: "South Asian (Sri Lankan), if no option exists use 'Asian'".
            fallback = re.search(
                r"if\s+no\s+option\s+exists\s+use\s+['\"]?([^'\".,;]+)",
                explicit_value,
                flags=re.IGNORECASE,
            )
            if fallback:
                fallback_value = fallback.group(1).strip()
                matched = match_option(options, fallback_value)
                if matched:
                    logger.info(
                        "Race value '%s' unavailable; using configured fallback '%s'.",
                        explicit_value,
                        fallback_value,
                    )
        if not matched:
            logger.warning(
                "Could not safely match configured %s value '%s' to available options %s. Leaving blank.",
                explicit_key,
                explicit_value,
                options,
            )
            return
        if AUTO_FILL_FIELDS and field_is_empty(element):
            Select(element).select_by_visible_text(matched)
            logger.info(
                "Selected configured %s dropdown option: %s", explicit_key, matched
            )
        return
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
    matched = match_option(options, answer["value"])
    if not matched:
        logger.warning(
            "Answer did not match one unique dropdown option for '%s': %s. Available options: %s",
            question,
            answer["value"],
            options,
        )
        return
    if AUTO_FILL_FIELDS and field_is_empty(element):
        Select(element).select_by_visible_text(matched)
        logger.info("Selected dropdown option for: %s -> %s", question, matched)


def click_control(element, description="form control"):
    """Scroll a control into view, try a normal click, then safely fall back if an overlay intercepts it."""
    try:
        driver = element.parent
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});",
            element,
        )
        time.sleep(0.2)
        try:
            element.click()
        except WebDriverException:
            # Lever sometimes has sticky headers/overlays that intercept the real click.
            # Clicking the associated label is the preferred fallback.
            element_id = element.get_attribute("id") or ""
            label_clicked = False
            if element_id:
                try:
                    label = driver.find_element(
                        By.XPATH, f"//label[@for={json.dumps(element_id)}]"
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                        label,
                    )
                    time.sleep(0.1)
                    try:
                        label.click()
                    except WebDriverException:
                        driver.execute_script("arguments[0].click();", label)
                    label_clicked = True
                except WebDriverException:
                    label_clicked = False
            if not label_clicked:
                # Use the DOM click only as a fallback; callers verify the resulting state.
                driver.execute_script("arguments[0].click();", element)
        return True
    except WebDriverException:
        logger.exception("Could not scroll/click %s", description)
        return False


def process_radio_groups(
    groups, metadata_by_prefix, personal_info, resume_text, page_title
):
    """Treat each radio group as one question and robustly map the answer to a real option."""
    for name, elements in groups.items():
        try:
            if any(element.is_selected() for element in elements):
                logger.info("Skipping answered radio group: %s", name)
                continue
            question, metadata, definition = build_group_question(
                elements, metadata_by_prefix
            )
            labels = [get_choice_label(element) for element in elements]
            options = [label for label in labels if label]
            if not options:
                options = get_option_labels(elements[0], definition)
            if is_sensitive(question):
                logger.info("Skipping sensitive radio group: %s", question)
                continue
            demographic_key, demographic_value = get_demographic_profile_value(
                question, elements, personal_info
            )
            if demographic_key:
                if not demographic_value:
                    logger.info(
                        "Skipping %s radio group: no explicit value in my_info.txt. Question: %s",
                        demographic_key,
                        question,
                    )
                    continue
                answer = {
                    "action": "select",
                    "value": demographic_value,
                    "reason": "Explicit demographic value from my_info.txt",
                }
            else:
                known = known_yes_no_answer(question, personal_info)
                if known and any(
                    normalize_choice(option) in {"yes", "no", "y", "n", "true", "false"}
                    for option in options
                ):
                    answer = {
                        "action": "select",
                        "value": known,
                        "reason": "Supported by explicit candidate information",
                    }
                else:
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
            matched = match_option(options, answer["value"])
            if not matched:
                logger.warning(
                    "Radio answer '%s' did not uniquely match options for '%s': %s",
                    answer["value"],
                    question,
                    options,
                )
                continue
            target = next(
                (
                    element
                    for element in elements
                    if normalize_choice(get_choice_label(element))
                    == normalize_choice(matched)
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        element
                        for element in elements
                        if normalize_choice(element.get_attribute("value") or "")
                        == normalize_choice(matched)
                    ),
                    None,
                )
            if target is None:
                logger.warning(
                    "Could not map matched radio option '%s' back to a control for '%s'.",
                    matched,
                    question,
                )
                continue
            if AUTO_FILL_FIELDS and not target.is_selected():
                if (
                    click_control(target, f"radio answer for {question}")
                    and not target.is_selected()
                ):
                    logger.warning(
                        "Radio control did not remain selected for: %s -> %s",
                        question,
                        matched,
                    )
                else:
                    logger.info(
                        "Selected radio answer for: %s -> %s", question, matched
                    )
        except WebDriverException:
            logger.exception("Could not process radio group %s", name)


def process_pronouns(driver, personal_info):
    """Handle Lever's built-in pronouns list separately from custom questions."""
    configured = get_explicit_profile_value(
        personal_info, ["pronouns", "pronoun", "preferred pronouns"]
    )
    if not configured:
        logger.info(
            "Pronouns skipped: no explicit pronouns value found in my_info.txt."
        )
        return
    controls = []
    for element in driver.find_elements(
        By.CSS_SELECTOR, 'input[type="checkbox"][name="pronouns"]'
    ):
        try:
            if element.is_displayed() and element.is_enabled():
                controls.append(element)
        except WebDriverException:
            continue
    if not controls:
        return
    # Support a comma/semicolon-separated list of pronouns, but only select options
    # that are actually present on the form.
    requested = [part.strip() for part in re.split(r"[,;]", configured) if part.strip()]
    labels = [(element, get_choice_label(element)) for element in controls]
    selected_targets = []
    for requested_value in requested:
        match = match_option([label for _, label in labels if label], requested_value)
        if not match:
            # Try the actual input value, which Lever uses for some built-in options.
            match = match_option(
                [
                    (element.get_attribute("value") or "").strip()
                    for element in controls
                ],
                requested_value,
            )
        if match:
            for element, label in labels:
                actual = label or (element.get_attribute("value") or "").strip()
                if normalize_choice(actual) == normalize_choice(match):
                    selected_targets.append(element)
                elif normalize_choice(
                    element.get_attribute("value") or ""
                ) == normalize_choice(match):
                    selected_targets.append(element)
    # De-duplicate Selenium elements by their stable element IDs.
    unique_targets = {element.id: element for element in selected_targets}
    if not unique_targets:
        logger.warning(
            "Configured pronouns '%s' did not match the available options. Leaving pronouns unchanged.",
            configured,
        )
        return
    if not AUTO_FILL_FIELDS:
        logger.info("Pronouns identified but automatic filling is disabled.")
        return
    # The user has explicitly configured their pronouns, so correct a stale accidental
    # 'Other' selection if a matching standard option exists.
    for element in controls:
        value = (element.get_attribute("value") or "").strip()
        label = get_choice_label(element)
        is_other = value.casefold() == "other" or label.casefold() == "other"
        should_select = element.id in unique_targets
        if is_other and should_select:
            continue
        if is_other and element.is_selected() and unique_targets:
            click_control(element, "pronouns Other checkbox")
            logger.info("Unselected accidental Pronouns: Other selection.")
        elif should_select and not element.is_selected():
            click_control(element, f"pronouns {label or value}")
            logger.info("Selected configured pronouns: %s", label or value)


def get_choice_label(element):
    """Read the label for one radio/checkbox option without capturing the whole group."""
    element_id = element.get_attribute("id") or ""
    if element_id:
        try:
            for label in element.parent.find_elements(
                By.XPATH, f"//label[@for={json.dumps(element_id)}]"
            ):
                text = " ".join((label.text or "").split())
                if text:
                    return text
        except WebDriverException:
            pass
    try:
        label = element.find_element(By.XPATH, "./ancestor::label[1]")
        text = " ".join((label.text or "").split())
        if text:
            return text
    except WebDriverException:
        pass
    value = (element.get_attribute("value") or "").strip()
    # A sibling label is safer than reading a large parent container.
    try:
        sibling = element.find_element(By.XPATH, "./following-sibling::label[1]")
        text = " ".join((sibling.text or "").split())
        if text:
            return text
    except WebDriverException:
        pass
    return value or (element.get_attribute("aria-label") or "").strip()


def known_yes_no_answer(question, personal_info):
    """Return Yes/No only when supported by explicit profile facts or Canadian citizenship."""
    q = (question or "").casefold()
    info = (personal_info or "").casefold()
    if any(
        term in q
        for term in (
            "eligible to work for any employer",
            "eligible to work",
            "legally eligible to work",
            "authorized to work",
        )
    ):
        explicit = get_explicit_profile_value(
            personal_info,
            [
                "eligible to work in Canada",
                "work eligibility in Canada",
                "work authorization in Canada",
                "eligible to work for any employer in Canada",
            ],
        )
        if normalize_choice(explicit) in {"yes", "no"}:
            return "Yes" if normalize_choice(explicit) == "yes" else "No"
        if "canadian citizen" in info:
            return "Yes"
    if any(
        term in q
        for term in (
            "visa sponsorship",
            "require sponsorship",
            "sponsorship/transfer",
            "sponsorship or transfer",
            "require a visa",
            "need sponsorship",
        )
    ):
        explicit = get_explicit_profile_value(
            personal_info,
            [
                "require visa sponsorship",
                "visa sponsorship required",
                "need sponsorship in Canada",
                "require sponsorship in Canada",
                "visa sponsorship/transfer",
            ],
        )
        if normalize_choice(explicit) in {"yes", "no"}:
            return "Yes" if normalize_choice(explicit) == "yes" else "No"
        if "canadian citizen" in info:
            return "No"
    if (
        "toronto office" in q
        or ("work from" in q and "office" in q)
        or ("office" in q and ("days a week" in q or "onsite" in q))
    ):
        explicit = get_explicit_profile_value(
            personal_info,
            [
                "Toronto office 4 to 5 days a week",
                "Toronto office availability",
                "willing to work onsite",
                "onsite availability",
                "Toronto onsite availability",
                "Toronto office schedule",
            ],
        )
        normalized = normalize_choice(explicit)
        if normalized in {
            "yes",
            "y",
            "willing",
            "available",
            "comfortable",
            "comfortablewiththisschedule",
            "true",
        }:
            return "Yes"
        if normalized in {
            "no",
            "n",
            "notwilling",
            "unavailable",
            "notcomfortable",
            "false",
        }:
            return "No"
        # The profile's common answer explicitly says commuting is acceptable
        # when the role is in/around the GTA or the candidate relocates.
        commute_answer = re.search(
            r"##\s*Are you willing to commute\?\s*\n\s*(yes\b[^\n]*)",
            personal_info,
            flags=re.IGNORECASE,
        )
        if commute_answer and any(
            place in q for place in ("toronto", "gta", "office", "onsite")
        ):
            return "Yes"
    return ""


def process_checkbox_groups(
    groups, metadata_by_prefix, personal_info, resume_text, page_title
):
    """Handle checkbox groups and standalone boolean checkboxes without confusing them with option groups."""
    for name, elements in groups.items():
        try:
            if name == "pronouns" or any(
                (element.get_attribute("name") or "") == "pronouns"
                for element in elements
            ):
                logger.info(
                    "Pronouns handled separately; skipping generic checkbox logic."
                )
                continue
            if any(element.is_selected() for element in elements):
                logger.info("Skipping answered checkbox group: %s", name)
                continue
            question, metadata, definition = build_group_question(
                elements, metadata_by_prefix
            )
            labels = [get_choice_label(element) for element in elements]
            pronoun_markers = " ".join(
                [
                    question or "",
                    name or "",
                    *[(element.get_attribute("id") or "") for element in elements],
                    *[
                        (element.get_attribute("aria-label") or "")
                        for element in elements
                    ],
                    *labels,
                ]
            ).casefold()
            if "pronoun" in pronoun_markers:
                logger.info(
                    "Pronouns handled separately; skipping generic checkbox logic: %s",
                    question,
                )
                continue
            options = [label for label in labels if label]
            if not options:
                options = get_option_labels(elements[0], definition)
            if not options:
                options = [
                    (element.get_attribute("value") or "").strip()
                    for element in elements
                ]
            if is_sensitive(question):
                logger.info("Skipping sensitive checkbox group: %s", question)
                continue
            demographic_key, demographic_value = get_demographic_profile_value(
                question, elements, personal_info
            )
            if demographic_key:
                if not demographic_value:
                    logger.info(
                        "Skipping %s checkbox group: no explicit value in my_info.txt. Question: %s",
                        demographic_key,
                        question,
                    )
                    continue
                matched = match_option(options, demographic_value)
                if not matched:
                    logger.warning(
                        "Configured %s value '%s' did not match checkbox options %s. Leaving unchanged.",
                        demographic_key,
                        demographic_value,
                        options,
                    )
                    continue
                answer = {
                    "action": "select",
                    "value": matched,
                    "reason": "Explicit demographic value from my_info.txt",
                }
            else:
                normalized_options = {
                    normalize_choice(option) for option in options if option
                }
                is_yes_no = (
                    bool(normalized_options)
                    and normalized_options.issubset(
                        {"yes", "no", "y", "n", "true", "false"}
                    )
                    and len(elements) > 1
                )
                known = known_yes_no_answer(question, personal_info)
                if is_yes_no and known:
                    answer = {
                        "action": "select",
                        "value": known,
                        "reason": "Supported by explicit candidate information",
                    }
                elif len(elements) == 1:
                    # A lone checkbox represents a boolean question, not a choice whose label must match.
                    if known == "Yes":
                        answer = {
                            "action": "check",
                            "value": "",
                            "reason": "Supported by explicit candidate information",
                        }
                    elif known == "No":
                        answer = {
                            "action": "uncheck",
                            "value": "",
                            "reason": "Supported by explicit candidate information",
                        }
                    else:
                        answer = ask_ollama(
                            question=question,
                            field_type="single checkbox (answer with check, uncheck, or skip)",
                            personal_info=personal_info,
                            resume_text=resume_text,
                            options=["checked/Yes", "unchecked/No"],
                            page_title=page_title,
                        )
                else:
                    answer = ask_ollama(
                        question=question,
                        field_type=(
                            "single-choice checkbox group"
                            if is_yes_no
                            else "checkbox group"
                        ),
                        personal_info=personal_info,
                        resume_text=resume_text,
                        options=options,
                        page_title=page_title,
                    )
            log_suggestion(question, answer)
            if not AUTO_FILL_FIELDS or answer["action"] == "skip":
                continue
            if len(elements) == 1:
                target = elements[0]
                if answer["action"] == "check":
                    if not target.is_selected():
                        click_control(target, f"checkbox for {question}")
                        if target.is_selected():
                            logger.info("Checked standalone checkbox for: %s", question)
                        else:
                            logger.warning(
                                "Checkbox did not remain checked for: %s", question
                            )
                elif answer["action"] == "uncheck":
                    if target.is_selected():
                        click_control(target, f"checkbox for {question}")
                        logger.info("Unchecked standalone checkbox for: %s", question)
                elif answer["action"] in {"select", "fill"}:
                    if (
                        normalize_choice(answer["value"])
                        in {"yes", "true", "check", "checked"}
                        and not target.is_selected()
                    ):
                        click_control(target, f"checkbox for {question}")
                        if target.is_selected():
                            logger.info("Checked standalone checkbox for: %s", question)
                        else:
                            logger.warning(
                                "Checkbox did not remain checked for: %s", question
                            )
                continue
            if answer["action"] not in {"select", "fill", "check"}:
                continue
            matched = match_option(options, answer["value"])
            if not matched and answer["action"] == "check" and len(elements) == 1:
                target = elements[0]
            else:
                target = (
                    next(
                        (
                            element
                            for element in elements
                            if normalize_choice(get_choice_label(element))
                            == normalize_choice(matched)
                        ),
                        None,
                    )
                    if matched
                    else None
                )
            if target is None:
                logger.warning(
                    "Checkbox answer '%s' did not match an option for '%s': %s",
                    answer["value"],
                    question,
                    options,
                )
                continue
            if not target.is_selected():
                if is_yes_no:
                    for element in elements:
                        if element.is_selected():
                            click_control(
                                element, f"checkbox group option for {question}"
                            )
                click_control(target, f"checkbox answer for {question}")
                if target.is_selected():
                    logger.info(
                        "Selected checkbox answer for: %s -> %s",
                        question,
                        matched or answer["value"],
                    )
                else:
                    logger.warning(
                        "Checkbox did not remain selected for: %s -> %s",
                        question,
                        matched or answer["value"],
                    )
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
    process_pronouns(driver, personal_info)
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
            field_name = (element.get_attribute("name") or "").casefold()
            if (
                field_name in {"pronounsother", "pronouns[other]"}
                or "pronounsother" in field_name
            ):
                logger.info(
                    "Skipping Pronouns: Other text field; use only an explicit pronouns configuration."
                )
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
    # Report required fields still blank so the user can review them manually.
    unanswered = []
    for element in driver.find_elements(By.CSS_SELECTOR, "input, select, textarea"):
        try:
            if not element.is_displayed() or not element.is_enabled():
                continue
            if (element.get_attribute("type") or "").lower() in {
                "hidden",
                "file",
                "submit",
                "button",
                "radio",
                "checkbox",
            }:
                continue
            if element.get_attribute("required") is not None and field_is_empty(
                element
            ):
                question, _, _, _ = build_question(element, metadata_by_prefix)
                unanswered.append(question)
        except WebDriverException:
            continue
    for name, groups in (("radio", radio_groups), ("checkbox", checkbox_groups)):
        for group_name, elements in groups.items():
            try:
                required = any(
                    element.get_attribute("required") is not None
                    for element in elements
                )
                if required and not any(element.is_selected() for element in elements):
                    question, _, _, _ = build_question(elements[0], metadata_by_prefix)
                    unanswered.append(question)
            except WebDriverException:
                continue
    if unanswered:
        logger.warning("Required fields that may still need your review:")
        for question in dict.fromkeys(unanswered):
            logger.warning("  - %s", question)
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
