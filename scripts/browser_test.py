"""Test HiringCafe Apply and dispatch to the detected application portal."""

import logging
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# Import the Lever handler from the sibling portals folder.
from portals import lever

TEST_URL = (
    "https://hiringcafe.com/job/qa-automation-tester-flynn-group-of-companies-mississauga-ontario-0emj38xyd6tl6ksh?utm_source=claude&utm_medium=llm-app&utm_campaign=mcp"
    # "https://hiringcafe.com/job/software-engineer-ii-emburse-toronto-ontario-0b1cq9k7t9jmfd5i?utm_source=claude&utm_medium=llm-app&utm_campaign=mcp"
)

APPLY_SELECTOR = '[data-testid="job-page-apply"]'
PORTALS_DIR = Path(__file__).resolve().parent / "portals"

# Lever is integrated directly into this browser session.
# Other portal scripts retain their existing standalone behavior for now.
PORTAL_SCRIPTS = {
    "workday": "workday.py",
    "ashbyhq": "ashbyhq.py",
    "workforcenow": "workforcenow.py",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def dispatch_portal(driver, url: str) -> bool:
    """Dispatch to the detected portal handler."""
    normalized_url = url.lower()

    if "lever" in normalized_url:
        logger.info("Detected portal 'lever'. Using existing Chrome session.")
        lever.run(driver)
        return True

    for keyword, script_name in PORTAL_SCRIPTS.items():
        if keyword in normalized_url:
            script_path = PORTALS_DIR / script_name

            if not script_path.is_file():
                logger.error("Portal script not found: %s", script_path)
                return False

            logger.info(
                "Detected portal '%s'. Launching %s",
                keyword,
                script_path.name,
            )
            subprocess.Popen([sys.executable, str(script_path)])
            return True

    logger.warning("Unknown application portal URL: %s", url)
    return False


def main() -> None:
    """Open HiringCafe, click Apply, and run the detected portal handler."""
    logger.info("Opening test URL: %s", TEST_URL)

    options = Options()
    options.add_experimental_option("detach", True)

    driver = webdriver.Chrome(options=options)

    try:
        driver.get(TEST_URL)

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

        logger.info("Clicking visible button: %r", apply_button.text)
        apply_button.click()

        logger.info("Waiting for the application site to open...")

        try:
            WebDriverWait(driver, 15).until(
                lambda d: (
                    len(d.window_handles) > len(original_handles)
                    or d.current_url != original_url
                )
            )
        except TimeoutException:
            logger.warning("No new tab or URL change detected within 15 seconds.")

        logger.info("Waiting 5 seconds for the application site to load...")
        time.sleep(5)

        logger.info("Open tabs: %d", len(driver.window_handles))

        portal_handle = None
        portal_url = None

        for index, handle in enumerate(driver.window_handles, start=1):
            try:
                driver.switch_to.window(handle)
                tab_url = driver.current_url
                tab_title = driver.title

                logger.info(
                    "Tab %d: title=%r URL=%s",
                    index,
                    tab_title,
                    tab_url,
                )

                if any(
                    keyword in tab_url.lower()
                    for keyword in (
                        "lever",
                        "workday",
                        "ashbyhq",
                        "workforcenow",
                    )
                ):
                    portal_handle = handle
                    portal_url = tab_url

            except WebDriverException:
                logger.warning("Could not inspect tab %d.", index)

        if portal_handle is None or portal_url is None:
            logger.error("Could not find a recognized application portal tab.")
            return

        # Select the actual portal tab before calling its handler.
        driver.switch_to.window(portal_handle)

        logger.info("Selected application tab: %s", driver.current_url)
        dispatch_portal(driver, portal_url)

        logger.info(
            "Test complete. No application details were filled in or submitted."
        )

    except (TimeoutException, WebDriverException):
        logger.exception("Browser test failed.")

    finally:
        # Do not call driver.quit(): Chrome should remain open for inspection.
        logger.info("Leaving Chrome open for manual inspection.")


if __name__ == "__main__":
    main()
