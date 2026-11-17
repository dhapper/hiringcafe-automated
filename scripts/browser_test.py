"""Open a HiringCafe job and dispatch to its application portal.

Uses the standard Selenium Chrome session for HiringCafe and most portals.

For Lever, launches a separate regular Chrome session with remote debugging
and attaches Selenium to that session.

Keeps Chrome open for manual review until application_flow.py signals
that the user has selected an outcome. Never submits an application.
"""

import argparse
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from portals import lever
from portals.workday import handler as workday_handler
from portals import dayforcehcm, greenhouse, bamboo, ashbyhq, careerpuck, workable

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
    "dayforcehcm",
    "ashbyhq",
    "workforcenow",
    "greenhouse",
    "bamboo",
    "careerpuck",
    "workable",
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


def is_lever_url(url: str) -> bool:
    """Check whether a URL belongs to Lever."""

    hostname = (urlparse(url).hostname or "").lower()

    return hostname == "lever.co" or hostname.endswith(".lever.co")


def is_workable_url(url: str) -> bool:
    """Check whether a URL belongs to Workable."""

    hostname = (urlparse(url).hostname or "").lower()

    return hostname == "workable.com" or hostname.endswith(".workable.com")


def find_chrome_executable() -> str:
    """Find Google Chrome using common paths and Windows registry."""

    import winreg

    possible_paths = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]

    for path in possible_paths:
        if path.is_file():
            logger.info("Found Chrome: %s", path)
            return str(path)

    registry_locations = [
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ),
    ]

    for root, key_path in registry_locations:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    root,
                    key_path,
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    chrome_path, _ = winreg.QueryValueEx(key, None)

                    if Path(chrome_path).is_file():
                        logger.info(
                            "Found Chrome through Windows registry: %s",
                            chrome_path,
                        )
                        return chrome_path

            except (FileNotFoundError, OSError):
                continue

    chrome_from_path = (
        shutil.which("chrome.exe")
        or shutil.which("chrome")
        or shutil.which("google-chrome")
    )

    if chrome_from_path:
        logger.info("Found Chrome through PATH: %s", chrome_from_path)
        return chrome_from_path

    raise FileNotFoundError(
        "Could not locate chrome.exe. Check that Google Chrome is installed."
    )


def get_available_port() -> int:
    """Find an available local port for Chrome remote debugging."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_debugger(port: int, timeout: int = 30):
    """Wait until Chrome's remote debugging endpoint is available."""

    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", port),
                timeout=1,
            ):
                logger.info(
                    "Chrome remote debugging is ready on port %s.",
                    port,
                )
                return

        except OSError:
            time.sleep(0.5)

    raise TimeoutError("Chrome did not start its remote debugging endpoint in time.")


def create_lever_driver():
    """Launch regular Chrome and attach Selenium to it for Lever."""

    chrome_path = find_chrome_executable()

    profile_path = PROJECT_ROOT / "data" / "lever_chrome_profile"
    profile_path.mkdir(parents=True, exist_ok=True)

    port = get_available_port()

    logger.info("Launching regular Chrome for Lever.")
    logger.info("Using Chrome profile: %s", profile_path)

    chrome_process = subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_debugger(port)
    except Exception:
        logger.exception("Could not start Chrome for Lever.")
        raise

    options = Options()
    options.add_experimental_option(
        "debuggerAddress",
        f"127.0.0.1:{port}",
    )

    logger.info("Attaching Selenium to the Lever Chrome session.")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        logger.exception("Could not attach Selenium to Lever Chrome.")
        raise

    # Keep the Chrome process referenced for the driver's lifetime.
    driver._lever_chrome_process = chrome_process

    return driver


def create_workable_driver():
    """Launch regular Chrome and attach Selenium to it for Workable."""

    chrome_path = find_chrome_executable()

    profile_path = PROJECT_ROOT / "data" / "workable_chrome_profile"
    profile_path.mkdir(parents=True, exist_ok=True)

    port = get_available_port()

    logger.info("Launching regular Chrome for Workable.")
    logger.info("Using Chrome profile: %s", profile_path)

    chrome_process = subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_path}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_debugger(port)
    except Exception:
        logger.exception("Could not start Chrome for Workable.")
        raise

    options = Options()
    options.add_experimental_option(
        "debuggerAddress",
        f"127.0.0.1:{port}",
    )

    logger.info("Attaching Selenium to the Workable Chrome session.")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        logger.exception("Could not attach Selenium to Workable Chrome.")
        raise

    # Keep the Chrome process referenced for the driver's lifetime.
    driver._workable_chrome_process = chrome_process

    return driver


def dispatch_portal(driver, url: str) -> bool:
    """Run implemented handlers only; never launch placeholder scripts."""

    normalized_url = url.lower()

    if is_lever_url(normalized_url):
        logger.info("Detected Lever. Starting handler.")
        lever.run(driver)
        return True

    if "workday" in normalized_url or "myworkdayjobs.com" in normalized_url:
        logger.info("Detected Workday. Starting handler.")
        workday_handler.run(driver)
        return True

    if "dayforcehcm" in normalized_url:
        logger.info("Detected DayForce HCM. Starting handler.")
        dayforcehcm.run(driver)
        return True

    if "greenhouse" in normalized_url:
        logger.info("Detected Greenhouse. Starting handler.")
        greenhouse.run(driver)
        return True

    if "bamboo" in normalized_url:
        logger.info("Detected Bamboo. Starting handler.")
        bamboo.run(driver)
        return True

    if "ashbyhq" in normalized_url:
        logger.info("Detected AshbyHQ. Starting handler.")
        ashbyhq.run(driver)
        return True

    if "careerpuck" in normalized_url:
        logger.info("Detected CareerPuck. Starting handler.")
        careerpuck.run(driver)
        return True

    if is_workable_url(normalized_url):
        logger.info("Detected Workable. Starting handler.")
        workable.run(driver)
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

    # Start with the normal Selenium browser for HiringCafe.
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
            logger.info("Selected portal URL: %s", portal_url)

            if is_lever_url(portal_url):
                logger.info("Lever detected. Switching to the regular Chrome session.")

                # Launch a separate regular Chrome session for Lever.
                lever_driver = create_lever_driver()

                try:
                    lever_driver.get(portal_url)

                    WebDriverWait(lever_driver, 30).until(
                        lambda d: (
                            d.execute_script("return document.readyState") == "complete"
                        )
                    )

                    time.sleep(3)

                except Exception:
                    try:
                        lever_driver.quit()
                    except WebDriverException:
                        pass
                    raise

                # The HiringCafe browser is no longer needed.
                try:
                    driver.quit()
                except WebDriverException:
                    logger.warning(
                        "The original HiringCafe browser was already closed."
                    )

                # Continue the workflow using the Lever browser.
                driver = lever_driver
            elif is_workable_url(portal_url):
                logger.info(
                    "Workable detected. Switching to the regular Chrome session."
                )

                # Launch a separate regular Chrome session for Workable.
                workable_driver = create_workable_driver()

                try:
                    workable_driver.get(portal_url)

                    WebDriverWait(workable_driver, 30).until(
                        lambda d: (
                            d.execute_script("return document.readyState") == "complete"
                        )
                    )

                    time.sleep(3)

                except Exception:
                    try:
                        workable_driver.quit()
                    except WebDriverException:
                        pass
                    raise

                # The HiringCafe browser is no longer needed.
                try:
                    driver.quit()
                except WebDriverException:
                    logger.warning(
                        "The original HiringCafe browser was already closed."
                    )

                # Continue the workflow using the Workable browser.
                driver = workable_driver
            else:
                driver.switch_to.window(portal_handle)

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
                "Browser workflow did not complete successfully; closing Chrome."
            )

        try:
            driver.quit()
            logger.info("Chrome closed.")
        except WebDriverException:
            logger.warning("Chrome was already closed.")


if __name__ == "__main__":
    main()
