"""Portal-independent filler for the output of common.scrape_content.

Supports native text inputs, textareas, native <select>, Ant Design selects,
Greenhouse React Select controls, radio groups, checkbox groups and single
checkboxes. File inputs are skipped. Consent/acknowledgement checkboxes are
never ticked unless explicitly allowed below.

This module never submits a form and never clicks Next/Submit.
"""

import re
import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select, WebDriverWait

from common import (
    Question,
    answer_question,
    get_explicit_profile_value,
    get_logger,
    is_sensitive,
    load_candidate_context,
    match_option,
    normalize_choice,
)
from common.salary import pick_salary_option, posted_salary_max
from common.scrape_content import KNOWN_FIELD_LABELS

logger = get_logger("fill_content.py")

OPTIONAL_FIELD_MODE = "profile"
ACCEPT_CONSENT_PHRASES = ()

CONSENT_TERMS = (
    "acknowledg",
    "consent",
    "i agree",
    "agree to",
    "certify",
    "authorize",
    "authorise",
    "terms and conditions",
    "privacy",
    "declare",
    "declaration",
)

DROPDOWN_WAIT_SECONDS = 5
MAX_SCROLL_STEPS = 100
SCRIPT_TIMEOUT_SECONDS = 60

SKIP_TYPES = {"file", "hidden", "submit", "button", "reset", "image", "password"}
CHOICE_TYPES = {"radio", "checkbox", "switch"}
CHOICE_SELECTORS = {
    "radio": (".ant-radio-wrapper, .ant-radio-button-wrapper", 'input[type="radio"]'),
    "checkbox": (".ant-checkbox-wrapper", 'input[type="checkbox"]'),
}
PLACEHOLDER_OPTIONS = {"", "select", "select one", "select an option", "please select"}

PROFILE_ALIASES = {
    "email": ["email address", "email"],
    "linkedin": ["linkedin profile", "linkedin url", "linkedin"],
    "github": ["github url", "github profile", "github"],
    "portfolio": ["portfolio url", "portfolio", "personal website", "website url"],
    "address line 1": ["address line 1", "street address", "address"],
    "postal": ["postal code", "zip code", "zip", "postal"],
    "zip": ["postal code", "zip code", "zip", "postal"],
    "province": ["province", "state", "state/province"],
    "dialing code": ["area code", "country code", "country dialing code"],
    "country code": ["area code", "country code", "country dialing code"],
    "accommodation": [
        "accommodation required",
        "accommodations required",
        "accommodation",
    ],
}

AI_OPTIONAL_KEYWORDS = (
    "state",
    "province",
    "region",
    "country",
    "city",
    "prefix",
    "dialing code",
    "area code",
)
DEMOGRAPHIC_TERMS = (
    "gender",
    "race",
    "ethnic",
    "veteran",
    "disabilit",
    "sexual orientation",
    "religion",
    "indigenous",
    "aboriginal",
    "visible minority",
    "pronoun",
    "marital",
    "accommodation",
)

ANT_SELECT_XPATH = (
    "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), "
    "' ant-select ')][1]"
)
ANT_DROPDOWN_SELECTOR = ".ant-select-dropdown:not(.ant-select-dropdown-hidden)"
REACT_SELECT_MENU_SELECTOR = ".select__menu, [class*='select__menu']"
REACT_SELECT_OPTION_SELECTOR = ".select__option, [role='option']"

_KNOWN_IDS_BY_LABEL = {
    label.casefold(): field_id for field_id, label in KNOWN_FIELD_LABELS.items()
}


def _is_demographic(text):
    lowered = (text or "").casefold()
    return any(term in lowered for term in DEMOGRAPHIC_TERMS)


def _click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.2)
    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def _profile_keys(label):
    candidates = [label]
    lowered = (label or "").casefold()
    for keyword, aliases in PROFILE_ALIASES.items():
        if keyword in lowered:
            candidates.extend(aliases)
    return candidates


def _explicit_value(context, label):
    return get_explicit_profile_value(context.personal_info, _profile_keys(label))


def _is_dialing_code(label):
    lowered = (label or "").casefold()
    return "dialing code" in lowered or "area code" in lowered


def _paired_phone_value(controls, index):
    for control in controls[index + 1 : index + 4]:
        label = (control.get("label") or "").casefold()
        if "phone" in label and (control.get("type") or "").lower() in {"text", "tel"}:
            return (control.get("value") or "").strip()
    return None


_FALLBACK_CLAUSE = re.compile(
    r"if\s+no\s+option\s+exists\s+use\s+['\"]?([^'\".,;]+)", re.IGNORECASE
)


def _explicit_choices(explicit):
    match = _FALLBACK_CLAUSE.search(explicit)
    if not match:
        return [explicit]
    primary = explicit[: match.start()].strip(" ,;")
    return [choice for choice in (primary, match.group(1).strip()) if choice]


def _posted_salary_choice(label, options, context):
    lowered = (label or "").casefold()
    if not options or not any(word in lowered for word in ("salary", "compensation")):
        return ""
    target = posted_salary_max(context.job_posting)
    if not target:
        logger.info("No posted salary range found; using the notes/AI answer.")
        return ""
    choice = pick_salary_option(options, target)
    if choice:
        logger.info(
            "Posted salary range tops out at $%s; choosing '%s' for: %s",
            f"{target:,}",
            choice,
            label,
        )
    return choice or ""


def _choose_answer(label, options, field_type, context, page_title, allow_ai=True):
    salary_choice = _posted_salary_choice(label, options, context)
    if salary_choice:
        return salary_choice

    explicit = _explicit_value(context, label)
    if explicit:
        for choice in _explicit_choices(explicit):
            if not options:
                logger.info("Using explicit notes value for: %s", label)
                return choice
            matched = match_option(options, choice)
            if matched:
                logger.info("Using explicit notes value for: %s -> %s", label, matched)
                return matched

    if not allow_ai:
        return ""
    answer = answer_question(
        Question(
            text=label, field_type=field_type, options=options, page_title=page_title
        ),
        context,
    )
    if answer.is_skip or answer.action not in {"fill", "select"}:
        return ""
    return answer.value


# --- locating controls -------------------------------------------------------


def _first(driver, selector):
    found = driver.find_elements(By.CSS_SELECTOR, selector)
    return found[0] if found else None


def _ant_wrapper(element):
    found = element.find_elements(By.XPATH, ANT_SELECT_XPATH)
    return found[0] if found else None


def _react_select_wrapper(element):
    """Return the Greenhouse React Select shell containing this input."""
    if element is None:
        return None
    if "select-shell" in (element.get_attribute("class") or "").split():
        return element
    found = element.find_elements(
        By.XPATH,
        "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' select-shell ')][1]",
    )
    return found[0] if found else None


def _ant_select_by_form_item_label(driver, label):
    wanted = (label or "").strip().casefold()
    matches = []
    for item in driver.find_elements(By.CSS_SELECTOR, ".ant-form-item"):
        try:
            labels = item.find_elements(By.CSS_SELECTOR, "label")
            text = (labels[0].text if labels else "").replace("*", "").strip()
            if text.casefold() == wanted:
                matches.extend(item.find_elements(By.CSS_SELECTOR, ".ant-select"))
        except WebDriverException:
            continue
    return matches[0] if len(matches) == 1 else None


def _locate(driver, control):
    """Return (element, wrapper), where wrapper is an Ant or React Select shell."""
    field_id = control.get("id") or ""
    name = control.get("name") or ""
    label = (control.get("label") or "").strip()
    element = None
    control_type = (control.get("type") or "").lower()

    if control_type in {"radio-group", "checkbox-group"}:
        group_class = (
            ".ant-radio-group"
            if control_type == "radio-group"
            else ".ant-checkbox-group"
        )
        group = _first(driver, f'[id="{field_id}"]') if field_id else None
        if group is None:
            group = _choice_group_by_form_item_label(driver, label, group_class)
        return group, None

    if control_type == "checkbox-single":
        return _checkbox_wrapper(driver, control), None

    if field_id:
        element = _first(driver, f'[id="{field_id}"]')
    elif name:
        element = _first(driver, f'[name="{name}"]')
    else:
        known_id = _KNOWN_IDS_BY_LABEL.get(label.casefold())
        if known_id:
            element = _first(driver, f'[id="{known_id}"]')
        if element is None:
            return None, _ant_select_by_form_item_label(driver, label)

    if element is None:
        return None, None
    if element.tag_name.lower() == "select":
        return element, None

    react_wrapper = _react_select_wrapper(element)
    if react_wrapper is not None:
        return element, react_wrapper
    return element, _ant_wrapper(element)


# --- text inputs and native selects -----------------------------------------


def _fill_text(driver, element, label, value):
    _click(driver, element)
    element.send_keys(value)
    if not (element.get_attribute("value") or "").strip():
        logger.warning("Text field may not have kept the value: %s", label)
        return False
    logger.info("Filled text field: %s", label)
    return True


def _fill_native_select(element, label, options, value):
    matched = match_option(options, value)
    if not matched:
        logger.warning("No unique dropdown match for '%s': %s", label, value)
        return False
    Select(element).select_by_visible_text(matched)
    logger.info("Selected dropdown option: %s -> %s", label, matched)
    return True


# --- Ant Design select -------------------------------------------------------


def _visible_dropdown(driver):
    for dropdown in driver.find_elements(By.CSS_SELECTOR, ANT_DROPDOWN_SELECTOR):
        try:
            if dropdown.is_displayed():
                return dropdown
        except WebDriverException:
            continue
    return None


COLLECT_OPTIONS_SCRIPT = """
const dropdown = arguments[0], maxSteps = arguments[1], done = arguments[arguments.length - 1];
const holder = dropdown.querySelector('.rc-virtual-list-holder');
const seen = new Set(), options = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const read = () => {
  for (const el of dropdown.querySelectorAll('.ant-select-item-option')) {
    if (el.classList.contains('ant-select-item-option-disabled')) continue;
    const text = (el.getAttribute('title') || el.textContent || '').trim();
    if (text && !seen.has(text)) { seen.add(text); options.push(text); }
  }
};
(async () => {
  if (holder) { holder.scrollTop = 0; await sleep(60); }
  read();
  for (let i = 0; holder && i < maxSteps; i++) {
    if (holder.scrollTop + holder.clientHeight >= holder.scrollHeight - 2) break;
    holder.scrollTop += holder.clientHeight * 0.8; await sleep(60); read();
  }
  if (holder) holder.scrollTop = 0;
  done(options);
})();
"""

FIND_OPTION_SCRIPT = """
const dropdown = arguments[0], wanted = arguments[1], maxSteps = arguments[2];
const done = arguments[arguments.length - 1];
const holder = dropdown.querySelector('.rc-virtual-list-holder');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const find = () => Array.from(dropdown.querySelectorAll('.ant-select-item-option'))
  .find(el => (el.getAttribute('title') || el.textContent || '').trim() === wanted) || null;
(async () => {
  if (holder) { holder.scrollTop = 0; await sleep(60); }
  let found = find();
  for (let i = 0; !found && holder && i < maxSteps; i++) {
    if (holder.scrollTop + holder.clientHeight >= holder.scrollHeight - 2) break;
    holder.scrollTop += holder.clientHeight * 0.8; await sleep(60); found = find();
  }
  done(found);
})();
"""


def _collect_options(driver, dropdown):
    try:
        driver.set_script_timeout(SCRIPT_TIMEOUT_SECONDS)
        return (
            driver.execute_async_script(
                COLLECT_OPTIONS_SCRIPT, dropdown, MAX_SCROLL_STEPS
            )
            or []
        )
    except WebDriverException:
        logger.exception("Could not read the dropdown options.")
        return []


def _click_option(driver, dropdown, text):
    try:
        driver.set_script_timeout(SCRIPT_TIMEOUT_SECONDS)
        option = driver.execute_async_script(
            FIND_OPTION_SCRIPT, dropdown, text, MAX_SCROLL_STEPS
        )
    except WebDriverException:
        logger.exception("Could not find the dropdown option: %s", text)
        return False
    if option is None:
        return False
    _click(driver, option)
    return True


def _close_dropdown(driver):
    try:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    except WebDriverException:
        pass


def _fill_ant_select(driver, wrapper, label, context, page_title, allow_ai):
    _click(driver, wrapper.find_element(By.CSS_SELECTOR, ".ant-select-selector"))
    try:
        dropdown = WebDriverWait(driver, DROPDOWN_WAIT_SECONDS).until(
            lambda d: _visible_dropdown(d) or False
        )
    except TimeoutException:
        logger.warning("The dropdown did not open for: %s", label)
        return False

    options = _collect_options(driver, dropdown)
    logger.info("Options for '%s': %s", label, options)
    if not options:
        logger.warning("No options were found for: %s", label)
        _close_dropdown(driver)
        return False

    choice = _choose_answer(label, options, "dropdown", context, page_title, allow_ai)
    if not choice or not _click_option(driver, dropdown, choice):
        logger.info("No answer selected for '%s'; leaving it blank.", label)
        _close_dropdown(driver)
        return False

    time.sleep(0.5)
    try:
        kept = bool(
            wrapper.find_elements(By.CSS_SELECTOR, ".ant-select-selection-item")
        )
    except WebDriverException:
        kept = False
    if not kept:
        logger.warning("Dropdown did not keep the selection: %s -> %s", label, choice)
        _close_dropdown(driver)
        return False
    logger.info("Selected dropdown option: %s -> %s", label, choice)
    return True


# --- Greenhouse React Select -------------------------------------------------


def _visible_react_menu(driver):
    for menu in driver.find_elements(By.CSS_SELECTOR, REACT_SELECT_MENU_SELECTOR):
        try:
            if menu.is_displayed():
                return menu
        except WebDriverException:
            continue
    return None


def _react_option_text(option):
    return " ".join((option.text or "").split())


def _react_options(menu):
    """Read currently rendered React Select options, excluding disabled entries."""
    options = []
    seen = set()
    for element in menu.find_elements(By.CSS_SELECTOR, REACT_SELECT_OPTION_SELECTOR):
        try:
            if not element.is_displayed():
                continue
            text = _react_option_text(element)
            disabled = element.get_attribute(
                "aria-disabled"
            ) == "true" or "select__option--is-disabled" in (
                element.get_attribute("class") or ""
            )
            if text and not disabled and text not in seen:
                seen.add(text)
                options.append(text)
        except WebDriverException:
            continue
    return options


def _fill_react_select(
    driver, input_element, wrapper, label, context, page_title, allow_ai
):
    """Open Greenhouse's React Select menu, read its options, then choose one."""
    try:
        control = wrapper.find_element(By.CSS_SELECTOR, ".select__control")
    except WebDriverException:
        control = input_element

    _click(driver, control)

    try:
        menu = WebDriverWait(driver, DROPDOWN_WAIT_SECONDS).until(
            lambda d: _visible_react_menu(d) or False
        )
    except TimeoutException:
        logger.warning("React Select menu did not open for: %s", label)
        return False

    options = _react_options(menu)
    logger.info("React Select options for '%s': %s", label, options)

    # React Select can filter options as the user types. If the menu has no
    # options, don't guess or type an answer into the combobox.
    if not options:
        logger.warning("No visible React Select options found for: %s", label)
        _close_dropdown(driver)
        return False

    choice = _choose_answer(label, options, "dropdown", context, page_title, allow_ai)
    if not choice:
        logger.info("No answer selected for '%s'; leaving it blank.", label)
        _close_dropdown(driver)
        return False

    matched = match_option(options, choice)
    if not matched:
        logger.warning("No matching React Select option for '%s': %s", label, choice)
        _close_dropdown(driver)
        return False

    # Re-query immediately before clicking: React may rerender the menu.
    try:
        current_menu = _visible_react_menu(driver)
        if current_menu is None:
            raise TimeoutException("React Select menu closed before option click")
        candidates = current_menu.find_elements(
            By.CSS_SELECTOR, REACT_SELECT_OPTION_SELECTOR
        )
        target = None
        for candidate in candidates:
            if candidate.is_displayed() and _react_option_text(candidate) == matched:
                target = candidate
                break
        if target is None:
            logger.warning(
                "React Select option disappeared before selection: %s", matched
            )
            _close_dropdown(driver)
            return False
        _click(driver, target)
    except WebDriverException:
        logger.exception("Could not select React Select option for: %s", label)
        return False

    try:
        WebDriverWait(driver, DROPDOWN_WAIT_SECONDS).until(
            lambda d: bool(
                wrapper.find_elements(By.CSS_SELECTOR, ".select__single-value")
                or wrapper.find_elements(By.CSS_SELECTOR, ".select__multi-value__label")
            )
        )
    except TimeoutException:
        logger.warning(
            "React Select did not retain the selection: %s -> %s", label, matched
        )
        return False

    logger.info("Selected React Select option: %s -> %s", label, matched)
    return True


# --- choice groups and checkboxes --------------------------------------------


def _normalize_space(text):
    return " ".join((text or "").split())


def _choice_group_by_form_item_label(driver, label, group_selector=".ant-radio-group"):
    wanted = _normalize_space(label).replace("*", "").strip().casefold()
    matches = []
    for item in driver.find_elements(By.CSS_SELECTOR, ".ant-form-item"):
        try:
            labels = item.find_elements(By.CSS_SELECTOR, ".ant-form-item-label")
            text = (
                _normalize_space(labels[0].text if labels else "")
                .replace("*", "")
                .strip()
                .casefold()
            )
            if text == wanted:
                matches.extend(item.find_elements(By.CSS_SELECTOR, group_selector))
        except WebDriverException:
            continue
    return matches[0] if len(matches) == 1 else None


def _fill_radio_group(driver, group, label, value, kind="radio"):
    wanted = _normalize_space(value)
    option_selector, input_selector = CHOICE_SELECTORS[kind]
    for wrapper in group.find_elements(By.CSS_SELECTOR, option_selector):
        if _normalize_space(wrapper.text) != wanted:
            continue
        _click(driver, wrapper)
        time.sleep(0.3)
        try:
            inputs = wrapper.find_elements(By.CSS_SELECTOR, input_selector)
            checked = bool(inputs) and inputs[0].is_selected()
        except WebDriverException:
            checked = False
        if not checked:
            logger.warning("Option did not stay selected: %s -> %s", label, value)
            return False
        logger.info("Selected answer: %s -> %s", label, value)
        return True
    logger.warning("No option labelled '%s' for: %s", value, label)
    return False


def _safe_text(element):
    try:
        return _normalize_space(element.text).casefold()
    except WebDriverException:
        return ""


def _checkbox_wrapper(driver, control):
    field_id = control.get("id") or ""
    if field_id:
        inputs = driver.find_elements(By.CSS_SELECTOR, f'[id="{field_id}"]')
        if inputs:
            wrappers = inputs[0].find_elements(
                By.XPATH,
                "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ant-checkbox-wrapper ')][1]",
            )
            if wrappers:
                return wrappers[0]
    wanted = _normalize_space(control.get("label")).casefold()
    matches = [
        wrapper
        for wrapper in driver.find_elements(By.CSS_SELECTOR, ".ant-checkbox-wrapper")
        if _safe_text(wrapper) == wanted
    ]
    return matches[0] if len(matches) == 1 else None


def _is_consent(text):
    lowered = (text or "").casefold()
    return is_sensitive(text) or any(term in lowered for term in CONSENT_TERMS)


def _yes_no(value):
    normalized = normalize_choice(value)
    if normalized in {"yes", "y", "true", "check", "checked", "checkedyes"}:
        return True
    if normalized in {"no", "n", "false", "uncheck", "unchecked", "uncheckedno"}:
        return False
    return None


def _decide_checkbox(label, form_label, required, context, page_title):
    explicit = _explicit_value(context, label)
    if explicit:
        decision = _yes_no(explicit)
        if decision is not None:
            logger.info("Using explicit notes value for checkbox: %s", label)
            return decision
    if _is_demographic(label):
        return None
    if not required and OPTIONAL_FIELD_MODE != "ai":
        return None
    answer = answer_question(
        Question(
            text=f"{form_label}: {label}" if form_label else label,
            field_type="single checkbox (answer with check, uncheck, or skip)",
            options=["checked/Yes", "unchecked/No"],
            page_title=page_title,
        ),
        context,
    )
    if answer.action == "check":
        return True
    if answer.action == "uncheck":
        return False
    if answer.is_skip:
        return None
    return _yes_no(answer.value)


def _tick_checkbox(driver, wrapper, label):
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", wrapper
        )
        time.sleep(0.3)
        boxes = wrapper.find_elements(By.CSS_SELECTOR, ".ant-checkbox")
        target = boxes[0] if boxes else wrapper
        try:
            target.click()
        except WebDriverException:
            driver.execute_script("arguments[0].click();", target)
        time.sleep(0.3)
        inputs = wrapper.find_elements(By.CSS_SELECTOR, 'input[type="checkbox"]')
        ticked = bool(inputs) and inputs[0].is_selected()
    except WebDriverException:
        logger.exception("Could not tick the checkbox: %s", label)
        return False
    if ticked:
        logger.info("Ticked checkbox: %s", label)
    else:
        logger.warning("Checkbox did not stay ticked: %s", label)
    return ticked


def _handle_checkbox(driver, control, context, page_title, handled):
    label = (control.get("label") or "").strip()
    form_label = (control.get("context") or "").strip()
    required = bool(control.get("required"))
    shown = label or control.get("id") or "?"
    if (control.get("value") or "").strip():
        logger.info("Skipping ticked checkbox: %s", shown)
        return "ok"

    if _is_consent(label) or _is_consent(form_label):
        combined = f"{label} {form_label}".casefold()
        allowed = any(
            phrase.casefold() in combined for phrase in ACCEPT_CONSENT_PHRASES
        )
        if not allowed:
            logger.warning(
                "Consent/acknowledgement checkbox left for you to tick manually: %s",
                shown,
            )
            return "blank" if required else "ok"
        logger.info("Consent checkbox permitted by ACCEPT_CONSENT_PHRASES: %s", shown)
        decision = True
    else:
        decision = _decide_checkbox(label, form_label, required, context, page_title)

    if decision is None:
        logger.info("No answer for checkbox '%s'; leaving it unticked.", shown)
        return "blank" if required else "ok"
    if decision is False:
        logger.info("Leaving checkbox unticked: %s", shown)
        return "ok"

    wrapper = _checkbox_wrapper(driver, control)
    if wrapper is None:
        logger.warning(
            "Could not locate checkbox '%s'; please tick it manually.", shown
        )
        return "blank" if required else "ok"
    if wrapper.id in handled:
        return "ok"
    handled.add(wrapper.id)
    done = _tick_checkbox(driver, wrapper, shown)
    return "ok" if done or not required else "blank"


def _is_empty_value(value):
    lowered = (value or "").strip().casefold()
    return lowered in PLACEHOLDER_OPTIONS or bool(
        re.match(r"^0 items? selected", lowered)
    )


def _handle_control(driver, control, context, page_title, handled, controls, index):
    label = (control.get("label") or "").strip()
    question = label or (control.get("context") or "").strip()
    shown = question or control.get("id") or control.get("name") or "?"
    control_type = (control.get("type") or "").lower()
    role = (control.get("role") or "").lower()
    tag = (control.get("tag") or "").lower()
    required = bool(control.get("required"))

    if control.get("disabled"):
        return "ok"

    if control_type == "button":
        looks_like_upload = (
            "upload" in (control.get("label") or "").casefold()
            or "attach" in (control.get("id") or "").casefold()
            or (control.get("value") or "").strip().casefold() == "select files"
        )
        label_lowered = (control.get("label") or "").strip().casefold()
        answered = "select one" not in label_lowered and label_lowered.endswith(
            " required"
        )
        if answered:
            logger.info("Skipping answered dropdown: %s", shown)
            return "ok"
        if required and not looks_like_upload:
            logger.warning(
                "Custom dropdown/button '%s' is not handled automatically; please review it manually.",
                shown,
            )
            return "blank"
        return "ok"

    if control_type in SKIP_TYPES:
        return "ok"
    if control_type == "checkbox-single":
        return _handle_checkbox(driver, control, context, page_title, handled)
    if control_type in CHOICE_TYPES or role in CHOICE_TYPES:
        if required:
            logger.warning(
                "Radio/checkbox '%s' is not handled automatically; please review it manually.",
                shown,
            )
        return "ok"

    # React Select and Ant Select are choice controls, not ordinary text boxes.
    if (control.get("placeholder") or "").strip().casefold() == "search":
        same_label = any(
            other is not control
            and (other.get("label") or "").strip()
            == (control.get("label") or "").strip()
            and not _is_empty_value((other.get("value") or "").strip())
            for other in controls
        )
        if same_label or not required:
            return "ok"
        logger.warning(
            "Searchable dropdown '%s' is not handled automatically; please review it manually.",
            shown,
        )
        return "blank"

    current_value = (control.get("value") or "").strip()
    if not _is_empty_value(current_value):
        logger.info("Skipping populated field: %s (%r)", shown, current_value)
        return "ok"
    if not question:
        logger.warning("Blank field has no label or context; skipping: %s", shown)
        return "blank" if required else "ok"

    if _is_dialing_code(question):
        paired_phone = _paired_phone_value(controls, index)
        if paired_phone is not None and not paired_phone:
            logger.info("Skipping %s: the phone number next to it is blank.", shown)
            return "ok"

    demographic = _is_demographic(question)
    explicit = _explicit_value(context, question)
    ai_optional = any(
        keyword in question.casefold() for keyword in AI_OPTIONAL_KEYWORDS
    )
    if not required and (
        OPTIONAL_FIELD_MODE == "skip"
        or (OPTIONAL_FIELD_MODE == "profile" and not explicit and not ai_optional)
    ):
        logger.info(
            "Skipping blank optional field: %s (no notes line for: %s)",
            shown,
            ", ".join(_profile_keys(question)[:4]),
        )
        return "ok"

    allow_ai = (
        required or OPTIONAL_FIELD_MODE == "ai" or ai_optional
    ) and not demographic
    element, wrapper = _locate(driver, control)
    if element is None and wrapper is None:
        logger.warning(
            "Could not locate '%s' on the page; leaving it for manual review.", shown
        )
        return "blank" if required else "ok"

    widget = wrapper if wrapper is not None else element
    identity = widget.id or control.get("id") or f"{control_type}:{question}"
    if identity in handled:
        return "ok"
    handled.add(identity)
    logger.info("Blank field needs an answer: %s", shown)

    try:
        if control_type in {"radio-group", "checkbox-group"}:
            kind = "radio" if control_type == "radio-group" else "checkbox"
            options = [
                o["text"]
                for o in control.get("options", [])
                if o.get("text") and not o.get("disabled")
            ]
            value = _choose_answer(
                question, options, f"{kind} group", context, page_title, allow_ai
            )
            done = bool(value) and _fill_radio_group(
                driver, element, shown, value, kind
            )

        elif control_type == "react-select":
            if wrapper is None:
                wrapper = _react_select_wrapper(element)
            done = bool(wrapper) and _fill_react_select(
                driver, element, wrapper, question, context, page_title, allow_ai
            )

        elif wrapper is not None and "ant-select" in (
            wrapper.get_attribute("class") or ""
        ):
            done = _fill_ant_select(
                driver, wrapper, question, context, page_title, allow_ai
            )

        elif tag == "select":
            options = [
                o["text"]
                for o in control.get("options", [])
                if o.get("text")
                and not o.get("disabled")
                and o["text"].casefold() not in PLACEHOLDER_OPTIONS
            ]
            value = _choose_answer(
                question, options, "dropdown", context, page_title, allow_ai
            )
            done = bool(value) and _fill_native_select(element, shown, options, value)

        else:
            value = _choose_answer(
                question,
                [],
                "textarea" if tag == "textarea" else "text input",
                context,
                page_title,
                allow_ai,
            )
            done = bool(value) and _fill_text(driver, element, shown, value)

    except WebDriverException:
        logger.exception("Could not fill field: %s", shown)
        done = False

    if not done and demographic:
        logger.info("Left '%s' blank: no explicit value in the notes file.", shown)
    return "ok" if done or not required else "blank"


def fill_form_content(driver, form_content, context=None):
    """Fill blank fields from scrape_form_content() output; return required blanks."""
    controls = (form_content or {}).get("controls", [])
    logger.info("=" * 65)
    logger.info("Starting AI form fill (no submission).")
    if not controls:
        logger.warning("No scraped controls to fill.")
        return []

    if context is None:
        try:
            context = load_candidate_context()
        except (FileNotFoundError, ValueError):
            logger.exception("Could not load candidate context; skipping AI fill.")
            return []

    page_title = form_content.get("page_title") or driver.title
    handled = set()
    left_blank = []

    for index, control in enumerate(controls):
        try:
            status = _handle_control(
                driver, control, context, page_title, handled, controls, index
            )
        except WebDriverException:
            logger.exception(
                "Could not process control: %s",
                control.get("label") or control.get("id"),
            )
            status = "blank" if control.get("required") else "ok"
        if status == "blank":
            left_blank.append(
                control.get("label") or control.get("context") or control.get("id")
            )

    left_blank = list(dict.fromkeys(left_blank))
    if left_blank:
        logger.warning("Required fields still blank; please review manually:")
        for label in left_blank:
            logger.warning("  - %s", label)
    else:
        logger.info("No required fields were left blank.")

    logger.info("AI form fill finished. Nothing was submitted.")
    logger.info("=" * 65)
    return left_blank
