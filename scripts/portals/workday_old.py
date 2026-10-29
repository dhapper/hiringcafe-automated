"""Workday application portal handler.

1. Click Apply if still on the job description page.
2. Click Autofill with Resume.
3. Read credentials from resume/my_info.txt.
4. Fill account-creation fields and check the required checkbox.
5. Click Create Account and wait for the Sign In heading.
6. Fill the sign-in fields.
7. Try JavaScript Sign In clicks up to three times, checking for the
   Autofill with Resume page after each attempt.
8. Upload resume/Dhaarshan_Preshanthan_Resume.pdf.
9. Click Continue and inspect the next page in read-only mode.

The final job application submission is never clicked automatically.
"""

import logging
import re
import time
from pathlib import Path

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30
SIGN_IN_MAX_ATTEMPTS = 3
SIGN_IN_RETRY_INTERVAL = 1
SIGN_IN_FINAL_WAIT = 10

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

    heading = WebDriverWait(driver, timeout, poll_frequency=0.25).until(heading_visible)

    logger.info("Detected heading: %r", heading.text.strip())
    return heading


def is_autofill_resume_visible(driver):
    """Return True if the Autofill with Resume heading is visible."""
    try:
        for heading in driver.find_elements(By.CSS_SELECTOR, RESUME_HEADING_SELECTOR):
            if (
                heading.is_displayed()
                and heading.text.strip().casefold() == "autofill with resume"
            ):
                return True
    except WebDriverException:
        logger.debug(
            "Could not check the Autofill with Resume heading yet.",
            exc_info=True,
        )

    return False


def log_sign_in_diagnostics(driver):
    """Log useful page state if sign-in does not reach the expected page."""
    logger.warning("Sign-in retry attempts did not confirm the expected page.")
    logger.warning("Current Workday URL: %s", driver.current_url)

    try:
        headings = [
            heading.text.strip()
            for heading in driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3")
            if heading.is_displayed() and heading.text.strip()
        ]
        logger.warning("Visible page headings: %s", headings[:20])
    except WebDriverException:
        logger.warning("Could not read visible page headings.")

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        logger.warning(
            "Visible page text (first 2500 characters): %s",
            body_text[:2500],
        )
    except WebDriverException:
        logger.warning("Could not read visible page text.")

    try:
        errors = []
        for selector in (
            '[role="alert"]',
            '[aria-live="assertive"]',
            '[data-automation-id*="errorMessage"]',
        ):
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                if element.is_displayed() and element.text.strip():
                    errors.append(element.text.strip())

        if errors:
            logger.warning("Visible sign-in errors: %s", errors[:10])
        else:
            logger.info("No visible sign-in error messages were detected.")
    except WebDriverException:
        logger.warning("Could not inspect sign-in error messages.")


def click_sign_in_until_autofill_appears(driver):
    """Try clicking Sign In up to three times until Autofill with Resume appears.

    Each click uses JavaScript on the visible Sign In div, matching the
    successful browser-console test. The hidden submit button is not targeted.
    """
    logger.info("Checking whether Autofill with Resume is already visible...")

    if is_autofill_resume_visible(driver):
        logger.info("Autofill with Resume is already visible; no sign-in click needed.")
        return True

    for attempt in range(1, SIGN_IN_MAX_ATTEMPTS + 1):
        # Always check the success condition before another click.
        if is_autofill_resume_visible(driver):
            logger.info(
                "Autofill with Resume appeared before attempt %s.",
                attempt,
            )
            return True

        logger.info(
            "Sign In click attempt %s of %s.",
            attempt,
            SIGN_IN_MAX_ATTEMPTS,
        )

        try:
            button = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.25).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SIGN_IN_SELECTOR))
            )

            if not button.is_displayed():
                logger.warning(
                    "Sign In control is not visible on attempt %s.",
                    attempt,
                )
            else:
                # This matches the browser-console click that worked.
                driver.execute_script("""
                    const button = document.querySelector(
                        'div[data-automation-id="click_filter"]'
                        + '[aria-label="Sign In"][role="button"]'
                    );

                    if (!button) {
                        throw new Error(
                            "Visible Workday Sign In control was not found."
                        );
                    }

                    button.click();
                    """)
                logger.info(
                    "JavaScript Sign In click %s executed.",
                    attempt,
                )

        except TimeoutException:
            logger.warning(
                "Sign In control was not found on attempt %s.",
                attempt,
            )
        except WebDriverException:
            logger.exception("Sign In click attempt %s failed.", attempt)

        # Check for the expected page for up to one second after each click.
        try:
            wait_for_heading(
                driver,
                RESUME_HEADING_SELECTOR,
                "Autofill with Resume",
                timeout=SIGN_IN_RETRY_INTERVAL,
            )
            logger.info(
                "Sign-in flow reached Autofill with Resume after " "attempt %s of %s.",
                attempt,
                SIGN_IN_MAX_ATTEMPTS,
            )
            return True
        except TimeoutException:
            logger.info(
                "Autofill with Resume was not detected after attempt %s.",
                attempt,
            )

        # Space attempts one second apart. Do not sleep after the final click.
        if attempt < SIGN_IN_MAX_ATTEMPTS:
            logger.info(
                "Waiting %s second before the next Sign In attempt.",
                SIGN_IN_RETRY_INTERVAL,
            )
            time.sleep(SIGN_IN_RETRY_INTERVAL)

    # The page may take longer to load after the final click.
    logger.info(
        "All %s click attempts completed. Waiting up to %s more seconds "
        "for Autofill with Resume before stopping.",
        SIGN_IN_MAX_ATTEMPTS,
        SIGN_IN_FINAL_WAIT,
    )

    try:
        wait_for_heading(
            driver,
            RESUME_HEADING_SELECTOR,
            "Autofill with Resume",
            timeout=SIGN_IN_FINAL_WAIT,
        )
        logger.info("Autofill with Resume appeared after the final click.")
        return True
    except TimeoutException:
        log_sign_in_diagnostics(driver)
        return False


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


def inspect_current_page(driver):
    """Print a read-only inventory of the current page's form controls."""
    logger.info("=" * 72)
    logger.info("READ-ONLY WORKDAY PAGE INSPECTION")
    logger.info("Current URL: %s", driver.current_url)

    try:
        headings = [
            heading.text.strip()
            for heading in driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3, h4")
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

        logger.info(
            "Visible form controls found: %s",
            len([control for control in controls if control.is_displayed()]),
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
                        for option in control.find_elements(By.TAG_NAME, "option")
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
            lambda current_driver: not is_autofill_resume_visible(current_driver)
        )
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

    # Read-only inspection of the next page.
    inspect_current_page(driver)

    logger.info(
        "Stopping at the next page for further detection. "
        "The job application has NOT been submitted automatically."
    )
    return True


def run(driver):
    """Run Workday account setup, sign-in, resume upload, and inspection."""
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
                'The visible heading h3#authViewTitle with text "Sign In" '
                "did not appear within %s seconds.",
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

        # Step 9: Retry JavaScript Sign In clicks until the resume page appears.
        sign_in_success = click_sign_in_until_autofill_appears(driver)

        if not sign_in_success:
            logger.error(
                "Could not confirm the Autofill with Resume page after "
                "%s Sign In attempts. Stopping for manual review.",
                SIGN_IN_MAX_ATTEMPTS,
            )
            return

        # Step 10: Locate the PDF to upload.
        resume_path = (
            project_root / "resume" / "Dhaarshan_Preshanthan_Resume.pdf"
        ).resolve()

        if not resume_path.is_file():
            logger.error("Resume PDF not found: %s", resume_path)
            return

        logger.info("Resume PDF exists and is ready to upload.")

        # Step 11 onward: upload, click Continue, and inspect the next page.
        upload_resume(driver, wait, resume_path)

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
    except TimeoutException:
        logger.error(
            "Timed out waiting for a Workday control or field. " "Current URL: %s",
            driver.current_url,
        )
    except WebDriverException:
        logger.exception("A Selenium error occurred in the Workday handler.")
