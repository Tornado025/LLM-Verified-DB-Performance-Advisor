"""Central logging with rendered-message credential redaction."""

import logging
import os

from app.pg import mask_dsn


class SecretFilter(logging.Filter):
    """Mask credentials after formatting arguments and exception traces."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize message, arguments and exception text before handlers emit."""

        def redact(value: str) -> str:
            for name in ("OPENROUTER_API_KEY", "PG_PASSWORD"):
                if secret := os.getenv(name):
                    value = value.replace(secret, "***")
            return mask_dsn(value)

        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure process handlers; repeated setup does not duplicate output."""
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if not any(isinstance(f, SecretFilter) for f in handler.filters):
            handler.addFilter(SecretFilter())
