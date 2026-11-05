"""Logging helper shared by every module in common/."""

import logging


class FileLoggerAdapter(logging.LoggerAdapter):
    """Add the source filename to every log message."""

    def process(self, msg, kwargs):
        return f"{self.extra['source_file']} | {msg}", kwargs


def get_logger(source_file):
    """Return a logger whose messages are prefixed with `source_file | `."""
    return FileLoggerAdapter(
        logging.getLogger(f"common.{source_file}"),
        {"source_file": source_file},
    )
