"""Workday account registration helpers."""

import logging
import re
from pathlib import Path

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "register.py"},
)


WAIT_SECONDS = 30

EMAIL_SELECTOR = 'input[data-automation-id="email"]'
PASSWORD_SELECTOR = 'input[data-automation-id="password"]'
VERIFY_PASSWORD_SELECTOR = 'input[data-automation-id="verifyPassword"]'
CHECKBOX_SELECTOR = 'input[data-automation-id="createAccountCheckbox"]'

CREATE_ACCOUNT_SELECTOR = (
    'div[data-automation-id="click_filter"]'
    '[role="button"][aria-label="Create Account"]'
)

APPLY_SELECTOR = 'a[data-automation-id="adventureButton"][role="button"]'
RESUME_SELECTOR = 'a[data-automation-id="autofillWithResume"][role="button"]'

AUTH_HEADING_SELECTOR = "h3#authViewTitle"


def read_account_info():
    """Read the email and password from resume/my_info.txt."""
    project_root = Path(__file__).resolve().parents[3]
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
    """Wait for a field, fill it, and verify that it is populated."""
    logger.info("Waiting for the Workday %s field...", field_name)

    field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

    field.clear()
    field.send_keys(value)

    if not field.get_attribute("value"):
        raise RuntimeError(
            f"The Workday {field_name} field appears to be empty " "after filling it."
        )

    logger.info("Filled the Workday %s field.", field_name)


def wait_for_heading(
    driver,
    selector,
    expected_text,
    timeout=WAIT_SECONDS,
):
    """Wait for a visible heading with the exact expected text."""

    def heading_visible(current_driver):
        for heading in current_driver.find_elements(
            By.CSS_SELECTOR,
            selector,
        ):
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

    heading = WebDriverWait(
        driver,
        timeout,
        poll_frequency=0.25,
    ).until(heading_visible)

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
                driver.execute_script(
                    "arguments[0].click();",
                    checkbox,
                )

        except WebDriverException:
            logger.warning("Normal checkbox click failed; trying JavaScript click.")

            driver.execute_script(
                "arguments[0].click();",
                checkbox,
            )

    def checkbox_is_checked(current_driver):
        element = current_driver.find_element(
            By.CSS_SELECTOR,
            CHECKBOX_SELECTOR,
        )

        return element if element.is_selected() else False

    wait.until(checkbox_is_checked)

    logger.info("Create Account checkbox is checked.")
