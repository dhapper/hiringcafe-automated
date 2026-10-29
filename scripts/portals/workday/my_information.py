import logging
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "my_information.py"},
)

WAIT_SECONDS = 15
RENDER_DELAY_SECONDS = 3
MAX_DIAGNOSTIC_ELEMENTS = 120


# This JavaScript only inspects the page.
# It does not click, type, select, or modify any form controls.
WORKDAY_INSPECTION_SCRIPT = r"""
const isVisible = (element) => {
    if (!element || !element.isConnected) {
        return false;
    }

    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();

    return (
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        Number(style.opacity) !== 0 &&
        rect.width > 0 &&
        rect.height > 0
    );
};

const clean = (value, maxLength = 240) => {
    return String(value || "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, maxLength);
};

const getText = (element, maxLength = 240) => {
    return clean(
        element?.innerText ||
        element?.textContent ||
        "",
        maxLength
    );
};

const getReferencedLabel = (element) => {
    const ids = clean(element.getAttribute("aria-labelledby"), 500)
        .split(/\s+/)
        .filter(Boolean);

    const parts = ids.map(id => {
        const referenced = document.getElementById(id);
        return referenced ? getText(referenced, 120) : "";
    }).filter(Boolean);

    return clean(parts.join(" "), 240);
};

const getLabel = (element) => {
    const ariaLabel = clean(element.getAttribute("aria-label"), 240);
    if (ariaLabel) return ariaLabel;

    const referencedLabel = getReferencedLabel(element);
    if (referencedLabel) return referencedLabel;

    if (element.labels && element.labels.length) {
        const labels = Array.from(element.labels)
            .map(label => getText(label, 160))
            .filter(Boolean);

        if (labels.length) return clean(labels.join(" "), 240);
    }

    const labelledBy = element.closest(
        '[data-automation-id*="formField"],' +
        '[data-automation-id*="field"],' +
        '[role="group"], fieldset'
    );

    if (labelledBy) {
        const label = labelledBy.querySelector(
            'label, [data-automation-id*="label"], legend'
        );

        if (label && getText(label)) {
            return getText(label, 240);
        }
    }

    const parent = element.parentElement;
    if (parent) {
        const label = parent.querySelector(
            ':scope > label, :scope > legend'
        );

        if (label && getText(label)) {
            return getText(label, 240);
        }
    }

    return "";
};

const getContext = (element) => {
    const fieldContainer = element.closest(
        '[data-automation-id*="formField"],' +
        '[data-automation-id*="field"],' +
        '[data-automation-id*="question"],' +
        '[role="group"], fieldset'
    );

    if (fieldContainer) {
        return getText(fieldContainer, 300);
    }

    let current = element;
    for (let level = 0; level < 3 && current; level++) {
        const text = getText(current, 300);
        if (text.length >= 3) return text;
        current = current.parentElement;
    }

    return "";
};

const isRequired = (element, label, context) => {
    const requiredAttribute =
        element.hasAttribute("required") ||
        element.getAttribute("aria-required") === "true";

    const textLooksRequired =
        /\*\s*$/.test(label) ||
        /\*\s*$/.test(getText(element, 100));

    return {
        required_attribute: requiredAttribute,
        required_marker_visible: textLooksRequired,
        required_marker_in_context: /\*\s/.test(context)
    };
};

const describeControl = (element, index) => {
    const tag = element.tagName.toLowerCase();
    const role = element.getAttribute("role") || "";
    const automationId = element.getAttribute("data-automation-id") || "";
    const label = getLabel(element);
    const context = getContext(element);

    let type = element.getAttribute("type") || "";

    if (!type && tag === "select") {
        type = "select";
    } else if (!type && role) {
        type = "role:" + role;
    } else if (!type) {
        type = tag;
    }

    let value = "";

    if (tag === "input" || tag === "textarea") {
        if (type === "password") {
            value = "[REDACTED]";
        } else if (type === "checkbox" || type === "radio") {
            value = element.checked ? "checked" : "unchecked";
        } else {
            value = element.value || "";
        }
    } else if (tag === "select") {
        value = Array.from(element.selectedOptions || [])
            .map(option => getText(option, 120))
            .join(", ");
    } else if (role === "checkbox" || role === "radio") {
        value = element.getAttribute("aria-checked") || "";
    } else if (role === "combobox" || role === "listbox") {
        value = clean(
            element.getAttribute("aria-valuetext") ||
            element.getAttribute("aria-valuenow") ||
            getText(element, 120),
            160
        );
    } else if (element.isContentEditable) {
        value = getText(element, 160);
    } else {
        value = getText(element, 160);
    }

    let options = [];

    if (tag === "select") {
        options = Array.from(element.options || [])
            .slice(0, 60)
            .map(option => ({
                text: getText(option, 120),
                value: option.value,
                selected: option.selected,
                disabled: option.disabled
            }));
    }

    const required = isRequired(element, label, context);

    return {
        index,
        tag,
        type,
        role,
        label,
        context,
        value: clean(value, 200),
        placeholder: element.getAttribute("placeholder") || "",
        automation_id: automationId,
        aria_label: element.getAttribute("aria-label") || "",
        aria_labelledby: element.getAttribute("aria-labelledby") || "",
        aria_expanded: element.getAttribute("aria-expanded") || "",
        aria_controls: element.getAttribute("aria-controls") || "",
        aria_checked: element.getAttribute("aria-checked") || "",
        disabled: Boolean(element.disabled) ||
            element.getAttribute("aria-disabled") === "true",
        required_attribute: required.required_attribute,
        required_marker_visible: required.required_marker_visible,
        required_marker_in_context: required.required_marker_in_context,
        options
    };
};

const heading = Array.from(
    document.querySelectorAll("h1, h2, h3, h4, [role='heading']")
).find(element =>
    isVisible(element) &&
    getText(element, 100).toLowerCase() === "my information"
);

let root = heading || document.body;
let containerFound = false;

if (heading) {
    let ancestor = heading.parentElement;

    // Find a reasonably narrow container around the My Information section.
    for (let level = 0; level < 9 && ancestor; level++) {
        const count = ancestor.querySelectorAll(
            'input, textarea, select, button, ' +
            '[role="combobox"], [role="radio"], [role="checkbox"], ' +
            '[role="listbox"], [role="option"], [role="group"], ' +
            '[contenteditable="true"]'
        ).length;

        if (count >= 5 && count <= 250) {
            root = ancestor;
            containerFound = true;
            break;
        }

        ancestor = ancestor.parentElement;
    }
}

const selector = [
    "input",
    "textarea",
    "select",
    "button",
    "[role='combobox']",
    "[role='radio']",
    "[role='checkbox']",
    "[role='listbox']",
    "[role='option']",
    "[role='group']",
    "[role='radiogroup']",
    "[contenteditable='true']",
    "label",
    "legend",
    "[data-automation-id*='formField']",
    "[data-automation-id*='field']",
    "[data-automation-id*='label']",
    "[data-automation-id*='select']",
    "[data-automation-id*='dropdown']",
    "[data-automation-id*='radio']",
    "[data-automation-id*='checkbox']",
    "[data-automation-id*='prompt']",
    "[aria-label]",
    "[aria-labelledby]"
].join(",");

const elements = Array.from(root.querySelectorAll(selector));
const results = [];
const seen = new Set();

for (const element of elements) {
    if (!isVisible(element)) continue;

    const tag = element.tagName.toLowerCase();
    const role = element.getAttribute("role") || "";
    const automationId = (
        element.getAttribute("data-automation-id") || ""
    ).toLowerCase();

    const isBasicControl = [
        "input", "textarea", "select", "button", "label", "legend"
    ].includes(tag);

    const isSemanticControl = [
        "combobox", "radio", "checkbox", "listbox",
        "option", "group", "radiogroup"
    ].includes(role);

    const isInterestingAutomationId =
        /formfield|field|label|select|dropdown|radio|checkbox|prompt/i
            .test(automationId);

    const hasAccessibleName =
        element.hasAttribute("aria-label") ||
        element.hasAttribute("aria-labelledby");

    const isContentEditable = element.isContentEditable;

    if (
        !isBasicControl &&
        !isSemanticControl &&
        !isInterestingAutomationId &&
        !hasAccessibleName &&
        !isContentEditable
    ) {
        continue;
    }

    // Avoid duplicate entries for the same DOM element.
    if (seen.has(element)) continue;
    seen.add(element);

    // Skip empty, generic layout containers unless they expose useful metadata.
    if (
        !isBasicControl &&
        !isSemanticControl &&
        !isContentEditable &&
        !getLabel(element) &&
        !automationId
    ) {
        continue;
    }

    results.push(describeControl(element, results.length + 1));

    if (results.length >= arguments[0]) break;
}

return {
    heading_found: Boolean(heading),
    container_found: containerFound,
    container_tag: root.tagName ? root.tagName.toLowerCase() : "",
    container_automation_id:
        root.getAttribute?.("data-automation-id") || "",
    page_loading_visible: Array.from(
        document.querySelectorAll("body *")
    ).some(element =>
        isVisible(element) &&
        /^(loading|please wait)\.?$/i.test(getText(element, 40))
    ),
    elements: results
};
"""


def _log_control(control):
    """Log one inspected control without changing it."""
    index = control.get("index", "?")
    tag = control.get("tag", "")
    control_type = control.get("type", "")
    role = control.get("role", "")
    label = control.get("label", "") or "(label not detected)"
    value = control.get("value", "")
    automation_id = control.get("automation_id", "")
    context = control.get("context", "")

    logger.info(
        "CONTROL %s | tag=%s | type=%s | role=%s | "
        "label=%r | required_attr=%s | required_marker=%s | "
        "disabled=%s | value=%r",
        index,
        tag,
        control_type,
        role,
        label,
        control.get("required_attribute", False),
        control.get("required_marker_visible", False),
        control.get("disabled", False),
        value,
    )

    if automation_id:
        logger.info(
            "  automation_id=%r",
            automation_id,
        )

    if control.get("placeholder"):
        logger.info(
            "  placeholder=%r",
            control["placeholder"],
        )

    if control.get("aria_labelledby"):
        logger.info(
            "  aria-labelledby=%r",
            control["aria_labelledby"],
        )

    if control.get("aria_expanded"):
        logger.info(
            "  aria-expanded=%s",
            control["aria_expanded"],
        )

    if context:
        logger.info(
            "  surrounding_question_text=%r",
            context,
        )

    options = control.get("options") or []
    if options:
        logger.info(
            "  native_select_options (%s):",
            len(options),
        )

        for option in options:
            logger.info(
                "    option=%r | value=%r | selected=%s | disabled=%s",
                option.get("text", ""),
                option.get("value", ""),
                option.get("selected", False),
                option.get("disabled", False),
            )


def scrape_my_information(driver, wait=None):
    """
    Inspect Workday's My Information page without changing form values.

    Logs visible native controls, custom ARIA controls, field labels,
    nearby question text, automation IDs, required indicators, current
    values, and options for native HTML select elements.

    This function does not click dropdowns, change answers, or submit.
    """
    logger.info("=" * 72)
    logger.info("Starting read-only Workday My Information inspection.")
    logger.info("No fields will be changed and no application will be submitted.")

    if wait is None:
        wait = WebDriverWait(driver, WAIT_SECONDS)

    try:
        wait.until(
            lambda current_driver: any(
                heading.is_displayed()
                and heading.text.strip().lower() == "my information"
                for heading in current_driver.find_elements(
                    By.CSS_SELECTOR,
                    "h1, h2, h3, h4, [role='heading']",
                )
            )
        )
        logger.info("Found the visible My Information heading.")

    except Exception as exc:
        logger.warning(
            "Could not confirm the My Information heading within %s seconds: %s",
            WAIT_SECONDS,
            exc,
        )
        logger.warning("Continuing with a page-wide diagnostic scan.")

    logger.info(
        "Waiting %s seconds for Workday fields to render...",
        RENDER_DELAY_SECONDS,
    )
    time.sleep(RENDER_DELAY_SECONDS)

    try:
        inspection = driver.execute_script(
            WORKDAY_INSPECTION_SCRIPT,
            MAX_DIAGNOSTIC_ELEMENTS,
        )
    except Exception:
        logger.exception("Could not inspect Workday's visible form elements.")
        return []

    if not inspection:
        logger.warning("Workday inspection returned no diagnostic data.")
        return []

    logger.info(
        "My Information heading found: %s",
        inspection.get("heading_found"),
    )
    logger.info(
        "Narrow form container found: %s",
        inspection.get("container_found"),
    )
    logger.info(
        "Inspection container: tag=%r, automation_id=%r",
        inspection.get("container_tag"),
        inspection.get("container_automation_id"),
    )
    logger.info(
        "Loading indicator visible: %s",
        inspection.get("page_loading_visible"),
    )

    controls = inspection.get("elements", [])

    logger.info(
        "Visible relevant elements inspected: %s",
        len(controls),
    )

    for control in controls:
        _log_control(control)

    # Summarize custom controls that may require separate handling later.
    custom_controls = [
        control
        for control in controls
        if control.get("role")
        in {"combobox", "radio", "checkbox", "listbox", "option", "group", "radiogroup"}
    ]

    unlabeled_controls = [
        control
        for control in controls
        if control.get("tag") in {"input", "textarea", "select", "button"}
        and not control.get("label")
    ]

    logger.info("-" * 72)
    logger.info(
        "Custom ARIA controls found: %s",
        len(custom_controls),
    )
    logger.info(
        "Native controls without a detected label: %s",
        len(unlabeled_controls),
    )

    if inspection.get("page_loading_visible"):
        logger.warning(
            "A loading indicator is still visible. Some fields may not "
            "have finished rendering."
        )

    if len(controls) >= MAX_DIAGNOSTIC_ELEMENTS:
        logger.warning(
            "The diagnostic element limit (%s) was reached. "
            "The inspection may be incomplete.",
            MAX_DIAGNOSTIC_ELEMENTS,
        )

    logger.info(
        "My Information inspection complete. "
        "No values were changed and no application was submitted."
    )
    logger.info("=" * 72)

    return controls
