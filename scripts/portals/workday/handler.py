"""Workday application portal handler."""

import logging
from pathlib import Path

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .register import (
    APPLY_SELECTOR,
    AUTH_HEADING_SELECTOR,
    CREATE_ACCOUNT_SELECTOR,
    EMAIL_SELECTOR,
    PASSWORD_SELECTOR,
    RESUME_SELECTOR,
    VERIFY_PASSWORD_SELECTOR,
    WAIT_SECONDS,
    check_create_account_checkbox,
    fill_field,
    read_account_info,
    wait_for_heading,
)
from .login import (
    SIGN_IN_MAX_ATTEMPTS,
    click_sign_in_until_autofill_appears,
)
from .resume_upload import upload_resume
from .my_information import scrape_my_information
from .my_information_fill import fill_my_information
from .application_pages import (
    advance_page,
    heading_visible,
    page_headings,
    run_application_pages,
)


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "handler.py"},
)


def run(driver):
    """Run Workday account creation, sign-in, resume upload, and every page."""
    wait = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.5)

    # handler.py is in scripts/portals/workday/.
    project_root = Path(__file__).resolve().parents[3]

    try:
        # Step 1: Click Apply if still on the job description page.
        if "/apply" not in driver.current_url.lower():
            logger.info("Waiting for the Workday Apply button...")

            apply_button = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, APPLY_SELECTOR))
            )

            logger.info("Clicking Apply.")
            apply_button.click()

        # Step 2: Open Autofill with Resume.
        logger.info("Waiting for Autofill with Resume...")

        resume_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, RESUME_SELECTOR))
        )

        resume_button.click()

        logger.info("Clicked Autofill with Resume.")
        logger.info("Current Workday URL: %s", driver.current_url)

        # Step 3: Load account credentials.
        email, password = read_account_info()
        logger.info("Loaded credentials from my_info.txt.")

        # Step 4: Fill account-creation fields.
        fill_field(wait, EMAIL_SELECTOR, email, "email")
        fill_field(wait, PASSWORD_SELECTOR, password, "password")
        fill_field(
            wait,
            VERIFY_PASSWORD_SELECTOR,
            password,
            "password confirmation",
        )

        logger.info("Account-creation fields are populated.")

        # Step 5: Check the required checkbox.
        check_create_account_checkbox(driver, wait)

        # Step 6: Click Create Account.
        logger.info("Waiting for the Create Account control...")

        create_account_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, CREATE_ACCOUNT_SELECTOR))
        )

        logger.info("Clicking Create Account.")
        create_account_button.click()
        logger.info("Clicked Create Account.")

        # Step 7: Wait for the exact Sign In heading.
        try:
            wait_for_heading(
                driver,
                AUTH_HEADING_SELECTOR,
                "Sign In",
            )

        except TimeoutException:
            logger.error(
                "The visible heading h3#authViewTitle with text "
                '"Sign In" did not appear within %s seconds.',
                WAIT_SECONDS,
            )
            logger.error("Current Workday URL: %s", driver.current_url)

            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text

                logger.error(
                    "Visible page text (first 1500 characters): %s",
                    body_text[:1500],
                )

            except WebDriverException:
                logger.warning("Could not read visible page text.")

            logger.warning("Stopping for manual browser review.")
            return

        # Step 8: Fill the sign-in form.
        email, password = read_account_info()
        logger.info("Reloaded credentials from my_info.txt.")

        fill_field(wait, EMAIL_SELECTOR, email, "sign-in email")
        fill_field(wait, PASSWORD_SELECTOR, password, "sign-in password")

        email_field = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, EMAIL_SELECTOR))
        )

        password_field = wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, PASSWORD_SELECTOR))
        )

        if not email_field.get_attribute("value") or not password_field.get_attribute(
            "value"
        ):
            raise RuntimeError("Sign-in fields were empty immediately before Sign In.")

        logger.info("Sign-in fields verified as populated.")

        # Step 9: Sign in. Returns "autofill" on a first visit, or "progress"
        # when Workday saved earlier progress and skipped the resume page.
        landing = click_sign_in_until_autofill_appears(driver)

        if not landing:
            logger.error(
                "Could not confirm a signed-in page after %s Sign In attempts. "
                "Stopping for manual review.",
                SIGN_IN_MAX_ATTEMPTS,
            )
            return

        if landing == "autofill":
            # Step 10: Locate the resume PDF.
            resume_path = (
                project_root / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
            ).resolve()

            if not resume_path.is_file():
                logger.error("Resume PDF not found: %s", resume_path)
                return

            logger.info("Resume PDF exists and is ready to upload.")

            # Step 11: Upload the resume and continue to My Information.
            upload_resume(driver, wait, resume_path)
        else:
            logger.info(
                "Saved progress detected: Workday skipped the resume page. "
                "Continuing from the current page."
            )

        # Step 12: My Information page (Workday-specific fields), if that is
        # where the application is.
        if heading_visible(driver, "My Information"):
            scrape_my_information(driver, wait)
            headings_before = page_headings(driver)

            left_blank = fill_my_information(driver)

            if left_blank:
                logger.warning(
                    "Stopping on My Information: required fields are still blank: %s",
                    left_blank,
                )
                return

            # Step 13: Save and Continue, unless the filler already moved on.
            if heading_visible(driver, "My Information"):
                if not advance_page(driver, headings_before):
                    return

        # Step 14: Every later page uses the common scraper and filler.
        run_application_pages(driver)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)

    except TimeoutException:
        logger.error(
            "Timed out waiting for a Workday control or field. " "Current URL: %s",
            driver.current_url,
        )

    except WebDriverException:
        logger.exception("A Selenium error occurred in the Workday handler.")
