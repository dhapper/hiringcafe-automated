
"""Test the visible HiringCafe Apply button without submitting an application."""

import csv
import json
import logging
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOBS_JSON = PROJECT_ROOT / "data" / "jobs.json"
APPLICATIONS_CSV = PROJECT_ROOT / "data" / "applications.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

APPLY_SELECTOR = '[data-testid="job-page-apply"]'


def select_first_pending_job() -> dict:
    with JOBS_JSON.open("r", encoding="utf-8") as file:
        jobs = json.load(file).get("jobs", [])

    with APPLICATIONS_CSV.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        statuses = {
            row["job_id"]: row.get("status", "").strip().lower()
            for row in csv.DictReader(file)
            if row.get("job_id")
        }

    for job in jobs:
        if (
            statuses.get(job.get("job_id", "")) == "pending"
            and job.get("job_url")
        ):
            return job

    raise RuntimeError("No pending jobs with valid URLs found.")


def main() -> None:
    job = select_first_pending_job()
    logger.info("Opening: %s at %s", job.get("title"), job.get("company"))

    options = Options()
    options.add_experimental_option("detach", True)
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(job["job_url"])

        wait = WebDriverWait(driver, 30)
        wait.until(
            lambda d: d.execute_script("return document.readyState")
            == "complete"
        )

        # Wait until at least one matching button is actually visible.
        wait.until(
            lambda d: any(
                button.is_displayed() and button.is_enabled()
                for button in d.find_elements(
                    By.CSS_SELECTOR, APPLY_SELECTOR
                )
            )
        )

        visible_buttons = [
            button
            for button in driver.find_elements(
                By.CSS_SELECTOR, APPLY_SELECTOR
            )
            if button.is_displayed() and button.is_enabled()
        ]

        if not visible_buttons:
            raise RuntimeError("No visible, enabled Apply button found.")

        apply_button = visible_buttons[0]
        logger.info("Clicking visible button: %r", apply_button.text)
        original_url = driver.current_url

        apply_button.click()

        # Clicking may open a new tab or navigate the current one.
        try:
            WebDriverWait(driver, 10).until(
                lambda d: (
                    len(d.window_handles) > 1
                    or d.current_url != original_url
                )
            )
        except TimeoutException:
            logger.warning(
                "No new tab or URL change detected within 10 seconds."
            )

        logger.info("Current URL: %s", driver.current_url)
        logger.info("Open tabs: %d", len(driver.window_handles))

        for index, handle in enumerate(driver.window_handles, start=1):
            driver.switch_to.window(handle)
            logger.info(
                "Tab %d: title=%r URL=%s",
                index,
                driver.title,
                driver.current_url,
            )

        logger.info(
            "Click test complete. Inspect the open browser manually. "
            "No application form was filled or submitted."
        )

    except (TimeoutException, WebDriverException, RuntimeError):
        logger.exception("Apply-button test failed.")
        raise

    finally:
        # Leave Chrome open for manual inspection.
        logger.info("Leaving Chrome open.")


if __name__ == "__main__":
    main()
