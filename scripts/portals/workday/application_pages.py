"""Workday application pages after My Information.

For every page: scrape the form with the common scraper, fill it with the
common filler, click Save and Continue, and wait for the next page.

Safety: Workday keeps the footer button's data-automation-id on the Review
page, where the same button reads "Submit". The click below therefore checks
the button text and only ever clicks Save and Continue / Continue / Next, so
this module can never submit the application.
"""

import logging
import time

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from common.fill_content import fill_form_content
from common.scrape_content import scrape_form_content

from .my_experience import HANDLED_ID_PREFIXES as EXPERIENCE_ID_PREFIXES
from .my_experience import fill_my_experience


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


logger = FileLoggerAdapter(
    logging.getLogger(__name__),
    {"source_file": "application_pages.py"},
)

FOOTER_BUTTON_SELECTOR = 'button[data-automation-id="pageFooterNextButton"]'
ALLOWED_BUTTON_TEXTS = {"save and continue", "continue", "next"}
HEADING_SELECTOR = "h1, h2, h3, h4, [role='heading']"
ERROR_SELECTORS = (
    '[role="alert"]',
    '[data-automation-id="errorMessage"]',
    '[data-automation-id*="errorBanner"]',
)

# Pages with their own handler: heading text (lower case) -> (handler, element-id
# prefixes the handler owns). The handler runs first and returns a list of
# problems. If it reports any, the loop stops; otherwise the generic
# scraper/filler handles the rest of the page, skipping those controls.
PAGE_HANDLERS = {
    "my experience": (fill_my_experience, EXPERIENCE_ID_PREFIXES),
}

# Do not click Save and Continue while a required field is still blank.
STOP_IF_REQUIRED_BLANK = True
MAX_APPLICATION_PAGES = 8
PAGE_RENDER_SECONDS = 3
PAGE_CHANGE_WAIT_SECONDS = 20
CLICK_ATTEMPTS = 3


def page_headings(driver):
    """Return the visible heading texts, used to tell one step from the next.

    The URL stays the same between Workday steps, so headings are the signal.
    """
    headings = []

    try:
        for element in driver.find_elements(By.CSS_SELECTOR, HEADING_SELECTOR):
            text = " ".join(element.text.split())

            if text and element.is_displayed():
                headings.append(text)
    except WebDriverException:
        pass

    return tuple(dict.fromkeys(headings))


def heading_visible(driver, text):
    wanted = text.casefold()
    return any(heading.casefold() == wanted for heading in page_headings(driver))


def visible_errors(driver):
    """Return visible validation/error messages."""
    errors = []

    for selector in ERROR_SELECTORS:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = " ".join(element.text.split())

                if text and element.is_displayed():
                    errors.append(text)
        except WebDriverException:
            continue

    return list(dict.fromkeys(errors))


def _footer_button(driver):
    try:
        for element in driver.find_elements(By.CSS_SELECTOR, FOOTER_BUTTON_SELECTOR):
            if element.is_displayed():
                return element
    except WebDriverException:
        pass

    return None


def click_save_and_continue(driver):
    """Click the footer button if it is Save and Continue.

    Returns "clicked", "final" (the button is something else, such as Submit),
    or "missing".
    """
    for attempt in range(1, CLICK_ATTEMPTS + 1):
        button = _footer_button(driver)

        if button is None:
            logger.warning("The footer button was not found.")
            return "missing"

        try:
            label = " ".join(button.text.split())

            if label.casefold() not in ALLOWED_BUTTON_TEXTS:
                logger.info(
                    "The footer button reads %r, not Save and Continue. "
                    "Treating this as the final page; it is left for you.",
                    label,
                )
                return "final"

            logger.info("Clicking %r.", label)
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                button,
            )
            time.sleep(0.3)
            button.click()
            logger.info("Clicked %r.", label)
            return "clicked"

        except (StaleElementReferenceException, ElementClickInterceptedException):
            logger.info(
                "The footer button changed while clicking; retrying (%s of %s).",
                attempt,
                CLICK_ATTEMPTS,
            )
            time.sleep(0.5)

    logger.warning("Could not click the footer button.")
    return "missing"


def wait_for_page_change(driver, headings_before):
    """Wait until a new heading appears and no errors are showing."""
    before = set(headings_before)

    def changed(current_driver):
        new_headings = set(page_headings(current_driver)) - before
        return bool(new_headings) and not visible_errors(current_driver)

    try:
        WebDriverWait(driver, PAGE_CHANGE_WAIT_SECONDS, poll_frequency=0.5).until(
            changed
        )
    except TimeoutException:
        logger.warning(
            "Still on the same page after Save and Continue. Visible errors: %s",
            visible_errors(driver) or "none detected",
        )
        return False

    logger.info(
        "Moved to the next page: %s",
        sorted(set(page_headings(driver)) - before),
    )
    return True


def advance_page(driver, headings_before):
    """Click Save and Continue and confirm the page changed."""
    if click_save_and_continue(driver) != "clicked":
        return False

    return wait_for_page_change(driver, headings_before)


def run_application_pages(driver, first_page_number=2):
    """Scrape, fill, and advance through every page until the final one."""
    for offset in range(MAX_APPLICATION_PAGES):
        page_number = first_page_number + offset

        logger.info("=== Workday application page %s ===", page_number)
        logger.info("Waiting %s seconds for the page to render.", PAGE_RENDER_SECONDS)
        time.sleep(PAGE_RENDER_SECONDS)

        headings_before = page_headings(driver)

        # A page with its own handler (e.g. My Experience) is filled by it first.
        custom_problems = []
        skip_prefixes = ()
        lowered_headings = {heading.casefold() for heading in headings_before}

        for heading, (handler, id_prefixes) in PAGE_HANDLERS.items():
            if heading in lowered_headings:
                logger.info("Using the dedicated handler for '%s'.", heading)
                custom_problems = list(handler(driver))
                skip_prefixes = tuple(id_prefixes)
                time.sleep(1)
                break

        # If the dedicated handler is not satisfied, do not let the generic
        # filler touch the page.
        if custom_problems and STOP_IF_REQUIRED_BLANK:
            logger.warning(
                "Stopping on page %s: the dedicated handler reported: %s",
                page_number,
                custom_problems,
            )
            return

        form_content = scrape_form_content(driver)

        if skip_prefixes:
            form_content["controls"] = [
                control
                for control in form_content.get("controls", [])
                if not (control.get("id") or "").startswith(skip_prefixes)
            ]

        logger.info(
            "Page %s scraped. Detected %s visible form controls.",
            page_number,
            len(form_content.get("controls", [])),
        )

        left_blank = custom_problems + fill_form_content(driver, form_content)

        if left_blank and STOP_IF_REQUIRED_BLANK:
            logger.warning(
                "Stopping on page %s: required fields are still blank: %s",
                page_number,
                left_blank,
            )
            return

        state = click_save_and_continue(driver)

        if state != "clicked":
            return

        if not wait_for_page_change(driver, headings_before):
            return

    logger.warning(
        "Reached the page limit (%s) without finding the final page.",
        MAX_APPLICATION_PAGES,
    )
