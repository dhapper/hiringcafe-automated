import logging
import time
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common.scrape_content import scrape_form_content
from common.fill_content import fill_form_content


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "dayforcehcm.py"},
)

NEXT_BUTTON_SELECTOR = 'button[test-id="application-next-step"]'

COOKIE_TITLE_SELECTOR = '[test-id="cookie-preferences-banner-title"]'
COOKIE_REJECT_SELECTOR = 'button[test-id="cookie-preferences-banner-reject-button"]'
COOKIE_CLICK_ATTEMPTS = 3

# Do not click Next while a required field on the page is still blank.
STOP_IF_REQUIRED_BLANK = True
PAGE_CHANGE_WAIT_SECONDS = 15

# Safety cap on the page loop. The loop normally ends by itself on the final
# page, which has no Next button (Submit is left for you to click).
MAX_APPLICATION_PAGES = 6
NEXT_BUTTON_WAIT_SECONDS = 5


def reject_cookie_banner(driver):
    """Reject cookie preferences if the banner appears.

    The banner can re-render right after it shows up, which invalidates an
    element found a moment earlier. The button is therefore looked up again on
    every attempt instead of being held across the click.
    """
    try:
        WebDriverWait(driver, 3).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, COOKIE_TITLE_SELECTOR))
        )
    except TimeoutException:
        logger.info("Cookie preferences banner not detected. Continuing.")
        return

    for attempt in range(1, COOKIE_CLICK_ATTEMPTS + 1):
        try:
            button = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, COOKIE_REJECT_SELECTOR))
            )
            button.click()
            logger.info("Cookie preferences banner detected. Clicked Reject.")
            return

        except (StaleElementReferenceException, ElementClickInterceptedException):
            logger.info(
                "Cookie banner changed while clicking; retrying (%s of %s).",
                attempt,
                COOKIE_CLICK_ATTEMPTS,
            )
            time.sleep(0.5)

        except TimeoutException:
            logger.info("Cookie reject button did not become clickable. Continuing.")
            return

    logger.warning(
        "Could not click the cookie Reject button; continuing with the banner open."
    )


def visible_form_errors(driver):
    """Return any visible validation messages on the page."""
    errors = []

    for selector in (
        ".ant-form-item-explain-error",
        '[role="alert"]',
        ".ant-message-notice",
    ):
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = element.text.strip()

                if element.is_displayed() and text:
                    errors.append(text)

        except WebDriverException:
            continue

    return list(dict.fromkeys(errors))


def next_button_present(driver):
    """Return True if a visible Next button is on the page."""
    try:
        return any(
            element.is_displayed()
            for element in driver.find_elements(By.CSS_SELECTOR, NEXT_BUTTON_SELECTOR)
        )
    except WebDriverException:
        return False


def click_next(driver):
    """Click Next, looking the button up again if the page re-renders."""
    wait = WebDriverWait(driver, 15)

    logger.info("Waiting for the Next button.")

    for attempt in range(1, 4):
        try:
            next_button = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, NEXT_BUTTON_SELECTOR))
            )
            logger.info("Next button is clickable. Clicking it.")
            next_button.click()
            logger.info("Clicked Next.")
            return

        except (StaleElementReferenceException, ElementClickInterceptedException):
            logger.info(
                "Next button changed while clicking; retrying (%s of 3).", attempt
            )
            time.sleep(0.5)

    raise RuntimeError("Could not click the Next button.")


def wait_for_page_change(driver, form_content):
    """Wait until the page that was just filled is replaced. Returns True/False."""
    old_url = driver.current_url
    marker_id = next(
        (
            control["id"]
            for control in form_content.get("controls", [])
            if control.get("id")
        ),
        None,
    )

    def changed(current_driver):
        if current_driver.current_url != old_url:
            return True

        if not marker_id:
            return False

        try:
            elements = current_driver.find_elements(
                By.CSS_SELECTOR,
                f'[id="{marker_id}"]',
            )
            return not any(element.is_displayed() for element in elements)

        except StaleElementReferenceException:
            return True

    try:
        WebDriverWait(driver, PAGE_CHANGE_WAIT_SECONDS).until(changed)

    except TimeoutException:
        logger.warning(
            "The page did not change after clicking Next. Visible errors: %s",
            visible_form_errors(driver) or "none detected",
        )
        return False

    logger.info("The page changed after clicking Next.")
    return True


def run(driver):
    """Open the Dayforce application and fill every page up to the final one."""

    wait = WebDriverWait(driver, 15)

    logger.info("Waiting for the Apply button.")
    apply_button = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[test-id="apply-button"]'))
    )
    apply_button.click()
    logger.info("Clicked Apply.")

    logger.info("Waiting 2 seconds before the next step.")
    time.sleep(2)

    logger.info("Waiting for Apply without an Account.")
    apply_without_account = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, 'button[test-id="apply-without-account"]')
        )
    )
    apply_without_account.click()
    logger.info("Clicked Apply without an Account.")

    reject_cookie_banner(driver)

    upload_resume(driver)

    # Give Dayforce a moment to render the application questions.
    logger.info("Waiting 3 seconds before scraping application questions.")
    time.sleep(3)

    # One loop handles every page: scrape, fill, then Next. The final page has
    # no Next button, so the loop stops there and Submit is left to you.
    for page_number in range(1, MAX_APPLICATION_PAGES + 1):
        logger.info("=== Application page %s ===", page_number)

        form_content = scrape_form_content(driver)

        logger.info(
            "Page %s scraped. Detected %s visible form controls.",
            page_number,
            len(form_content.get("controls", [])),
        )

        left_blank = fill_form_content(driver, form_content)

        # The cover letter upload only exists on the first page.
        if page_number == 1 and upload_cover_letter(driver):
            logger.info("Waiting 3 seconds for the cover letter upload to finish.")
            time.sleep(3)

        if left_blank and STOP_IF_REQUIRED_BLANK:
            logger.warning(
                "Stopping on page %s: required fields are still blank: %s",
                page_number,
                left_blank,
            )
            break

        # No Next button means this is the final page (Submit is manual).
        try:
            WebDriverWait(driver, NEXT_BUTTON_WAIT_SECONDS).until(next_button_present)
        except TimeoutException:
            logger.info(
                "No Next button on page %s: this looks like the final page. "
                "Submit has NOT been clicked.",
                page_number,
            )
            break

        click_next(driver)

        if not wait_for_page_change(driver, form_content):
            break

        logger.info("Waiting 3 seconds for the next page to render.")
        time.sleep(3)

    else:
        logger.warning(
            "Reached the page limit (%s) without finding a final page.",
            MAX_APPLICATION_PAGES,
        )

    logger.info(
        "Browser is ready for manual review. Application will NOT be submitted."
    )


def upload_resume(driver):
    """Upload the resume directly through the file input and verify its filename."""

    wait = WebDriverWait(driver, 15)

    resume_path = (
        Path(__file__).resolve().parents[2]
        / "resume"
        / "Dhaarshan_Preshanthan_Resume.pdf"
    )

    if not resume_path.is_file():
        logger.error("Resume file not found: %s", resume_path)
        raise FileNotFoundError(f"Resume file not found: {resume_path}")

    logger.info("Preparing resume upload: %s", resume_path.name)

    # Locate the file input without clicking Import Resume.
    file_input = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="file"]'))
    )

    file_input.send_keys(str(resume_path.resolve()))
    logger.info("Sent resume path directly to the file input.")

    try:
        uploaded_resume = WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "span.ant-upload-list-item-name"
                    '[title="Dhaarshan_Preshanthan_Resume.pdf"]',
                )
            )
        )

        logger.info("Resume filename displayed by Dayforce: %s", uploaded_resume.text)
        logger.info("Resume upload appears successful; check for any upload errors.")

    except TimeoutException:
        logger.error("Could not verify the resume upload.")
        raise RuntimeError("Resume upload could not be verified on the Dayforce page.")


def upload_cover_letter(driver):
    """Upload the cover letter using the file input associated with Add Cover Letter."""

    cover_letter_path = (
        Path(__file__).resolve().parents[2] / "resume" / "Cover_Letter_Dhaarshan.pdf"
    )

    cover_letter_button = driver.find_elements(
        By.CSS_SELECTOR,
        'button[test-id="cover-letter-upload-button"]',
    )

    if not cover_letter_button:
        logger.info("Cover Letter upload button not found. Skipping upload.")
        return False

    if not cover_letter_path.is_file():
        logger.error("Cover Letter file not found: %s", cover_letter_path)
        raise FileNotFoundError(f"Cover Letter file not found: {cover_letter_path}")

    logger.info("Preparing cover letter upload: %s", cover_letter_path.name)

    # Find the upload component containing the Cover Letter button.
    upload_container = cover_letter_button[0].find_element(
        By.XPATH,
        "./ancestor::*[.//input[@type='file']][1]",
    )

    file_input = upload_container.find_element(
        By.CSS_SELECTOR,
        'input[type="file"]',
    )

    file_input.send_keys(str(cover_letter_path.resolve()))

    logger.info("Sent cover letter path to the Cover Letter file input.")

    try:
        uploaded_cover_letter = WebDriverWait(driver, 20).until(
            EC.visibility_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "span.ant-upload-list-item-name"
                    '[title="Cover_Letter_Dhaarshan.pdf"]',
                )
            )
        )

        logger.info(
            "Cover Letter filename displayed by Dayforce: %s",
            uploaded_cover_letter.text,
        )
        logger.info("Cover Letter upload appears successful.")

        return True

    except TimeoutException:
        logger.error("Could not verify the cover letter upload.")
        raise RuntimeError(
            "Cover Letter upload could not be verified on the Dayforce page."
        )
