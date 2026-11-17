"""CareerPuck application portal handler.

Uploads the resume through the Attach button, then uses the shared
scraper and filler for the remaining application questions.

Never submits the application.
"""

import logging
import time
from pathlib import Path

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from common.scrape_content import scrape_form_content
from common.fill_content import fill_form_content


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "careerpuck.py"},
)


# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"


def _find_upload_input(driver, button):
    """Find the file input associated with the CareerPuck Attach button.

    Searches progressively larger parent containers for a file input.
    Falls back to the page-level input only when exactly one exists.
    """

    current = button

    for _ in range(8):
        try:
            inputs = current.find_elements(
                By.CSS_SELECTOR,
                "input[type='file']",
            )

            if len(inputs) == 1:
                return inputs[0]

            current = current.find_element(By.XPATH, "..")

        except WebDriverException:
            break

    # Fallback: use the page-level input if it is unambiguous.
    try:
        inputs = driver.find_elements(
            By.CSS_SELECTOR,
            "input[type='file']",
        )

        if len(inputs) == 1:
            return inputs[0]

    except WebDriverException:
        logger.exception("Could not search for the resume file input.")

    return None


def _upload_resume(driver):
    """Upload the resume using the CareerPuck Attach button."""

    if not RESUME_PATH.is_file():
        logger.warning(
            "Resume file does not exist; skipping upload: %s",
            RESUME_PATH,
        )
        return False

    buttons = driver.find_elements(
        By.XPATH,
        "//button[normalize-space()='Attach']",
    )

    if not buttons:
        logger.warning("No CareerPuck Attach button was found.")
        return False

    for button in buttons:
        try:
            if not button.is_displayed():
                continue

            file_input = _find_upload_input(driver, button)

            if file_input is None:
                logger.warning(
                    "Could not identify the file input associated "
                    "with the Attach button."
                )
                continue

            # Selenium can send a file path directly to a hidden file input.
            file_input.send_keys(str(RESUME_PATH.resolve()))

            time.sleep(2)

            # Check whether the browser still exposes the uploaded filename.
            uploaded_value = file_input.get_attribute("value") or ""

            if RESUME_PATH.name.casefold() in uploaded_value.casefold():
                logger.info(
                    "Successfully uploaded resume: %s",
                    RESUME_PATH.name,
                )
                return True

            # Some portals clear the input after uploading.
            # Check the button's surrounding container for the filename.
            container = button

            for _ in range(8):
                try:
                    if RESUME_PATH.name.casefold() in container.text.casefold():
                        logger.info(
                            "Successfully uploaded resume: %s",
                            RESUME_PATH.name,
                        )
                        return True

                    container = container.find_element(By.XPATH, "..")

                except WebDriverException:
                    break

            logger.warning(
                "Resume upload was attempted, but completion " "could not be verified."
            )
            return False

        except WebDriverException:
            logger.exception("Could not upload the resume.")
            return False

    logger.warning("No usable CareerPuck resume upload field was found.")
    return False


def run(driver):
    """Run the CareerPuck application handler."""

    logger.info("=" * 65)
    logger.info("Starting CareerPuck application handler.")

    # Allow the application form to render.
    time.sleep(3)

    # Upload resume.
    _upload_resume(driver)

    # Scrape the remaining application questions.
    logger.info("Starting common form inspection.")

    form_content = scrape_form_content(driver)

    # Fill remaining fields using the common AI/profile logic.
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

    logger.info("CareerPuck handler finished. Application was NOT submitted.")
    logger.info("=" * 65)

    return left_blank
