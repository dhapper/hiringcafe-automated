"""Gems application portal handler.

Uploads the resume first, then uses the shared scraper and filler
for the remaining application questions.

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
    {"source_file": "gems.py"},
)


# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESUME_PATH = PROJECT_ROOT / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"


def _find_resume_input(driver):
    """Find the Gems resume upload input."""

    file_inputs = driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='file']",
    )

    if not file_inputs:
        logger.warning("No file upload inputs found.")
        return None

    document_inputs = []

    for file_input in file_inputs:
        try:
            accept = (file_input.get_attribute("accept") or "").casefold()

            if any(extension in accept for extension in (".pdf", ".doc", ".docx")):
                document_inputs.append(file_input)

        except WebDriverException:
            continue

    if not document_inputs:
        logger.warning("No file input accepting PDF, DOC, or DOCX files was found.")
        return None

    # Prefer the input inside the Gems upload container.
    for file_input in document_inputs:
        try:
            container = file_input.find_element(
                By.XPATH,
                "./ancestor::div[@role='presentation'][1]",
            )

            if container:
                logger.info("Found the resume input inside the Gems upload container.")
                return file_input

        except WebDriverException:
            continue

    # Use a page-level input only when unambiguous.
    if len(document_inputs) == 1:
        return document_inputs[0]

    logger.warning(
        "Multiple document upload inputs were found and none could "
        "be uniquely identified as the resume input."
    )

    return None


def _upload_resume(driver):
    """Upload the resume through the Gems hidden file input."""

    if not RESUME_PATH.is_file():
        logger.error(
            "Resume file does not exist: %s",
            RESUME_PATH,
        )
        return False

    file_input = _find_resume_input(driver)

    if file_input is None:
        logger.warning("Could not identify the Gems resume upload input.")
        return False

    try:
        # The input is normally hidden with display:none.
        # Temporarily expose it so Selenium can send the file path.
        original_style = file_input.get_attribute("style") or ""

        driver.execute_script(
            """
            const el = arguments[0];

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
            # Restore the original styling.
            driver.execute_script(
                """
                arguments[0].setAttribute('style', arguments[1]);
                """,
                file_input,
                original_style,
            )

        time.sleep(2)

        # First verification: file input value.
        uploaded_value = file_input.get_attribute("value") or ""

        if RESUME_PATH.name.casefold() in uploaded_value.casefold():
            logger.info(
                "Successfully uploaded resume: %s",
                RESUME_PATH.name,
            )
            return True

        # Second verification: inspect the upload container for the filename.
        try:
            container = file_input.find_element(
                By.XPATH,
                "./ancestor::div[@role='presentation'][1]",
            )

            container_text = (container.text or "").casefold()

            if RESUME_PATH.name.casefold() in container_text:
                logger.info(
                    "Successfully uploaded resume: %s",
                    RESUME_PATH.name,
                )
                return True

        except WebDriverException:
            pass

        logger.warning(
            "Resume upload was attempted, but the filename could not "
            "be verified. Please check the browser."
        )

        return False

    except WebDriverException:
        logger.exception("Could not upload the resume.")
        return False


def run(driver):
    """Run the Gems application handler."""

    logger.info("=" * 65)
    logger.info("Starting Gems application handler.")

    # Allow the application form to render.
    time.sleep(2)

    # Upload resume FIRST.
    logger.info("Uploading resume before inspecting other form fields.")

    resume_uploaded = _upload_resume(driver)

    if not resume_uploaded:
        logger.warning(
            "Resume upload was not confirmed. "
            "Please check the browser before continuing."
        )

    # Allow the portal to process the uploaded resume.
    time.sleep(2)

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

    logger.info("Gems handler finished. Application was NOT submitted.")
    logger.info("=" * 65)

    return left_blank
