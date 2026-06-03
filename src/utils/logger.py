import logging
import os
import textwrap
from logging.handlers import RotatingFileHandler


LOG_DIR = "logs"
LOG_FILE = "app.log"
LOG_PATH = os.path.join(LOG_DIR, LOG_FILE)
WRAP_WIDTH = 110
FIELD_INDENT = "  "
CONTINUATION_INDENT = "    "

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)


RESERVED_FIELDS = set(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__.keys()
)
RESERVED_FIELDS.update(
    {
        "asctime",
        "created",
        "details",
        "exc_info",
        "exc_text",
        "message",
        "msg",
        "msecs",
        "relativeCreated",
        "stack_info",
    }
)


def _safe_string(value) -> str:
    try:
        return str(value)
    except Exception as exc:
        return f"<unprintable {type(value).__name__}: {exc}>"


def _format_field(key: str, value) -> list[str]:
    value_text = _safe_string(value)
    prefix = f"{FIELD_INDENT}{key}: "
    wrapped = textwrap.wrap(
        value_text,
        width=WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=CONTINUATION_INDENT,
        replace_whitespace=False,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapped or [prefix.rstrip()]


class MultiLineFormatter(logging.Formatter):
    def format(self, record):
        timestamp = self.formatTime(record, self.datefmt)
        header = f"{timestamp} | {record.levelname:<7} | {record.name}"
        lines = [header]

        event = getattr(record, "event", "-") or "-"
        lines.extend(_format_field("event", event))
        lines.extend(_format_field("msg", record.getMessage()))

        extra_keys = sorted(
            key
            for key in record.__dict__
            if key not in RESERVED_FIELDS and key != "event"
        )
        for key in extra_keys:
            lines.extend(_format_field(key, record.__dict__[key]))

        if record.exc_info:
            lines.append(f"{FIELD_INDENT}traceback:")
            lines.extend(
                f"{CONTINUATION_INDENT}{line}"
                for line in self.formatException(record.exc_info).splitlines()
            )

        if record.stack_info:
            lines.append(f"{FIELD_INDENT}stack:")
            lines.extend(
                f"{CONTINUATION_INDENT}{line}"
                for line in self.formatStack(record.stack_info).splitlines()
            )

        return "\n".join(lines) + "\n"


def _log(logger: logging.Logger, level: int, message: str, event: str = "-", **fields):
    exc_info = fields.pop("exc_info", None)
    stack_info = fields.pop("stack_info", False)
    logger.log(
        level,
        message,
        extra={"event": event, **fields},
        exc_info=exc_info,
        stack_info=stack_info,
    )


def log_debug(logger: logging.Logger, message: str, event: str = "-", **fields):
    _log(logger, logging.DEBUG, message, event, **fields)


def log_info(logger: logging.Logger, message: str, event: str = "-", **fields):
    _log(logger, logging.INFO, message, event, **fields)


def log_warning(logger: logging.Logger, message: str, event: str = "-", **fields):
    _log(logger, logging.WARNING, message, event, **fields)


def log_error(logger: logging.Logger, message: str, event: str = "-", **fields):
    _log(logger, logging.ERROR, message, event, **fields)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = MultiLineFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
