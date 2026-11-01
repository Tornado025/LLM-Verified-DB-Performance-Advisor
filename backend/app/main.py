"""FastAPI lifecycle, serial clone jobs, monitoring and structured errors."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException

from app.api import api_router
from app.config import Settings, get_settings
from app.errors import AdvisorError
from app.logging_conf import setup_logging
from app.monitor import MonitorThread
from app.pipeline import run_job
from app.sandbox import drop_all_clones
from app.store import Store

_LOG = logging.getLogger(__name__)


class JobRunner:
    """Keep clone-using work serial while a second executor task waits queued."""

    def __init__(self, store: Store):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="advisor-job")
        self.semaphore = threading.Semaphore(1)
        self.closing = threading.Event()

    def submit(self, params: dict) -> str:
        """Persist before submitting so queued work is visible immediately."""
        if self.closing.is_set():
            from app.errors import Conflict

            raise Conflict("Server is shutting down")
        job_id = self.store.create_job(params)
        self.executor.submit(self._run, job_id, params)
        return job_id

    def _run(self, job_id: str, params: dict) -> None:
        with self.semaphore:
            if self.closing.is_set() or self.is_cancelled(job_id):
                self.store.set_job_status(job_id, "cancelled")
                return
            try:
                run_job(job_id, params, self.store)
            except Exception:
                _LOG.exception("Background job failed")
                self.store.emit(job_id, "system", "error", "Job runner failed")
                self.store.set_job_status(job_id, "failed")

    def cancel(self, job_id: str) -> None:
        """Leave completed jobs intact; mark other jobs cancelled."""
        if self.store.get_job(job_id)["status"] in {"queued", "running"}:
            self.store.set_job_status(job_id, "cancelled")

    def is_cancelled(self, job_id: str) -> bool:
        """Read persistent cancellation state."""
        return self.store.is_cancelled(job_id)

    def close(self) -> None:
        """Cancel pending work and wait for job-owned cleanup before closing the store."""
        self.closing.set()
        for job in self.store.list_jobs(100000):
            self.cancel(job["id"])
        self.executor.shutdown(wait=True)


def create_app(
    *, settings: Settings | None = None, store: Store | None = None, manage_database: bool = True
) -> FastAPI:
    """Create the service; injectable resources keep unit tests network-free."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        s = settings or get_settings()
        metadata = store or Store(s.meta_db_path)
        app.state.settings, app.state.store = s, metadata
        app.state.traffic, app.state.traffic_started = None, None
        app.state.traffic_lock = threading.Lock()
        for job in metadata.list_jobs(100000):
            if job["status"] in {"running", "queued"}:
                metadata.emit(job["id"], "system", "error", "server restarted")
                metadata.set_job_status(job["id"], "failed")
        if manage_database:
            try:
                drop_all_clones()
            except Exception:
                _LOG.exception("Startup orphan cleanup failed")
        app.state.runner = JobRunner(metadata)
        monitor = MonitorThread(
            metadata, s.monitor_interval_s, s.monitor_min_calls, s.monitor_consecutive
        )
        if manage_database:
            monitor.start()
        try:
            yield
        finally:
            monitor.stop()
            if monitor.ident:
                monitor.join(s.apply_timeout_s + 10)
            if app.state.traffic:
                app.state.traffic.stop()
                app.state.traffic.join(s.stmt_timeout_ms / 1000 + 10)
            app.state.runner.close()
            if manage_database:
                drop_all_clones()
            if store is None:
                metadata.close()

    application = FastAPI(title="DB Performance Advisor", version="0.15.0", lifespan=lifespan)
    s = settings or get_settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Last-Event-ID"],
    )
    application.include_router(api_router, prefix="/api")

    @application.get("/", include_in_schema=False)
    async def api_home():
        return RedirectResponse(url="/docs")

    @application.exception_handler(AdvisorError)
    async def advisor_error(request: Request, exc: AdvisorError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc), "detail": exc.detail}},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {"loc": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request",
                    "detail": {"errors": errors},
                }
            },
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "not_found" if exc.status_code == 404 else "http_error",
                    "message": str(exc.detail),
                    "detail": {},
                }
            },
        )

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        _LOG.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal", "message": "Internal server error", "detail": {}}
            },
        )

    return application


app = create_app()
