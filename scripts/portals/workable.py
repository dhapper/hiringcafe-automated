"""Placeholder Workable application portal handler.

The handler will be expanded once the Workable form structure is reviewed.
It does not fill fields or submit applications.
"""

import logging

logger = logging.getLogger(__name__)


def run(driver):
    """Inspect the Workable page and leave it open for manual review."""

    logger.info("=" * 65)
    logger.info("Workable handler started.")
    logger.info("Current URL: %s", driver.current_url)

    try:
        logger.info("Page title: %s", driver.title)
    except Exception:
        logger.warning("Could not retrieve the Workable page title.")

    logger.info(
        "Workable handler is currently a placeholder. "
        "No fields were filled and no application was submitted."
    )
    logger.info("Review the Workable page in Chrome.")
    logger.info("=" * 65)
