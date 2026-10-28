"""Workday application portal handler.

1. Click Apply if still on the job description page.
2. Click Autofill with Resume.
3. Read credentials from resume/my_info.txt.
4. Fill account-creation fields and check the required checkbox.
5. Click Create Account and wait for the Sign In heading.
6. Fill the sign-in fields and click Sign In.
7. Wait for the Autofill with Resume page.
8. Upload resume/Dhaarshan_Preshanthan_Resume.pdf.
9. Click Continue and detect that the page changed.

The final job application submission is never clicked automatically.
"""

import logging
import re
from pathlib import Path

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30

APPLY_SELECTOR = 'a[data-automation-id="adventureButton"][role="button"]'
RESUME_SELECTOR = 'a[data-automation-id="autofillWithResume"][role="button"]'
EMAIL_SELECTOR = 'input[data-automation-id="email"]'
PASSWORD_SELECTOR = 'input[data-automation-id="password"]'
VERIFY_PASSWORD_SELECTOR = 'input[data-automation-id="verifyPassword"]'
CHECKBOX_SELECTOR = 'input[data-automation-id="createAccountCheckbox"]'

CREATE_ACCOUNT_SELECTOR = (
    'div[data-automation-id="click_filter"]'
    '[role="button"][aria-label="Create Account"]'
)
SIGN_IN_SELECTOR = (
    'div[data-automation-id="click_filter"]' '[role="button"][aria-label="Sign In"]'
)
RESUME_HEADING_SELECTOR = "h3"
AUTH_HEADING_SELECTOR = "h3#authViewTitle"
FILE_INPUT_SELECTOR = 'input[type="file"]'
CONTINUE_SELECTOR = 'button[data-automation-id="pageFooterNextButton"]'


def read_account_info():
    """Read the email and password from resume/my_info.txt."""
    project_root = Path(__file__).resolve().parents[2]
    info_path = project_root / "resume" / "my_info.txt"

    if not info_path.is_file():
        raise FileNotFoundError(f"Personal information file not found: {info_path}")

    content = info_path.read_text(encoding="utf-8")

    email_match = re.search(
        r"^\s*email\s*:\s*(\S+@\S+\.\S+)\s*$",
        content,
        re.IGNORECASE | re.MULTILINE,
    )
    password_match = re.search(
        r"^\s*password\s*:\s*(.*?)\s*$",
        content,
        re.IGNORECASE | re.MULTILINE,
    )

    if not email_match:
        raise ValueError("Could not find a valid 'email: ...' entry in my_info.txt.")

    if not password_match or not password_match.group(1):
        raise ValueError("Could not find a valid 'password: ...' entry in my_info.txt.")

    return email_match.group(1).strip(), password_match.group(1)


def fill_field(wait, selector, value, field_name):
    """Wait for a field, fill it, and verify it is populated."""
    logger.info("Waiting for the Workday %s field...", field_name)
    field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
    field.clear()
    field.send_keys(value)

    if not field.get_attribute("value"):
        raise RuntimeError(
            f"The Workday {field_name} field appears to be empty after filling it."
        )

    logger.info("Filled the Workday %s field.", field_name)


def wait_for_heading(driver, selector, expected_text, timeout=WAIT_SECONDS):
    """Wait for a visible heading with the exact expected text."""

    def heading_visible(current_driver):
        for heading in current_driver.find_elements(By.CSS_SELECTOR, selector):
            if (
                heading.is_displayed()
                and heading.text.strip().casefold() == expected_text.casefold()
            ):
                return heading
        return False

    logger.info(
        'Waiting up to %s seconds for heading "%s"...',
        timeout,
        expected_text,
    )
    heading = WebDriverWait(driver, timeout, poll_frequency=0.5).until(heading_visible)
    logger.info("Detected heading: %r", heading.text.strip())
    return heading


def check_create_account_checkbox(driver, wait):
    """Check the account-creation checkbox and verify its state."""
    logger.info("Waiting for the Create Account checkbox...")
    checkbox = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, CHECKBOX_SELECTOR))
    )

    logger.info(
        "Checkbox found. Visible=%s, Enabled=%s, Selected=%s",
        checkbox.is_displayed(),
        checkbox.is_enabled(),
        checkbox.is_selected(),
    )

    if not checkbox.is_selected():
        try:
            if checkbox.is_displayed() and checkbox.is_enabled():
                checkbox.click()
            else:
                driver.execute_script("arguments[0].click();", checkbox)
        except WebDriverException:
            logger.warning("Normal checkbox click failed; trying JavaScript click.")
            driver.execute_script("arguments[0].click();", checkbox)

    def checkbox_is_checked(current_driver):
        element = current_driver.find_element(By.CSS_SELECTOR, CHECKBOX_SELECTOR)
        return element if element.is_selected() else False

    wait.until(checkbox_is_checked)
    logger.info("Create Account checkbox is checked.")


def wait_for_sign_in_result(driver, timeout=WAIT_SECONDS):
    """Wait for a visible error or a change away from the login page."""

    def result_detected(current_driver):
        if "/login" not in current_driver.current_url.lower():
            return "URL changed away from login"

        for selector in (
            '[role="alert"]',
            '[data-automation-id*="errorMessage"]',
        ):
            for element in current_driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and element.text.strip():
                    return "visible sign-in error"
        return False

    logger.info("Waiting for Workday to respond to sign-in...")
    try:
        result = WebDriverWait(driver, timeout, poll_frequency=0.5).until(
            result_detected
        )
        logger.info("Workday sign-in response detected: %s.", result)
        return result
    except TimeoutException:
        logger.warning("No clear sign-in result detected within %s seconds.", timeout)
        return None


def upload_resume(driver, wait, resume_path):
    """Upload the resume and continue without checking input.value."""
    logger.info("Waiting for an enabled resume file input...")

    def file_input_ready(current_driver):
        for element in current_driver.find_elements(
            By.CSS_SELECTOR, FILE_INPUT_SELECTOR
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

    # The input.value check was blocking progress even though the user could see
    # the resume attached in Workday. Do not use that check as a gate.
    logger.info("Skipping the unreliable file-input value check.")

    try:
        continue_button = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, CONTINUE_SELECTOR))
        )
    except TimeoutException:
        logger.error("Continue did not become clickable. Stopping for manual review.")
        return False

    if not continue_button.is_enabled():
        logger.error("Continue is disabled. Stopping for manual review.")
        return False

    logger.info("Clicking Continue.")
    continue_button.click()
    logger.info("Clicked Continue.")

    try:
        WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.5).until(
            lambda current_driver: not any(
                heading.is_displayed()
                and heading.text.strip().casefold() == "autofill with resume"
                for heading in current_driver.find_elements(
                    By.CSS_SELECTOR, RESUME_HEADING_SELECTOR
                )
            )
        )
    except TimeoutException:
        logger.warning(
            'The "Autofill with Resume" heading is still visible after Continue. '
            "Check for validation errors or upload processing."
        )
        logger.info("Current Workday URL: %s", driver.current_url)
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            logger.info(
                "Visible page text (first 1800 characters): %s", body_text[:1800]
            )
        except WebDriverException:
            logger.warning("Could not read visible page text.")
        return False

    logger.info("Workday moved past the resume-upload page.")
    logger.info("Current Workday URL: %s", driver.current_url)
    try:
        headings = [
            heading.text.strip()
            for heading in driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3")
            if heading.is_displayed() and heading.text.strip()
        ]
        logger.info("Visible headings on the next page: %s", headings[:15])
    except WebDriverException:
        logger.warning("Could not read headings on the next page.")

    logger.info(
        "Stopping at the next page for further detection. "
        "The job application has NOT been submitted automatically."
    )
    return True


def run(driver):
    """Run the Workday account setup, sign-in, and resume-upload steps."""
    wait = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.5)
    project_root = Path(__file__).resolve().parents[2]

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

        # Step 4: Fill the account-creation fields.
        fill_field(wait, EMAIL_SELECTOR, email, "email")
        fill_field(wait, PASSWORD_SELECTOR, password, "password")
        fill_field(wait, VERIFY_PASSWORD_SELECTOR, password, "password confirmation")
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
            wait_for_heading(driver, AUTH_HEADING_SELECTOR, "Sign In")
        except TimeoutException:
            logger.error(
                'The visible heading h3#authViewTitle with text "Sign In" '
                "did not appear within %s seconds.",
                WAIT_SECONDS,
            )
            logger.error("Current Workday URL: %s", driver.current_url)
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
                logger.error(
                    "Visible page text (first 1500 characters): %s", body_text[:1500]
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

        # Step 9: Click Sign In.
        logger.info("Waiting for the Sign In control...")
        sign_in_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SIGN_IN_SELECTOR))
        )
        logger.info("Clicking Sign In.")
        sign_in_button.click()
        logger.info("Clicked Sign In.")

        sign_in_result = wait_for_sign_in_result(driver, timeout=WAIT_SECONDS)
        if sign_in_result != "URL changed away from login":
            logger.warning(
                "Sign-in success was not confirmed by a URL change. "
                "Not attempting resume upload. Review the browser manually."
            )
            logger.info("Current Workday URL: %s", driver.current_url)
            return

        # Step 10: Wait for the resume-upload page heading.
        try:
            wait_for_heading(driver, RESUME_HEADING_SELECTOR, "Autofill with Resume")
        except TimeoutException:
            logger.error(
                'The "Autofill with Resume" heading did not appear after sign-in. '
                "Current URL: %s",
                driver.current_url,
            )
            return

        # Step 11: Locate the PDF to upload.
        resume_path = (
            project_root / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
        ).resolve()
        if not resume_path.is_file():
            logger.error("Resume PDF not found: %s", resume_path)
            return

        logger.info("Resume PDF exists and is ready to upload.")

        # Step 12 onward: upload, click Continue, and detect the next page.
        upload_resume(driver, wait, resume_path)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
    except TimeoutException:
        logger.error(
            "Timed out waiting for a Workday control or field. Current URL: %s",
            driver.current_url,
        )
    except WebDriverException:
        logger.exception("A Selenium error occurred in the Workday handler.")
