"""Main entry point for HiringCafe application preparation."""

import logging

from application_flow import main as run_application_flow

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    run_application_flow()
