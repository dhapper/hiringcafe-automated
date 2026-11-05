"""Greenhouse application portal handler.

Uploads the resume and cover letter when their respective upload fields
exist, then uses the shared scraper and filler for the remaining questions.

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
    {"source_file": "greenhouse.py"},
)


# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
COVER_LETTER_PATH = PROJECT_ROOT / "resume" / "Cover_Letter_Dhaarshan.pdf"


def _find_upload_input(driver, button, keyword):
    """Find the file input associated with an Attach button.

    Searches progressively larger parent containers until it finds a
    container containing the expected field label and exactly one file input.
    """

    current = button

    for _ in range(7):
        try:
            container_text = current.text.casefold()

            if keyword in container_text:
                inputs = current.find_elements(
                    By.CSS_SELECTOR,
                    "input[type='file']",
                )

                if len(inputs) == 1:
                    return inputs[0]

            current = current.find_element(By.XPATH, "..")

        except WebDriverException:
            break

    return None


def _upload_file(driver, keyword, file_path, description):
    """Upload a file through its corresponding Greenhouse Attach button."""

    if not file_path.is_file():
        logger.warning(
            "%s file does not exist; skipping upload: %s",
            description,
            file_path,
        )
        return False

    buttons = driver.find_elements(
        By.XPATH,
        "//button[normalize-space()='Attach']",
    )

    if not buttons:
        logger.info("No Attach buttons found for %s.", description)
        return False

    for button in buttons:
        try:
            if not button.is_displayed():
                continue

            file_input = _find_upload_input(driver, button, keyword)

            if file_input is None:
                continue

            file_input.send_keys(str(file_path.resolve()))
            time.sleep(1)

            uploaded_value = file_input.get_attribute("value") or ""

            if file_path.name.casefold() in uploaded_value.casefold():
                logger.info(
                    "Successfully uploaded %s: %s",
                    description,
                    file_path.name,
                )
                return True

            # Some Greenhouse pages clear the input after uploading.
            # Check the surrounding field for the displayed filename.
            container = button

            for _ in range(7):
                try:
                    if file_path.name.casefold() in container.text.casefold():
                        logger.info(
                            "Successfully uploaded %s: %s",
                            description,
                            file_path.name,
                        )
                        return True

                    container = container.find_element(By.XPATH, "..")

                except WebDriverException:
                    break

            logger.warning(
                "%s upload was attempted, but its completion " "could not be verified.",
                description,
            )
            return False

        except WebDriverException:
            logger.exception("Could not upload %s.", description)
            return False

    logger.info(
        "No matching %s upload field was found.",
        description,
    )
    return False


def run(driver):
    """Run the Greenhouse application handler."""

    logger.info("=" * 65)
    logger.info("Starting Greenhouse application handler.")

    # Allow the application form to render.
    time.sleep(3)

    # Upload resume if the field exists.
    _upload_file(
        driver,
        "resume",
        RESUME_PATH,
        "resume",
    )

    # Upload cover letter if the field exists.
    _upload_file(
        driver,
        "cover letter",
        COVER_LETTER_PATH,
        "cover letter",
    )

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

    logger.info("Greenhouse handler finished. Application was NOT submitted.")
    logger.info("=" * 65)

    return left_blank
