"""Job-local cooperative cancellation for long measurements."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT: ContextVar[threading.Event | None] = ContextVar("job_cancel_event", default=None)


class JobCancelled(BaseException):
    """Stop a job without being mistaken for a candidate measurement error."""


@contextmanager
def cancellation(event: threading.Event) -> Iterator[None]:
    """Expose one job's stop event to synchronous measurement helpers."""
    token = _CURRENT.set(event)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_event() -> threading.Event | None:
    """Return the job's stop event, if running inside a pipeline job."""
    return _CURRENT.get()


def check_cancelled() -> None:
    """Raise an unambiguous cancellation signal at a safe boundary."""
    event = _CURRENT.get()
    if event is not None and event.is_set():
        raise JobCancelled()
