"""Workday sign-in helpers."""

import logging

from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "login.py"},
)


SIGN_IN_MAX_ATTEMPTS = 3
SIGN_IN_AFTER_CLICK_WAIT = 10  # how long to wait for the next page after a click
SIGN_IN_BUTTON_LOOKUP = 3  # short lookup for "autofill heading OR sign-in button"
SIGN_IN_FINAL_WAIT = 10

SIGN_IN_SELECTOR = (
    'div[data-automation-id="click_filter"]' '[role="button"][aria-label="Sign In"]'
)

RESUME_HEADING_SELECTOR = "h3"

# Workday saves application progress on the account. After signing in again, it
# skips the resume page and opens the step you stopped at. These page headings
# mean "signed in, and already past the resume page".
SAVED_PROGRESS_HEADINGS = {
    "my information",
    "my experience",
    "application questions",
    "voluntary disclosures",
    "self identify",
    "review",
}


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


def detect_landing_page(driver):
    """Return where a successful sign-in landed.

    "autofill"  - the Autofill with Resume page (first visit)
    "progress"  - a later step, because Workday saved earlier progress
    None        - neither is visible yet
    """
    if is_autofill_resume_visible(driver):
        return "autofill"

    try:
        for heading in driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3, h4"):
            if (
                heading.is_displayed()
                and heading.text.strip().casefold() in SAVED_PROGRESS_HEADINGS
            ):
                return "progress"

    except WebDriverException:
        logger.debug("Could not check the page headings yet.", exc_info=True)

    return None


def wait_for_landing(driver, timeout):
    """Wait for either landing page. Raises TimeoutException if neither appears."""
    return WebDriverWait(
        driver,
        timeout,
        poll_frequency=0.25,
    ).until(lambda current_driver: detect_landing_page(current_driver) or False)


def wait_for_autofill_or_sign_in(driver, timeout):
    """Return 'autofill', 'progress' or 'button', whichever appears first.

    Raises TimeoutException if none shows up within the timeout.
    """

    def state(current_driver):
        landing = detect_landing_page(current_driver)

        if landing:
            return landing

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
    """Retry Sign In until a signed-in page appears.

    Returns "autofill" (first visit), "progress" (Workday saved earlier
    progress and skipped the resume page), or False if sign-in never succeeded.
    """
    logger.info("Checking whether a signed-in page is already visible...")

    landing = detect_landing_page(driver)

    if landing:
        logger.info("Already signed in (%s page); no sign-in click needed.", landing)
        return landing

    for attempt in range(1, SIGN_IN_MAX_ATTEMPTS + 1):
        landing = detect_landing_page(driver)

        if landing:
            logger.info("Signed in (%s page) before attempt %s.", landing, attempt)
            return landing

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

        if state in ("autofill", "progress"):
            logger.info(
                "Signed in (%s page) before clicking on attempt %s.",
                state,
                attempt,
            )
            return state

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
            landing = wait_for_landing(driver, SIGN_IN_AFTER_CLICK_WAIT)

            logger.info(
                "Sign-in reached the %s page after attempt %s of %s.",
                landing,
                attempt,
                SIGN_IN_MAX_ATTEMPTS,
            )

            return landing

        except TimeoutException:
            logger.info(
                "No signed-in page was detected after attempt %s.",
                attempt,
            )

    logger.info(
        "All %s click attempts completed. Waiting up to %s more seconds "
        "for a signed-in page before stopping.",
        SIGN_IN_MAX_ATTEMPTS,
        SIGN_IN_FINAL_WAIT,
    )

    try:
        landing = wait_for_landing(driver, SIGN_IN_FINAL_WAIT)

        logger.info("The %s page appeared after the final click.", landing)
        return landing

    except TimeoutException:
        log_sign_in_diagnostics(driver)
        return False
