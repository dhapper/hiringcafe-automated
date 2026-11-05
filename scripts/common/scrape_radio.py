"""Read-only scraper for Ant Design radio groups and checkboxes.

scrape_form_content() skips these because Ant Design renders the real inputs
with opacity 0. This module finds the visible groups/checkboxes instead and
returns them in the same control format, so fill_content can answer them.

Control types returned:
    radio-group      one question, several radio options
    checkbox-group   one question, several checkbox options
    checkbox-single  one standalone checkbox (e.g. "I agree to ...")

It does not click anything or change any value. (The module keeps its original
name so existing imports keep working.)
"""

from selenium.common.exceptions import WebDriverException

from common import get_logger

logger = get_logger("scrape_radio.py")

CHOICE_SCRIPT = r"""
const clean = (value, maxLength = 500) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maxLength);

const textOf = (element, maxLength = 500) =>
    element ? clean(element.innerText || element.textContent || "", maxLength) : "";

const isVisible = (element) => {
    if (!element || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
        rect.width > 0 && rect.height > 0;
};

const RADIO_OPTION_SELECTOR = ".ant-radio-wrapper, .ant-radio-button-wrapper";
const CHECKBOX_OPTION_SELECTOR = ".ant-checkbox-wrapper";
const LABEL_SELECTOR = [
    ".ant-form-item-label label",
    ".ant-form-item-label",
    "legend",
    "label:not(.ant-radio-wrapper):not(.ant-radio-button-wrapper):not(.ant-checkbox-wrapper)",
    '[class*="label" i]'
].join(",");

const formLabelOf = (element) => {
    const formItem = element.closest(".ant-form-item");
    return formItem ? textOf(formItem.querySelector(LABEL_SELECTOR), 500) : "";
};

const isRequired = (element, label) => {
    const formItem = element.closest(".ant-form-item");
    return Boolean(
        (formItem && formItem.querySelector(".ant-form-item-required, [aria-required='true']")) ||
        element.getAttribute("aria-required") === "true" ||
        /\*\s*$/.test(label)
    );
};

const optionsOf = (group, optionSelector, inputSelector) =>
    Array.from(group.querySelectorAll(optionSelector)).map((wrapper) => {
        const input = wrapper.querySelector(inputSelector);
        return {
            text: textOf(wrapper, 200),
            value: input ? input.value : "",
            selected: Boolean(input && input.checked),
            disabled: Boolean(input && input.disabled) ||
                wrapper.classList.contains("ant-radio-wrapper-disabled") ||
                wrapper.classList.contains("ant-radio-button-wrapper-disabled") ||
                wrapper.classList.contains("ant-checkbox-wrapper-disabled")
        };
    }).filter((option) => option.text);

const baseControl = (overrides) => Object.assign({
    tag: "div",
    type: "",
    role: "",
    label: "",
    question: "",
    context: "",
    value: "",
    placeholder: "",
    name: "",
    id: "",
    data_testid: "",
    data_automation_id: "",
    aria_label: "",
    aria_labelledby: "",
    disabled: false,
    required: false,
    required_attribute: false,
    required_marker_detected: false,
    options: []
}, overrides);

const results = [];
const seen = new Set();

// --- radio groups -----------------------------------------------------------
for (const group of document.querySelectorAll('.ant-radio-group, [role="radiogroup"]')) {
    if (seen.has(group) || !isVisible(group)) continue;
    seen.add(group);

    const radios = Array.from(group.querySelectorAll('input[type="radio"]'));
    if (!radios.length) continue;

    const options = optionsOf(group, RADIO_OPTION_SELECTOR, 'input[type="radio"]');

    // Not an Ant Design radio group: leave it to whatever other scraper
    // handles that portal.
    if (!options.length) continue;

    const label = formLabelOf(group) || clean(group.getAttribute("aria-label"), 500);
    const required = isRequired(group, label);
    const selected = options.find((option) => option.selected);

    results.push(baseControl({
        type: "radio-group",
        role: "radiogroup",
        label: label.replace(/\s*\*\s*$/, ""),
        question: label,
        value: selected ? selected.text : "",
        name: radios[0].getAttribute("name") || "",
        id: group.id || "",
        aria_label: group.getAttribute("aria-label") || "",
        aria_labelledby: group.getAttribute("aria-labelledby") || "",
        disabled: radios.every((radio) => radio.disabled),
        required,
        required_attribute: required,
        required_marker_detected: required,
        options
    }));
}

// --- checkbox groups --------------------------------------------------------
for (const group of document.querySelectorAll(".ant-checkbox-group")) {
    if (seen.has(group) || !isVisible(group)) continue;
    seen.add(group);

    const options = optionsOf(group, CHECKBOX_OPTION_SELECTOR, 'input[type="checkbox"]');
    if (!options.length) continue;

    const label = formLabelOf(group) || clean(group.getAttribute("aria-label"), 500);
    const required = isRequired(group, label);
    const selectedTexts = options.filter((option) => option.selected).map((option) => option.text);

    results.push(baseControl({
        type: "checkbox-group",
        role: "group",
        label: label.replace(/\s*\*\s*$/, ""),
        question: label,
        value: selectedTexts.join(", "),
        id: group.id || "",
        aria_label: group.getAttribute("aria-label") || "",
        aria_labelledby: group.getAttribute("aria-labelledby") || "",
        required,
        required_attribute: required,
        required_marker_detected: required,
        options
    }));
}

// --- single checkboxes ------------------------------------------------------
for (const wrapper of document.querySelectorAll(CHECKBOX_OPTION_SELECTOR)) {
    if (seen.has(wrapper) || !isVisible(wrapper)) continue;
    seen.add(wrapper);

    if (wrapper.closest(".ant-checkbox-group")) continue;

    const input = wrapper.querySelector('input[type="checkbox"]');
    if (!input) continue;

    const text = textOf(wrapper, 500);
    if (!text) continue;

    const formLabel = formLabelOf(wrapper);
    const required = isRequired(wrapper, formLabel) || input.required;

    results.push(baseControl({
        type: "checkbox-single",
        role: "checkbox",
        label: text,
        question: text,
        context: formLabel && formLabel !== text ? formLabel : "",
        value: input.checked ? "checked" : "",
        name: input.getAttribute("name") || "",
        id: input.id || "",
        aria_label: input.getAttribute("aria-label") || "",
        disabled: input.disabled,
        required,
        required_attribute: required,
        required_marker_detected: required,
        options: [{ text, value: input.value || "", selected: input.checked, disabled: input.disabled }]
    }));
}

return results;
"""


def scrape_radio_groups(driver, start_index=1):
    """Return Ant radio groups and checkboxes as scrape_form_content-style controls."""
    try:
        controls = driver.execute_script(CHOICE_SCRIPT) or []
    except WebDriverException:
        logger.exception("Could not inspect radio groups and checkboxes.")
        return []

    for offset, control in enumerate(controls):
        control["index"] = start_index + offset

    logger.info("Radio groups and checkboxes detected: %s", len(controls))
    return controls
