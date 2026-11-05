"""Portal-independent scraper for visible web form content.

Inspects visible form controls and extracts labels, surrounding question
text, field types, options, required indicators, and locator metadata.

This module does not change form values, click controls, or submit forms.
"""

import logging
import time

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from common.scrape_radio import scrape_radio_groups


class FileLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "scrape_content.py"},
)

WAIT_SECONDS = 10
RENDER_DELAY_SECONDS = 2
MAX_CONTROLS = 150
MAX_PAGE_TEXT_LENGTH = 12000

KNOWN_FIELD_LABELS = {
    "jobPostingApplication_personalInfo_prefixId": "Prefix",
    "jobPostingApplication_personalInfo_suffixId": "Suffix",
    "jobPostingApplication_personalInfo_preferredContactMethod": "Preferred Contact Method",
    "jobPostingApplication_personalInfo_candidateSource": "How did you hear about this job?",
    "jobPostingApplication_personalInfo_countryCode": "Country",
    "jobPostingApplication_personalInfo_stateCode": "State/Province",
}

FORM_INSPECTION_SCRIPT = r"""
const maxControls = arguments[0];
const maxPageTextLength = arguments[1];
const knownFieldLabels = arguments[2] || {};

const clean = (value, maxLength = 500) =>
    String(value || "").replace(/\s+/g, " ").trim().slice(0, maxLength);

const textOf = (element, maxLength = 500) => {
    if (!element) return "";
    return clean(element.innerText || element.textContent || "", maxLength);
};

const isVisible = (element) => {
    if (!element || !element.isConnected) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity) !== 0 &&
        rect.width > 0 && rect.height > 0;
};

const FIELD_CONTAINER_SELECTOR = [
    "fieldset", '[role="group"]', '[role="radiogroup"]',
    '[data-testid*="field" i]', '[data-automation-id*="field" i]',
    '[class*="form-field" i]', '[class*="form-group" i]',
    '[class*="form-item" i]', '[class*="question" i]', ".ant-form-item",
    ".field-wrapper", ".select__container"
].join(",");

const LABEL_SELECTOR = [
    "label", "legend", '[class*="label" i]',
    '[data-testid*="label" i]', '[data-automation-id*="label" i]',
    ".ant-form-item-label"
].join(",");

const referencedText = (element, attribute) => {
    const ids = clean(element.getAttribute(attribute), 1000).split(/\s+/).filter(Boolean);
    return clean(ids.map(id => textOf(document.getElementById(id), 200))
        .filter(Boolean).join(" "), 300);
};

const isReactSelect = (element) =>
    element.matches("input.select__input[role='combobox'][aria-autocomplete='list']") ||
    Boolean(element.closest(".select-shell"));

const getKnownLabel = (element) => {
    const id = element.id || "";
    if (knownFieldLabels[id]) return knownFieldLabels[id];
    const wrapper = element.closest(".ant-select");
    const innerInput = wrapper?.querySelector("input[id]");
    if (innerInput && knownFieldLabels[innerInput.id]) return knownFieldLabels[innerInput.id];
    return "";
};

const getControlRoot = (element) => {
    const react = element.closest(".select-shell");
    if (react) return react;
    const ant = element.closest(".ant-select");
    if (ant) return ant;
    return element;
};

const getLabel = (element) => {
    const known = getKnownLabel(element);
    if (known) return known;

    const ariaLabel = clean(element.getAttribute("aria-label"), 300);
    if (ariaLabel) return ariaLabel;

    const labelledBy = referencedText(element, "aria-labelledby");
    if (labelledBy) return labelledBy;

    if (element.labels && element.labels.length) {
        const labels = Array.from(element.labels).map(label => textOf(label, 200)).filter(Boolean);
        if (labels.length) return clean(labels.join(" "), 300);
    }

    const root = getControlRoot(element);
    const container = root.closest(FIELD_CONTAINER_SELECTOR) ||
        element.closest(FIELD_CONTAINER_SELECTOR);

    if (container) {
        const labelElement = container.querySelector(LABEL_SELECTOR);
        if (labelElement) {
            const labelText = textOf(labelElement, 300);
            if (labelText) return labelText;
        }
    }

    let current = root;
    for (let level = 0; level < 4 && current; level++) {
        let sibling = current.previousElementSibling;
        while (sibling) {
            if (sibling.matches && sibling.matches(LABEL_SELECTOR)) {
                const siblingText = textOf(sibling, 300);
                if (siblingText) return siblingText;
            }
            sibling = sibling.previousElementSibling;
        }
        current = current.parentElement;
    }
    return "";
};

const getContext = (element) => {
    const root = getControlRoot(element);
    const container = root.closest(FIELD_CONTAINER_SELECTOR) ||
        element.closest(FIELD_CONTAINER_SELECTOR);
    if (container) return textOf(container, 500);

    let current = root.parentElement;
    for (let level = 0; level < 4 && current; level++) {
        const text = textOf(current, 500);
        if (text.length >= 3 && text.length <= 500) return text;
        current = current.parentElement;
    }
    return "";
};

const getRequiredInfo = (element, label, context) => {
    const root = getControlRoot(element);
    const container = root.closest(FIELD_CONTAINER_SELECTOR) ||
        element.closest(FIELD_CONTAINER_SELECTOR);
    const requiredAttribute =
        element.hasAttribute("required") ||
        element.getAttribute("aria-required") === "true" ||
        root.getAttribute("aria-required") === "true" ||
        Boolean(container?.querySelector(
            '[aria-required="true"], .ant-form-item-required'
        ));
    const requiredMarker = /\*/.test(label) || /\*/.test(context);
    return {
        required: requiredAttribute || requiredMarker,
        required_attribute: requiredAttribute,
        required_marker_detected: requiredMarker
    };
};

const getValue = (element, tag, type, role) => {
    if (type === "password") return "[REDACTED]";

    if (isReactSelect(element)) {
        const root = getControlRoot(element);
        const selected = root.querySelector(
            ".select__single-value, .select__multi-value__label"
        );
        return selected ? textOf(selected, 200) : "";
    }

    if (tag === "input" || tag === "textarea") {
        if (type === "checkbox" || type === "radio") return element.checked ? "checked" : "unchecked";
        if (type === "file") return Array.from(element.files || []).map(file => file.name).join(", ");

        const ant = element.closest(".ant-select");
        if (ant) {
            const selected = ant.querySelector(".ant-select-selection-item");
            if (selected) return textOf(selected, 200);
            return element.value || "";
        }
        return element.value || "";
    }

    if (tag === "select") {
        return Array.from(element.selectedOptions || []).map(option => textOf(option, 150)).join(", ");
    }

    if (role === "checkbox" || role === "radio" || role === "switch") {
        return element.getAttribute("aria-checked") || "";
    }

    const root = getControlRoot(element);
    const selected = root.querySelector?.([
        ".ant-select-selection-item", ".select__single-value",
        ".select__multi-value__label", "[aria-selected='true']", "[data-value]"
    ].join(","));
    if (selected) return textOf(selected, 200);

    if (role === "combobox" || role === "listbox") {
        return clean(element.getAttribute("aria-valuetext") || element.value || textOf(element, 200), 200);
    }
    if (element.isContentEditable) return textOf(element, 500);
    return clean(element.value || textOf(element, 200), 500);
};

const getOptions = (element, tag) => {
    if (tag === "select") {
        return Array.from(element.options || []).slice(0, 100).map(option => ({
            text: textOf(option, 200), value: option.value,
            selected: option.selected, disabled: option.disabled
        }));
    }

    // Dynamic React Select / Ant options are intentionally not opened here.
    // The filler opens the control and reads the live menu before choosing.
    const optionElements = Array.from(document.querySelectorAll([
        "[role='option']", ".ant-select-item-option", ".select__option"
    ].join(","))).filter(isVisible);

    return optionElements.slice(0, 100).map(option => ({
        text: textOf(option, 200),
        value: option.getAttribute("value") || option.getAttribute("data-value") || "",
        selected: option.getAttribute("aria-selected") === "true" ||
            option.classList.contains("ant-select-item-option-selected"),
        disabled: option.getAttribute("aria-disabled") === "true" ||
            option.classList.contains("ant-select-item-option-disabled")
    })).filter(option => option.text);
};

const selector = [
    "input:not([type='hidden'])", "textarea", "select",
    "[role='combobox']", "[role='radio']", "[role='checkbox']",
    "[role='switch']", "[role='listbox']", "[contenteditable='true']",
    ".ant-select", '[aria-haspopup="listbox"]',
    '[data-testid*="select" i]', '[data-automation-id*="select" i]'
].join(",");

const elements = Array.from(document.querySelectorAll(selector));
const controls = [];
const seen = new Set();

for (const element of elements) {
    if (!isVisible(element)) continue;

    const antSelect = element.closest(".ant-select");
    if (element.matches(".ant-select") && antSelect && element !== antSelect) continue;

    const tag = element.tagName.toLowerCase();
    const reactSelect = isReactSelect(element);
    const isAntInput = tag === "input" && Boolean(antSelect);
    const isAntWrapper = element.matches(".ant-select");
    const type = reactSelect ? "react-select" : (
        element.getAttribute("type") ||
        (tag === "select" ? "select" :
         isAntInput || isAntWrapper ? "custom-select" : tag)
    ).toLowerCase();
    const role = element.getAttribute("role") || (isAntWrapper ? "combobox" : "");

    // Do not treat React Select's inner text input as an ordinary text field.
    const label = getLabel(element);
    const context = getContext(element);
    const root = getControlRoot(element);

    const key = element.id ? `${type}:${element.id}` : `${type}:${label}:${context}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const requiredInfo = getRequiredInfo(element, label, context);
    const isButton = tag === "button" || element.getAttribute("role") === "button";
    const hasIdentity = Boolean(label || element.getAttribute("aria-label") ||
        element.getAttribute("aria-labelledby") || element.getAttribute("aria-haspopup") ||
        element.closest(FIELD_CONTAINER_SELECTOR));
    if (isButton && !hasIdentity) continue;

    controls.push({
        index: controls.length + 1,
        tag, type, role, label, question: label || context, context,
        value: clean(getValue(element, tag, type, role), 500),
        placeholder: element.getAttribute("placeholder") ||
            (reactSelect ? root.querySelector(".select__placeholder")?.textContent?.trim() : "") ||
            root.querySelector?.("input")?.getAttribute("placeholder") || "",
        name: element.getAttribute("name") || "",
        id: element.id || "",
        data_testid: element.getAttribute("data-testid") ||
            root.getAttribute?.("test-id") || "",
        data_automation_id: element.getAttribute("data-automation-id") || "",
        aria_label: element.getAttribute("aria-label") || "",
        aria_labelledby: element.getAttribute("aria-labelledby") || "",
        disabled: Boolean(element.disabled) ||
            element.getAttribute("aria-disabled") === "true" ||
            Boolean(root.classList?.contains("ant-select-disabled")) ||
            Boolean(root.querySelector?.(".select__control--is-disabled")),
        ...requiredInfo,
        options: getOptions(element, tag)
    });

    if (controls.length >= maxControls) break;
}

const headings = Array.from(document.querySelectorAll(
    "h1, h2, h3, h4, h5, h6, [role='heading']"
)).filter(isVisible).map(element => textOf(element, 250)).filter(Boolean).slice(0, 100);

const bodyText = isVisible(document.body) ? clean(document.body.innerText, maxPageTextLength) : "";

return {
    page_title: document.title || "",
    page_url: window.location.href,
    headings,
    visible_page_text: bodyText,
    controls,
    control_limit_reached: controls.length >= maxControls
};
"""


def _empty_result(title="", url=""):
    return {
        "page_title": title,
        "page_url": url,
        "headings": [],
        "visible_page_text": "",
        "controls": [],
        "control_limit_reached": False,
    }


def scrape_form_content(driver, wait=None):
    """Inspect visible page text and form controls without changing them."""
    if wait is None:
        wait = WebDriverWait(driver, WAIT_SECONDS)

    logger.info("=" * 65)
    logger.info("Starting read-only form inspection.")
    logger.info("No form values will be changed.")

    try:
        wait.until(
            lambda current_driver: current_driver.find_elements(By.TAG_NAME, "body")
        )
    except TimeoutException:
        logger.warning("Page body did not become available in time.")
        return _empty_result()

    time.sleep(RENDER_DELAY_SECONDS)

    try:
        result = driver.execute_script(
            FORM_INSPECTION_SCRIPT,
            MAX_CONTROLS,
            MAX_PAGE_TEXT_LENGTH,
            KNOWN_FIELD_LABELS,
        )
    except Exception:
        logger.exception("Could not inspect the page.")
        return _empty_result(title=driver.title, url=driver.current_url)

    if not result:
        logger.warning("The page inspection returned no data.")
        return _empty_result(title=driver.title, url=driver.current_url)

    result.setdefault("controls", []).extend(
        scrape_radio_groups(driver, len(result.get("controls", [])) + 1)
    )
    controls = result.get("controls", [])

    logger.info("Page title: %s", result.get("page_title", ""))
    logger.info("Page URL: %s", result.get("page_url", ""))
    logger.info("Visible headings detected: %s", len(result.get("headings", [])))
    logger.info("Visible form controls detected: %s", len(controls))

    for control in controls:
        logger.info(
            "FIELD %s | type=%s | label=%r | value=%r | required=%s | "
            "disabled=%s | name=%r | id=%r",
            control.get("index"),
            control.get("type"),
            control.get("label") or "(label not detected)",
            control.get("value", ""),
            control.get("required"),
            control.get("disabled"),
            control.get("name"),
            control.get("id"),
        )
        if control.get("context") and not control.get("label"):
            logger.info("  Question/context: %r", control["context"])
        if control.get("placeholder"):
            logger.info("  Placeholder: %r", control["placeholder"])
        options = control.get("options") or []
        if options:
            logger.info(
                "  Available native/visible options: %s",
                ", ".join(
                    option.get("text", "") for option in options if option.get("text")
                ),
            )

    if result.get("control_limit_reached"):
        logger.warning(
            "The control limit (%s) was reached. The page may contain additional fields.",
            MAX_CONTROLS,
        )

    logger.info("Inspection complete. No fields were changed or submitted.")
    logger.info("=" * 65)
    return result
