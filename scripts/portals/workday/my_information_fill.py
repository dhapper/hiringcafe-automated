"""Fill blank Workday My Information fields using the shared AI component.

Loop:
    1. Extract every question on the page (read-only).
    2. If the field is already populated, skip it.
    3. Otherwise ask the AI (common.answer_question) and fill in the answer.

When every required field is filled, it clicks Save and Continue to move to the
next page. It never submits the application itself.
"""

import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common import (
    Question,
    answer_question,
    get_explicit_profile_value,
    get_logger,
    load_candidate_context,
    match_option,
)

logger = get_logger("my_information_fill.py")

# Only ask the AI about blank *required* fields. Workday prefills most of the
# rest from the resume, and optional fields (middle name, extension, ...) are
# not worth an AI guess. Set to False to also fill blank optional fields.
FILL_REQUIRED_ONLY = True

# Click Save and Continue once no required field is blank. It is never clicked
# while any required field is still empty.
SAVE_AND_CONTINUE = False  # navigation now lives in application_pages.py
SAVE_AND_CONTINUE_SELECTOR = 'button[data-automation-id="pageFooterNextButton"]'
NEXT_PAGE_WAIT_SECONDS = 15

MAX_PASSES = 2  # a second pass catches fields revealed by an earlier answer
MAX_MULTISELECT_LEVELS = 3  # some option lists open a sub-list when clicked
OPTION_WAIT_SECONDS = 5

SUPPORTED_KINDS = {"text", "radio", "multiselect", "dropdown"}

# What a Workday dropdown button shows when nothing is chosen.
DROPDOWN_PLACEHOLDERS = {"", "select one", "select"}

# Visible entries in an opened Workday multiselect list. Entries that are
# already-selected items are filtered out in _visible_options().
OPTION_SELECTOR = (
    '[data-automation-id="activeListContainer"] [role="option"], '
    '[data-automation-id="promptOption"], '
    '[role="listbox"] [role="option"]'
)


# Read-only: describes each Workday question container and never changes it.
FIELD_EXTRACTION_SCRIPT = r"""
const clean = (v, n = 240) =>
    String(v || "").replace(/\s+/g, " ").trim().slice(0, n);

const isVisible = (el) => {
    if (!el || !el.isConnected) return false;
    const s = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== "none" && s.visibility !== "hidden" &&
        r.width > 0 && r.height > 0;
};

const textOf = (el) => clean(el ? (el.innerText || el.textContent) : "");
const PLACEHOLDERS = ["", "select one", "select"];

const results = [];

for (const c of document.querySelectorAll('[data-automation-id^="formField-"]')) {
    if (!isVisible(c)) continue;

    const key = c.getAttribute("data-automation-id");
    const labelEl = c.querySelector("legend") || c.querySelector("label");
    const rawLabel = textOf(labelEl);
    const label = rawLabel.replace(/\s*\*\s*$/, "").trim();
    const required =
        /\*\s*$/.test(rawLabel) ||
        Boolean(c.querySelector("[required], [aria-required='true']"));

    let kind = "unknown";
    let options = [];
    let populated = false;
    let current = "";

    const radios = Array.from(c.querySelectorAll('input[type="radio"]'));
    const multiselect = c.querySelector(
        '[data-automation-id="multiselectInputContainer"]'
    );
    const checkbox = c.querySelector('input[type="checkbox"]');
    const textInput = Array.from(c.querySelectorAll(
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]),' +
        ' textarea'
    )).find(isVisible) || null;
    const button = c.querySelector("button");

    if (radios.length) {
        kind = "radio";
        options = radios.map((r) => {
            const lab =
                Array.from(c.querySelectorAll("label"))
                    .find((l) => r.id && l.htmlFor === r.id) ||
                r.closest("label");
            return clean(lab ? textOf(lab) : (r.getAttribute("aria-label") || r.value), 120);
        });
        const checkedIndex = radios.findIndex((r) => r.checked);
        populated = checkedIndex >= 0;
        current = populated ? options[checkedIndex] : "";
    } else if (multiselect) {
        kind = "multiselect";
        const selected = Array.from(
            c.querySelectorAll('[data-automation-id="selectedItem"]')
        );
        populated = selected.length > 0;
        current = selected.map((e) => textOf(e)).join(", ");
    } else if (checkbox) {
        kind = "checkbox";
        populated = checkbox.checked;
        current = checkbox.checked ? "checked" : "";
    } else if (textInput) {
        kind = "text";
        const value = (textInput.value || "").trim();
        populated = value.length > 0;
        current = value;
    } else if (button) {
        kind = "dropdown";
        const value = textOf(button);
        populated = !PLACEHOLDERS.includes(value.toLowerCase());
        current = value;
    }

    results.push({ key, label, required, kind, options, populated, current });
}

return results;
"""


def extract_fields(driver):
    """Return one dict per visible Workday question on the page (read-only)."""
    try:
        return driver.execute_script(FIELD_EXTRACTION_SCRIPT) or []
    except WebDriverException:
        logger.exception("Could not extract the Workday form fields.")
        return []


def _container(driver, key):
    return driver.find_element(By.CSS_SELECTOR, f'[data-automation-id="{key}"]')


def _click(driver, element):
    """Scroll into view, click normally, fall back to a JavaScript click."""
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        element,
    )
    time.sleep(0.2)

    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def _choose_answer(field, options, field_type, context, page_title, question_text=None):
    """Return the value to enter, or "" if the field should be left blank.

    An explicit `question: value` line in the notes file wins over the AI.
    """
    label = field["label"]
    explicit = get_explicit_profile_value(context.personal_info, [label])

    if explicit:
        if not options:
            logger.info("Using explicit notes value for: %s", label)
            return explicit

        matched = match_option(options, explicit)

        if matched:
            logger.info("Using explicit notes value for: %s -> %s", label, matched)
            return matched

    answer = answer_question(
        Question(
            text=question_text or label,
            field_type=field_type,
            options=options,
            page_title=page_title,
        ),
        context,
    )

    if answer.is_skip or answer.action not in {"fill", "select"}:
        return ""

    return answer.value


def _fill_text(driver, field, value):
    container = _container(driver, field["key"])
    element = container.find_element(
        By.CSS_SELECTOR,
        'input:not([type="hidden"]), textarea',
    )

    _click(driver, element)
    element.clear()
    element.send_keys(value)

    if (element.get_attribute("value") or "").strip() != value.strip():
        logger.warning("Text field may not have kept the value: %s", field["label"])
        return False

    logger.info("Filled text field: %s", field["label"])
    return True


def _fill_radio(driver, field, value):
    container = _container(driver, field["key"])
    radios = container.find_elements(By.CSS_SELECTOR, 'input[type="radio"]')

    if len(radios) != len(field["options"]) or value not in field["options"]:
        logger.warning(
            "Could not map '%s' to a radio option for: %s", value, field["label"]
        )
        return False

    index = field["options"].index(value)
    radio = radios[index]
    radio_id = radio.get_attribute("id") or ""

    target = radio

    # Workday radio inputs are visually hidden; clicking the label is reliable.
    for label in container.find_elements(By.TAG_NAME, "label"):
        if radio_id and label.get_attribute("for") == radio_id:
            target = label
            break

    _click(driver, target)

    radios = _container(driver, field["key"]).find_elements(
        By.CSS_SELECTOR,
        'input[type="radio"]',
    )

    if not radios[index].is_selected():
        logger.warning("Radio did not stay selected: %s -> %s", field["label"], value)
        return False

    logger.info("Selected radio answer: %s -> %s", field["label"], value)
    return True


def _visible_options(driver):
    """Return [(text, element)] for the options currently shown in a list."""
    options = []
    seen = set()

    for element in driver.find_elements(By.CSS_SELECTOR, OPTION_SELECTOR):
        try:
            if not element.is_displayed():
                continue

            # Skip the chips that show what is already selected.
            if element.find_elements(
                By.XPATH,
                "./ancestor::*[@data-automation-id='selectedItemList']",
            ):
                continue

            text = (
                element.get_attribute("data-automation-label") or element.text or ""
            ).strip()

        except WebDriverException:
            continue

        if text and text not in seen:
            seen.add(text)
            options.append((text, element))

    return options


def _multiselect_has_selection(driver, key):
    try:
        return bool(
            _container(driver, key).find_elements(
                By.CSS_SELECTOR,
                '[data-automation-id="selectedItem"]',
            )
        )
    except WebDriverException:
        return False


def _close_popup(driver):
    try:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    except WebDriverException:
        pass


def _fill_multiselect(driver, field, context, page_title):
    """Open a Workday searchable list and pick one option, drilling if needed."""
    label = field["label"]
    search = _container(driver, field["key"]).find_element(
        By.CSS_SELECTOR,
        '[data-automation-id="multiselectInputContainer"] input',
    )
    _click(driver, search)

    path = []

    for level in range(1, MAX_MULTISELECT_LEVELS + 1):
        try:
            options = WebDriverWait(driver, OPTION_WAIT_SECONDS).until(
                lambda d: _visible_options(d) or False
            )
        except TimeoutException:
            logger.warning("No options appeared for: %s", label)
            _close_popup(driver)
            return False

        texts = [text for text, _ in options]
        logger.info("Level %d options for '%s': %s", level, label, texts)

        question_text = label
        if path:
            question_text = f"{label} (already chose: {' > '.join(path)})"

        choice = _choose_answer(
            field,
            texts,
            "searchable dropdown; pick the one best option shown "
            "(some options open a sub-list)",
            context,
            page_title,
            question_text=question_text,
        )

        element = next((el for text, el in options if text == choice), None)

        if not choice or element is None:
            logger.info("No answer chosen for '%s'; leaving it blank.", label)
            _close_popup(driver)
            return False

        path.append(choice)
        _click(driver, element)
        time.sleep(1)

        if _multiselect_has_selection(driver, field["key"]):
            logger.info(
                "Selected multiselect answer: %s -> %s", label, " > ".join(path)
            )
            return True

    logger.warning(
        "Did not reach a final selection for '%s' (path: %s).",
        label,
        " > ".join(path),
    )
    _close_popup(driver)
    return False


def _fill_dropdown(driver, field, context, page_title):
    """Open a Workday dropdown button, pick one option, and confirm it stuck."""
    label = field["label"]
    button = _container(driver, field["key"]).find_element(By.CSS_SELECTOR, "button")
    _click(driver, button)

    try:
        options = WebDriverWait(driver, OPTION_WAIT_SECONDS).until(
            lambda d: [
                option
                for option in _visible_options(d)
                if option[0].casefold() not in DROPDOWN_PLACEHOLDERS
            ]
            or False
        )
    except TimeoutException:
        logger.warning("No options appeared for dropdown: %s", label)
        _close_popup(driver)
        return False

    texts = [text for text, _ in options]
    logger.info("Options for '%s': %s", label, texts)

    choice = _choose_answer(field, texts, "dropdown", context, page_title)
    element = next((el for text, el in options if text == choice), None)

    if not choice or element is None:
        logger.info("No answer chosen for '%s'; leaving it blank.", label)
        _close_popup(driver)
        return False

    _click(driver, element)
    time.sleep(1)

    try:
        shown = (
            _container(driver, field["key"])
            .find_element(By.CSS_SELECTOR, "button")
            .text.strip()
        )
    except WebDriverException:
        shown = ""

    if not shown or shown.casefold() in DROPDOWN_PLACEHOLDERS:
        logger.warning("Dropdown did not keep the selection: %s -> %s", label, choice)
        return False

    logger.info("Selected dropdown option: %s -> %s", label, shown)
    return True


def _handle_field(driver, field, context, page_title):
    label = field["label"] or field["key"]

    if field["populated"]:
        logger.info("Skipping populated field: %s (%r)", label, field["current"])
        return

    if FILL_REQUIRED_ONLY and not field["required"]:
        logger.info("Skipping blank optional field: %s", label)
        return

    kind = field["kind"]

    if kind not in SUPPORTED_KINDS:
        logger.warning(
            "Blank %s field '%s' is not handled automatically; "
            "leaving it for manual review.",
            kind,
            label,
        )
        return

    logger.info("Blank required field needs an answer: %s (%s)", label, kind)

    try:
        if kind == "multiselect":
            _fill_multiselect(driver, field, context, page_title)
            return

        if kind == "dropdown":
            _fill_dropdown(driver, field, context, page_title)
            return

        field_type = "text input" if kind == "text" else "radio group"
        value = _choose_answer(
            field,
            field["options"] if kind == "radio" else [],
            field_type,
            context,
            page_title,
        )

        if not value:
            logger.info("No answer chosen for '%s'; leaving it blank.", label)
            return

        if kind == "text":
            _fill_text(driver, field, value)
        else:
            _fill_radio(driver, field, value)

    except WebDriverException:
        logger.exception("Could not fill field: %s", label)


def _my_information_heading_visible(driver):
    try:
        for heading in driver.find_elements(
            By.CSS_SELECTOR,
            "h1, h2, h3, h4, [role='heading']",
        ):
            if (
                heading.is_displayed()
                and heading.text.strip().casefold() == "my information"
            ):
                return True
    except WebDriverException:
        pass

    return False


def _visible_errors(driver):
    """Return any visible validation/error messages on the page."""
    errors = []

    for selector in (
        '[role="alert"]',
        '[data-automation-id*="errorMessage"]',
        '[data-automation-id*="errorBanner"]',
    ):
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = element.text.strip()

                if element.is_displayed() and text:
                    errors.append(text)
        except WebDriverException:
            continue

    return list(dict.fromkeys(errors))


def click_save_and_continue(driver):
    """Click Save and Continue and confirm Workday left My Information."""
    logger.info("Clicking Save and Continue...")

    try:
        button = WebDriverWait(driver, NEXT_PAGE_WAIT_SECONDS).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SAVE_AND_CONTINUE_SELECTOR))
        )
    except TimeoutException:
        logger.warning("The Save and Continue button was not clickable.")
        return False

    _click(driver, button)

    try:
        WebDriverWait(driver, NEXT_PAGE_WAIT_SECONDS).until(
            lambda d: not _my_information_heading_visible(d)
        )
    except TimeoutException:
        errors = _visible_errors(driver)
        logger.warning(
            "Still on My Information after Save and Continue. " "Visible errors: %s",
            errors or "none detected",
        )
        return False

    logger.info("Moved past My Information.")
    return True


def fill_my_information(driver, context=None):
    """Fill blank required My Information fields. Returns labels still blank."""
    logger.info("=" * 72)
    logger.info("Starting Workday My Information fill (no submission).")

    if context is None:
        try:
            context = load_candidate_context()
        except (FileNotFoundError, ValueError):
            logger.exception("Could not load candidate context; skipping AI fill.")
            return []

    page_title = driver.title
    attempted = set()

    for pass_number in range(1, MAX_PASSES + 1):
        fields = [f for f in extract_fields(driver) if f["key"] not in attempted]

        if not fields:
            break

        logger.info("Pass %d: reviewing %d field(s).", pass_number, len(fields))

        for field in fields:
            attempted.add(field["key"])
            _handle_field(driver, field, context, page_title)

        time.sleep(1)  # let Workday reveal any follow-up fields

    remaining = [
        f["label"] or f["key"]
        for f in extract_fields(driver)
        if f["required"] and not f["populated"]
    ]

    if remaining:
        logger.warning("Required fields still blank; please review manually:")

        for label in remaining:
            logger.warning("  - %s", label)
    else:
        logger.info("No required fields are blank.")

    if remaining:
        logger.warning(
            "Not clicking Save and Continue because required fields are "
            "still blank. Please finish them manually."
        )
    elif not SAVE_AND_CONTINUE:
        logger.info("Save and Continue is disabled; staying on this page.")
    else:
        click_save_and_continue(driver)

    logger.info("My Information fill finished. Application NOT submitted.")
    logger.info("=" * 72)

    return remaining
