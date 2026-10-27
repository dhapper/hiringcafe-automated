"""Workday application portal handler.

Step 1: Click Apply if still on the job description page.
Step 2: Click Autofill with Resume.
Step 3: Read email and password from resume/my_info.txt.
Step 4: Fill email, password, and verify-password fields.

Does not create the account or submit the application automatically.
"""

import logging
import re
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

logger = logging.getLogger(__name__)


def read_account_info():
    """Read the email and password from resume/my_info.txt."""
    project_root = Path(__file__).resolve().parents[2]
    info_path = project_root / "resume" / "my_info.txt"

    if not info_path.exists():
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

    email = email_match.group(1).strip()
    password = password_match.group(1)

    return email, password


def fill_field(wait, selector, value, field_name):
    """Wait for a field, fill it, and verify it is populated."""
    logger.info("Waiting for the Workday %s field...", field_name)

    field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))

    field.clear()
    field.send_keys(value)

    # Check only whether the field is populated. Never log its contents.
    if not field.get_attribute("value"):
        raise RuntimeError(
            f"The Workday {field_name} field appears to be empty " "after filling it."
        )

    logger.info("Filled the Workday %s field.", field_name)


def run(driver):
    """Navigate initial Workday steps and fill account fields."""
    wait = WebDriverWait(driver, 20)

    try:
        # Step 1: Click Apply if still on the job description page.
        if "/apply" not in driver.current_url.lower():
            apply_selector = 'a[data-automation-id="adventureButton"][role="button"]'

            logger.info("Waiting for the Workday Apply button...")
            apply_button = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, apply_selector))
            )

            logger.info("Clicking Apply.")
            apply_button.click()

        # Step 2: Click Autofill with Resume.
        resume_selector = 'a[data-automation-id="autofillWithResume"][role="button"]'

        logger.info("Waiting for Autofill with Resume...")
        resume_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, resume_selector))
        )

        logger.info("Found button: %r", resume_button.text)
        resume_button.click()

        logger.info("Clicked Autofill with Resume.")
        logger.info("Current Workday URL: %s", driver.current_url)

        # Step 3: Read account information.
        email, password = read_account_info()
        logger.info("Loaded account email and password from my_info.txt.")

        # Step 4: Fill email.
        fill_field(
            wait,
            'input[data-automation-id="email"]',
            email,
            "email",
        )

        # Step 5: Fill password.
        fill_field(
            wait,
            'input[data-automation-id="password"]',
            password,
            "password",
        )

        # Step 6: Enter the same password in the confirmation field.
        fill_field(
            wait,
            'input[data-automation-id="verifyPassword"]',
            password,
            "password confirmation",
        )

        logger.info("Email, password, and password confirmation are populated.")
        logger.info(
            "Paused for manual review. No account-creation or application "
            "submission button was clicked."
        )
        logger.info("Current Workday URL: %s", driver.current_url)

        # Step 7: Find and check the Create Account checkbox.
        checkbox_selector = 'input[data-automation-id="createAccountCheckbox"]'

        logger.info("Waiting for the Create Account checkbox element...")
        checkbox = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, checkbox_selector))
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
                    # Use the page's normal checkbox event handling.
                    driver.execute_script("arguments[0].click();", checkbox)
            except WebDriverException:
                logger.warning("Normal checkbox click failed; trying JavaScript click.")
                driver.execute_script("arguments[0].click();", checkbox)

        # Confirm the checkbox state after the click.
        checkbox = driver.find_element(By.CSS_SELECTOR, checkbox_selector)

        if checkbox.is_selected():
            logger.info("Create Account checkbox is checked.")
        else:
            logger.error(
                "Checkbox was found but is still unchecked. "
                "Not clicking Create Account."
            )
            return

        import time

        time.sleep(1)

        # Step 8: Inspect the Create Account button and its form.
        logger.info("Inspecting the Create Account button and form...")

        diagnostic = driver.execute_script("""
            const button = document.querySelector(
                'button[data-automation-id="createAccountSubmitButton"]'
            );

            if (!button) {
                return { error: "Create Account button not found" };
            }

            const form = button.closest("form");

            function describe(el) {
                if (!el) return null;

                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);

                return {
                    tag: el.tagName,
                    type: el.type || null,
                    text: (el.innerText || el.value || "").trim(),
                    automationId: el.getAttribute("data-automation-id"),
                    ariaHidden: el.getAttribute("aria-hidden"),
                    disabled: !!el.disabled,
                    display: style.display,
                    visibility: style.visibility,
                    pointerEvents: style.pointerEvents,
                    width: rect.width,
                    height: rect.height,
                    className: String(el.className || "").slice(0, 200)
                };
            }

            return {
                button: describe(button),
                form: form ? {
                    action: form.action,
                    method: form.method,
                    noValidate: form.noValidate,
                    submitButtons: Array.from(
                        form.querySelectorAll(
                            'button[type="submit"], input[type="submit"]'
                        )
                    ).map(describe),
                    controls: Array.from(form.elements).map(el => ({
                        tag: el.tagName,
                        type: el.type || null,
                        automationId: el.getAttribute("data-automation-id"),
                        name: el.name || null,
                        disabled: !!el.disabled
                    }))
                } : null,
                matchingButtons: Array.from(
                    document.querySelectorAll(
                        'button[data-automation-id="createAccountSubmitButton"]'
                    )
                ).map(describe)
            };
        """)

        logger.info(
            "Create Account inspection:\\n%s",
            __import__("json").dumps(diagnostic, indent=2),
        )

        logger.info("Inspection complete. Nothing was submitted.")

        # Allow the page a moment to settle before returning to the controller.
        logger.info("Waiting 2 seconds before returning to the review workflow.")
        time.sleep(2)

        logger.info("Workday handler finished. Returning control to browser_test.py.")

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)

    except TimeoutException:
        logger.error(
            "Timed out waiting for a Workday button or account field. "
            "Current URL: %s",
            driver.current_url,
        )

    except WebDriverException:
        logger.exception("A Selenium error occurred in the Workday handler.")
