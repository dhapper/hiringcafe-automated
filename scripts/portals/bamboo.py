"""Bamboo application portal handler.

Uploads the resume when a file-upload field exists, then uses the shared
scraper and filler for the remaining questions.

Detects reCAPTCHA but leaves verification to the user.
Never submits the application.
"""

import logging
import time
from pathlib import Path

from selenium.common.exceptions import (
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from common.scrape_content import scrape_form_content
from common.fill_content import fill_form_content


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "bamboo.py"},
)


# Project paths
# Assumes this handler is saved beside greenhouse.py.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"


def _click_apply_button(driver):
    """Open the application form by clicking Apply for This Job."""

    selectors = (
        "button[data-bi-id='careers-site-apply-button']",
        "//button[.//span[normalize-space()='Apply for This Job']]",
    )

    for selector in selectors:
        try:
            if selector.startswith("//"):
                buttons = driver.find_elements(By.XPATH, selector)
            else:
                buttons = driver.find_elements(By.CSS_SELECTOR, selector)

            for button in buttons:
                if button.is_displayed() and button.is_enabled():
                    button.click()
                    logger.info(
                        "Clicked 'Apply for This Job'. Waiting for "
                        "the application form to load."
                    )
                    time.sleep(3)
                    return True

        except WebDriverException:
            logger.exception("Could not click the 'Apply for This Job' button.")
            return False

    logger.warning(
        "'Apply for This Job' button was not found. "
        "Continuing with the current page."
    )
    return False


def _find_file_input(driver, button):
    """Find the file input associated with a Bamboo Choose File button."""

    current = button

    # Search progressively larger parent containers.
    for _ in range(8):
        try:
            inputs = current.find_elements(
                By.CSS_SELECTOR,
                "input[type='file']",
            )

            if len(inputs) == 1:
                return inputs[0]

            if len(inputs) > 1:
                document_inputs = []

                for file_input in inputs:
                    accept = (file_input.get_attribute("accept") or "").casefold()

                    if any(
                        extension in accept for extension in (".pdf", ".doc", ".docx")
                    ):
                        document_inputs.append(file_input)

                if len(document_inputs) == 1:
                    return document_inputs[0]

            current = current.find_element(By.XPATH, "..")

        except (NoSuchElementException, WebDriverException):
            break

    # Fallback: only use a page-level input if it is unambiguous.
    page_inputs = driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='file']",
    )

    if len(page_inputs) == 1:
        return page_inputs[0]

    document_inputs = []

    for file_input in page_inputs:
        try:
            accept = (file_input.get_attribute("accept") or "").casefold()

            if any(extension in accept for extension in (".pdf", ".doc", ".docx")):
                document_inputs.append(file_input)

        except WebDriverException:
            continue

    if len(document_inputs) == 1:
        return document_inputs[0]

    return None


def _upload_resume(driver):
    """Upload the resume through Bamboo's Choose File button."""

    if not RESUME_PATH.is_file():
        logger.warning(
            "Resume file does not exist; skipping upload: %s",
            RESUME_PATH,
        )
        return False

    buttons = driver.find_elements(
        By.XPATH,
        "//button[.//span[normalize-space()='Choose File*'] "
        "or normalize-space()='Choose File*' "
        "or .//span[contains(normalize-space(), 'Choose File')]]",
    )

    # Fallback for minor label variations.
    if not buttons:
        buttons = driver.find_elements(
            By.XPATH,
            "//button[contains(translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
            "'choose file')]",
        )

    if not buttons:
        logger.info("No Bamboo Choose File button found; skipping resume upload.")
        return False

    for button in buttons:
        try:
            if not button.is_displayed() or not button.is_enabled():
                continue

            file_input = _find_file_input(driver, button)

            if file_input is None:
                logger.warning(
                    "Found the Choose File button, but could not uniquely "
                    "identify its associated file input."
                )
                continue

            # Try sending the file path directly first.
            try:
                file_input.send_keys(str(RESUME_PATH.resolve()))

            except WebDriverException:
                # Some portals visually hide the file input.
                # Temporarily expose it for Selenium.
                driver.execute_script(
                    """
                    const el = arguments[0];

                    el.setAttribute(
                        'data-chatgpt-original-style',
                        el.getAttribute('style') || ''
                    );

                    el.style.display = 'block';
                    el.style.visibility = 'visible';
                    el.style.opacity = '1';
                    el.style.position = 'static';
                    el.style.width = '1px';
                    el.style.height = '1px';
                    """,
                    file_input,
                )

                try:
                    file_input.send_keys(str(RESUME_PATH.resolve()))

                finally:
                    driver.execute_script(
                        """
                        const el = arguments[0];

                        const oldStyle = el.getAttribute(
                            'data-chatgpt-original-style'
                        );

                        if (oldStyle !== null) {
                            el.setAttribute('style', oldStyle);
                            el.removeAttribute(
                                'data-chatgpt-original-style'
                            );
                        }
                        """,
                        file_input,
                    )

            time.sleep(1)

            uploaded_value = file_input.get_attribute("value") or ""

            if RESUME_PATH.name.casefold() in uploaded_value.casefold():
                logger.info(
                    "Successfully uploaded resume: %s",
                    RESUME_PATH.name,
                )
                return True

            # Some portals clear the input after uploading and display
            # the filename elsewhere in the upload component.
            try:
                container = button

                for _ in range(8):
                    if RESUME_PATH.name.casefold() in (container.text or "").casefold():
                        logger.info(
                            "Successfully uploaded resume: %s",
                            RESUME_PATH.name,
                        )
                        return True

                    container = container.find_element(
                        By.XPATH,
                        "..",
                    )

            except WebDriverException:
                pass

            logger.warning(
                "Resume upload was attempted, but completion could not "
                "be verified. Please check the browser."
            )
            return False

        except WebDriverException:
            logger.exception("Could not upload the resume.")
            return False

    logger.warning(
        "A Choose File button was found, but no usable associated "
        "file input was identified."
    )
    return False


def _detect_recaptcha(driver):
    """Detect reCAPTCHA and request manual completion.

    Does not click, solve, or otherwise interact with the CAPTCHA.
    """

    selectors = (
        "#recaptcha-anchor",
        ".g-recaptcha",
        ".recaptcha-checkbox",
        "iframe[src*='recaptcha']",
        "iframe[title*='reCAPTCHA']",
    )

    for selector in selectors:
        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            )

            if any(element.is_displayed() for element in elements):
                logger.warning(
                    "reCAPTCHA detected. Complete the verification "
                    "manually in the browser."
                )
                return True

        except WebDriverException:
            continue

    logger.info("No visible reCAPTCHA detected.")
    return False


def run(driver):
    """Run the Bamboo application handler."""

    logger.info("=" * 65)
    logger.info("Starting Bamboo application handler.")

    # Open the application form first.
    time.sleep(2)
    _click_apply_button(driver)

    # Allow the application form to render.
    time.sleep(2)

    # Upload resume if the field exists.
    _upload_resume(driver)

    # Detect CAPTCHA. Verification must be completed manually.
    recaptcha_present = _detect_recaptcha(driver)

    # Scrape the remaining application questions.
    logger.info("Starting common form inspection.")

    form_content = scrape_form_content(driver)

    # Fill remaining fields using the shared AI/profile logic.
    logger.info("Starting common form filling.")

    left_blank = fill_form_content(
        driver,
        form_content,
    )

    if left_blank:
        logger.warning(
            "Required fields still need manual attention: %s",
            left_blank,
        )
    else:
        logger.info("All detected required fields were handled.")

    if recaptcha_present:
        logger.warning(
            "Please ensure reCAPTCHA has been completed manually "
            "before continuing with the application."
        )

    logger.info("Bamboo handler finished. Application was NOT submitted.")
    logger.info("=" * 65)

    return left_blank
