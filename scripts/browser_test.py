"""Open a HiringCafe job and dispatch to its application portal.

Keeps Chrome open for manual review until application_flow.py signals
that the user has selected an outcome. Never submits an application.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from portals import lever, workday

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TEST_URL = (
    "https://hiringcafe.com/job/"
    "qa-automation-tester-flynn-group-of-companies-mississauga-ontario-"
    "0emj38xyd6tl6ksh"
    "?utm_source=claude&utm_medium=llm-app&utm_campaign=mcp"
)

APPLY_SELECTOR = '[data-testid="job-page-apply"]'

PORTAL_KEYWORDS = (
    "lever",
    "workday",
    "ashbyhq",
    "workforcenow",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Open a HiringCafe job and detect its application portal."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_TEST_URL,
        help="HiringCafe job URL to open.",
    )
    parser.add_argument(
        "--wait-for-review",
        action="store_true",
        help="Keep Chrome open until application_flow.py signals closure.",
    )
    return parser.parse_args()


def dispatch_portal(driver, url: str) -> bool:
    """Run implemented handlers only; never launch placeholder scripts."""
    normalized_url = url.lower()

    if "lever" in normalized_url:
        logger.info("Detected Lever. Using the existing Chrome session.")
        lever.run(driver)
        return True

    if "workday" in normalized_url or "myworkdayjobs.com" in normalized_url:
        logger.info("Detected Workday. Starting handler.")
        workday.run(driver)
        return True

    for keyword in PORTAL_KEYWORDS:
        if keyword in normalized_url:
            logger.info("Detected application portal: %s", keyword)
            logger.info(
                "No automated handler is implemented for this portal yet. "
                "Leave the page open for manual review."
            )
            return False

    logger.warning("Unrecognized application portal: %s", url)
    logger.info("Leave the open browser page available for manual review.")
    return False


def main():
    args = parse_arguments()
    job_url = args.url.strip()

    if not job_url.startswith(("https://", "http://")):
        raise ValueError("The supplied job URL must begin with http:// or https://.")

    logger.info("Opening HiringCafe job: %s", job_url)

    # Do not enable detach. We want Selenium to close Chrome when finished.
    options = Options()
    driver = webdriver.Chrome(options=options)
    workflow_completed = False

    try:
        driver.get(job_url)
        wait = WebDriverWait(driver, 30)

        wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        logger.info("Waiting for the visible HiringCafe Apply button...")

        def find_apply_button(d):
            for button in d.find_elements(By.CSS_SELECTOR, APPLY_SELECTOR):
                try:
                    if button.is_displayed() and button.is_enabled():
                        return button
                except WebDriverException:
                    continue
            return False

        apply_button = wait.until(find_apply_button)

        original_url = driver.current_url
        original_handles = set(driver.window_handles)

        logger.info("Clicking HiringCafe Apply button: %r", apply_button.text)
        apply_button.click()

        try:
            WebDriverWait(driver, 15).until(
                lambda d: (
                    len(set(d.window_handles) - original_handles) > 0
                    or d.current_url != original_url
                )
            )
        except TimeoutException:
            logger.warning("No new tab or URL change detected within 15 seconds.")

        time.sleep(3)

        portal_handle = None
        portal_url = None

        for index, handle in enumerate(driver.window_handles, start=1):
            try:
                driver.switch_to.window(handle)
                tab_url = driver.current_url

                logger.info(
                    "Tab %d: title=%r URL=%s",
                    index,
                    driver.title,
                    tab_url,
                )

                if any(keyword in tab_url.lower() for keyword in PORTAL_KEYWORDS):
                    portal_handle = handle
                    portal_url = tab_url

            except WebDriverException:
                logger.warning("Could not inspect tab %d.", index)

        if portal_handle is None or portal_url is None:
            logger.warning("No recognized application portal was detected.")
            logger.info("Chrome will remain available for manual review.")
        else:
            driver.switch_to.window(portal_handle)
            logger.info("Selected portal URL: %s", portal_url)
            dispatch_portal(driver, portal_url)

        logger.info(
            "Browser is ready for manual review. "
            "The application will NOT be submitted automatically."
        )
        workflow_completed = True

        ready_file = PROJECT_ROOT / "data" / "browser_review_ready.txt"
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text("ready", encoding="utf-8")

        if args.wait_for_review:
            logger.info(
                "Waiting for application_flow.py to finish the outcome prompt "
                "before closing Chrome."
            )
            # application_flow.py sends this line after the user chooses
            # an outcome. The child receives it through its piped stdin.
            sys.stdin.readline()

    except (TimeoutException, WebDriverException):
        logger.exception("Browser workflow failed.")
        raise

    finally:
        if args.wait_for_review and not workflow_completed:
            logger.error(
                "Browser workflow did not complete successfully; " "closing Chrome."
            )

        try:
            driver.quit()
            logger.info("Chrome closed.")
        except WebDriverException:
            logger.warning("Chrome was already closed.")


if __name__ == "__main__":
    main()
