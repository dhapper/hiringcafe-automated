"""Workday resume upload and read-only page inspection."""

import logging

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .login import is_autofill_resume_visible
from .register import WAIT_SECONDS


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "resume_upload.py"},
)


FILE_INPUT_SELECTOR = 'input[type="file"]'
CONTINUE_SELECTOR = 'button[data-automation-id="pageFooterNextButton"]'


def inspect_current_page(driver):
    """Print a read-only inventory of the current page's form controls."""
    logger.info("=" * 72)
    logger.info("READ-ONLY WORKDAY PAGE INSPECTION")
    logger.info("Current URL: %s", driver.current_url)

    try:
        headings = [
            heading.text.strip()
            for heading in driver.find_elements(
                By.CSS_SELECTOR,
                "h1, h2, h3, h4",
            )
            if heading.is_displayed() and heading.text.strip()
        ]

        logger.info("Visible headings: %s", headings[:30])

    except WebDriverException:
        logger.warning("Could not read visible headings.")

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text

        logger.info(
            "Visible page text (first 5000 characters):\n%s",
            body_text[:5000],
        )

    except WebDriverException:
        logger.warning("Could not read visible page text.")

    try:
        controls = driver.find_elements(
            By.CSS_SELECTOR,
            "input, textarea, select, button, "
            "[role='combobox'], [role='radio'], [role='checkbox']",
        )

        visible_controls = [control for control in controls if control.is_displayed()]

        logger.info(
            "Visible form controls found: %s",
            len(visible_controls),
        )

        for index, control in enumerate(controls, start=1):
            if not control.is_displayed():
                continue

            try:
                tag = control.tag_name
                control_type = control.get_attribute("type") or ""
                automation_id = control.get_attribute("data-automation-id") or ""
                name = control.get_attribute("name") or ""
                element_id = control.get_attribute("id") or ""
                value = control.get_attribute("value") or ""
                placeholder = control.get_attribute("placeholder") or ""
                aria_label = control.get_attribute("aria-label") or ""
                role = control.get_attribute("role") or ""
                text = control.text.strip().replace("\n", " ")[:200]

                required = (
                    control.get_attribute("required") is not None
                    or control.get_attribute("aria-required") == "true"
                )

                logger.info(
                    "CONTROL %s | tag=%r type=%r role=%r "
                    "automation_id=%r name=%r id=%r value=%r "
                    "placeholder=%r aria_label=%r required=%s text=%r",
                    index,
                    tag,
                    control_type,
                    role,
                    automation_id,
                    name,
                    element_id,
                    value[:150],
                    placeholder,
                    aria_label,
                    required,
                    text,
                )

                if tag.lower() == "select":
                    options = [
                        option.text.strip()
                        for option in control.find_elements(
                            By.TAG_NAME,
                            "option",
                        )
                        if option.text.strip()
                    ]

                    logger.info("  SELECT OPTIONS: %s", options[:50])

            except WebDriverException:
                logger.debug(
                    "Could not inspect control %s.",
                    index,
                    exc_info=True,
                )

    except WebDriverException:
        logger.warning("Could not enumerate form controls.")

    logger.info(
        "Read-only inspection finished. No application answers were "
        "filled by the inspector, and no final submission was made."
    )
    logger.info("=" * 72)


def upload_resume(driver, wait, resume_path):
    """Upload the resume, click Continue, and inspect the next page."""
    logger.info("Waiting for an enabled resume file input...")

    def file_input_ready(current_driver):
        for element in current_driver.find_elements(
            By.CSS_SELECTOR,
            FILE_INPUT_SELECTOR,
        ):
            if element.is_enabled():
                return element

        return False

    try:
        file_input = wait.until(file_input_ready)

    except TimeoutException:
        logger.error("No enabled input[type=file] was found. Not clicking Continue.")
        return False

    file_input.send_keys(str(resume_path))

    logger.info("Sent the resume path to the file input.")
    logger.info("Skipping the unreliable file-input value check.")

    try:
        continue_button = WebDriverWait(
            driver,
            WAIT_SECONDS,
            poll_frequency=0.5,
        ).until(EC.element_to_be_clickable((By.CSS_SELECTOR, CONTINUE_SELECTOR)))

    except TimeoutException:
        logger.error(
            "Continue did not become clickable. " "Stopping for manual review."
        )
        return False

    if not continue_button.is_enabled():
        logger.error("Continue is disabled. Stopping for manual review.")
        return False

    logger.info("Clicking Continue.")
    continue_button.click()
    logger.info("Clicked Continue.")

    try:
        WebDriverWait(
            driver,
            WAIT_SECONDS,
            poll_frequency=0.5,
        ).until(lambda current_driver: not is_autofill_resume_visible(current_driver))

    except TimeoutException:
        logger.warning(
            'The "Autofill with Resume" heading is still visible after '
            "Continue. Check for validation errors or upload processing."
        )

        logger.info("Current Workday URL: %s", driver.current_url)

        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text

            logger.info(
                "Visible page text (first 1800 characters): %s",
                body_text[:1800],
            )

        except WebDriverException:
            logger.warning("Could not read visible page text.")

        return False

    logger.info("Workday moved past the resume-upload page.")
    logger.info("Current Workday URL: %s", driver.current_url)

    # Inspect the next page without filling or submitting anything.
    inspect_current_page(driver)

    logger.info(
        "Stopping at the next page for further detection. "
        "The job application has NOT been submitted automatically."
    )

    return True
