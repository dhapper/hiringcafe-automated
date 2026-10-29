"""Workday sign-in helpers."""

import logging

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .register import wait_for_heading


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "login.py"},
)


SIGN_IN_MAX_ATTEMPTS = 3
SIGN_IN_AFTER_CLICK_WAIT = 5  # how long to wait for the next page after a click
SIGN_IN_BUTTON_LOOKUP = 3  # short lookup for "autofill heading OR sign-in button"
SIGN_IN_FINAL_WAIT = 5

SIGN_IN_SELECTOR = (
    'div[data-automation-id="click_filter"]' '[role="button"][aria-label="Sign In"]'
)

RESUME_HEADING_SELECTOR = "h3"


def is_autofill_resume_visible(driver):
    """Return True if the Autofill with Resume heading is visible."""
    try:
        for heading in driver.find_elements(
            By.CSS_SELECTOR,
            RESUME_HEADING_SELECTOR,
        ):
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


def wait_for_autofill_or_sign_in(driver, timeout):
    """Return 'autofill' or 'button', whichever appears first.

    Raises TimeoutException if neither shows up within the timeout.
    """

    def state(current_driver):
        if is_autofill_resume_visible(current_driver):
            return "autofill"

        try:
            if current_driver.find_elements(By.CSS_SELECTOR, SIGN_IN_SELECTOR):
                return "button"
        except WebDriverException:
            logger.debug("Could not look up the Sign In control yet.", exc_info=True)

        return False

    return WebDriverWait(
        driver,
        timeout,
        poll_frequency=0.25,
    ).until(state)


def log_sign_in_diagnostics(driver):
    """Log useful page state if sign-in does not reach the expected page."""
    logger.warning("Sign-in retry attempts did not confirm the expected page.")
    logger.warning("Current Workday URL: %s", driver.current_url)

    try:
        headings = [
            heading.text.strip()
            for heading in driver.find_elements(
                By.CSS_SELECTOR,
                "h1, h2, h3",
            )
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
            for element in driver.find_elements(
                By.CSS_SELECTOR,
                selector,
            ):
                if element.is_displayed() and element.text.strip():
                    errors.append(element.text.strip())

        if errors:
            logger.warning("Visible sign-in errors: %s", errors[:10])
        else:
            logger.info("No visible sign-in error messages were detected.")

    except WebDriverException:
        logger.warning("Could not inspect sign-in error messages.")


def click_sign_in_until_autofill_appears(driver):
    """Retry Sign In until Autofill with Resume appears."""
    logger.info("Checking whether Autofill with Resume is already visible...")

    if is_autofill_resume_visible(driver):
        logger.info("Autofill with Resume is already visible; no sign-in click needed.")
        return True

    for attempt in range(1, SIGN_IN_MAX_ATTEMPTS + 1):
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

        # Wait for whichever appears first: the next page or the Sign In button.
        try:
            state = wait_for_autofill_or_sign_in(driver, SIGN_IN_BUTTON_LOOKUP)
        except TimeoutException:
            state = None  # mid-transition; fall through to the heading wait

        if state == "autofill":
            logger.info(
                "Autofill with Resume appeared before clicking on attempt %s.",
                attempt,
            )
            return True

        if state == "button":
            try:
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

            except WebDriverException:
                logger.exception(
                    "Sign In click attempt %s failed.",
                    attempt,
                )
        else:
            logger.info(
                "Neither Sign In nor Autofill was visible on attempt %s; "
                "the page may be mid-transition.",
                attempt,
            )

        # Give Workday realistic time to authenticate and render the next page
        # before deciding the click did not work.
        try:
            wait_for_heading(
                driver,
                RESUME_HEADING_SELECTOR,
                "Autofill with Resume",
                timeout=SIGN_IN_AFTER_CLICK_WAIT,
            )

            logger.info(
                "Sign-in flow reached Autofill with Resume after attempt %s of %s.",
                attempt,
                SIGN_IN_MAX_ATTEMPTS,
            )

            return True

        except TimeoutException:
            logger.info(
                "Autofill with Resume was not detected after attempt %s.",
                attempt,
            )

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
