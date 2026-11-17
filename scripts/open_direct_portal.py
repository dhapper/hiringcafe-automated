"""Open an application portal directly and dispatch to its handler.

Skips HiringCafe and opens the supplied application portal URL directly.

Lever uses a regular Chrome session launched with remote debugging,
then attaches Selenium to that session.

Keeps Chrome open for manual review until the user presses Enter.
Never submits an application.
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
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

from portals import lever
from portals.workday import handler as workday_handler
from portals import dayforcehcm, greenhouse, bamboo, ashbyhq, careerpuck

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PORTAL_URL = (
    "https://jobs.lever.co/PocketHealth/72a1603f-2fa5-4242-9250-9573f7e1070c/"
)

PORTAL_KEYWORDS = (
    "lever",
    "workday",
    "myworkdayjobs",
    "dayforcehcm",
    "ashbyhq",
    "workforcenow",
    "greenhouse",
    "bamboo",
    "careerpuck",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Open a direct application portal URL."
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_PORTAL_URL,
        help="Direct application portal URL to open.",
    )

    parser.add_argument(
        "--wait-for-review",
        action="store_true",
        help="Kept for compatibility with application_flow.py.",
    )

    return parser.parse_args()


def is_lever_url(url: str) -> bool:
    """Check whether the URL belongs to Lever."""

    hostname = (urlparse(url).hostname or "").lower()

    return hostname == "lever.co" or hostname.endswith(".lever.co")


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
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome Beta/Application/chrome.exe",
    ]

    # Check common installation locations first.
    for path in possible_paths:
        if path.is_file():
            logger.info("Found Chrome: %s", path)
            return str(path)

    # Check Windows registry for Chrome's registered executable.
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
                            "Found Chrome through Windows registry: %s", chrome_path
                        )
                        return chrome_path

            except (FileNotFoundError, OSError):
                continue

    # Final fallback: check PATH.
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
                logger.info("Chrome remote debugging is ready on port %s.", port)
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
        logger.exception("Could not attach Selenium to the Lever Chrome session.")
        raise

    # Keep a reference to the process for the lifetime of this workflow.
    driver._lever_chrome_process = chrome_process

    return driver


def create_driver(portal_url: str):
    """Create the appropriate browser session for the portal."""

    if is_lever_url(portal_url):
        logger.info(
            "Lever detected. Using a regular Chrome session with remote debugging."
        )
        return create_lever_driver()

    logger.info("Using the standard Selenium Chrome session.")
    options = Options()
    return webdriver.Chrome(options=options)


def dispatch_portal(driver, url: str) -> bool:
    """Run the implemented handler matching the application portal URL."""

    normalized_url = url.lower()

    if "lever" in normalized_url:
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

    for keyword in PORTAL_KEYWORDS:
        if keyword in normalized_url:
            logger.info("Detected application portal: %s", keyword)
            logger.info(
                "No automated handler is implemented for this portal yet. "
                "Leaving the page open for manual review."
            )
            return False

    logger.warning("Unrecognized application portal: %s", url)
    logger.info("Leaving the page open for manual review.")
    return False


def main():
    args = parse_arguments()
    portal_url = args.url.strip()

    if not portal_url.startswith(("https://", "http://")):
        raise ValueError("The supplied portal URL must begin with http:// or https://.")

    logger.info("Opening direct application portal: %s", portal_url)

    driver = create_driver(portal_url)

    try:
        driver.get(portal_url)

        wait = WebDriverWait(driver, 30)

        wait.until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )

        time.sleep(3)

        logger.info(
            "Page loaded. Title: %s | URL: %s",
            driver.title,
            driver.current_url,
        )

        dispatch_portal(driver, driver.current_url)

        logger.info(
            "Browser is ready for manual review. "
            "The application will NOT be submitted automatically."
        )

        ready_file = PROJECT_ROOT / "data" / "browser_review_ready.txt"
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text("ready", encoding="utf-8")

        logger.info("Chrome will remain open until you press Enter here.")
        input("Press Enter when you are finished reviewing the application...")

    except WebDriverException:
        logger.exception("Browser workflow failed.")
        raise

    # Intentionally do not call driver.quit().
    # Chrome is left open for manual review.


if __name__ == "__main__":
    main()
