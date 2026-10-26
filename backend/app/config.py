"""Cached, validated environment configuration."""

import os
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from math import ceil

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())


def boolean(value: str) -> bool:
    """Parse an explicit environment boolean, rejecting typos."""
    if value.lower() not in {"1", "0", "true", "false"}:
        raise ValueError("Expected boolean 1/0/true/false")
    return value.lower() in {"1", "true"}


@dataclass(frozen=True)
class Settings:
    """All contract settings; environment values are read at construction."""

    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "advisor"))
    pg_password: str = field(
        default_factory=lambda: os.getenv("PG_PASSWORD", "advisor"), repr=False
    )
    golden_db: str = field(default_factory=lambda: os.getenv("GOLDEN_DB", "qc_golden"))
    live_db: str = field(default_factory=lambda: os.getenv("LIVE_DB", "qc_live"))
    maint_db: str = field(default_factory=lambda: os.getenv("MAINT_DB", "postgres"))
    sandbox_prefix: str = field(default_factory=lambda: os.getenv("SANDBOX_PREFIX", "qc_sbx_"))
    meta_db_path: str = field(
        default_factory=lambda: os.getenv("META_DB_PATH", "./data/meta.sqlite")
    )
    delta: float = field(default_factory=lambda: float(os.getenv("DELTA", "0.05")))
    alpha: float = field(default_factory=lambda: float(os.getenv("ALPHA", "0.05")))
    min_runs: int = field(default_factory=lambda: int(os.getenv("MIN_RUNS", "6")))
    max_runs: int = field(default_factory=lambda: int(os.getenv("MAX_RUNS", "60")))
    race_batch: int = field(default_factory=lambda: int(os.getenv("RACE_BATCH", "3")))
    prefer_ratio: float = field(default_factory=lambda: float(os.getenv("PREFER_RATIO", "0.8")))
    metric: str = field(default_factory=lambda: os.getenv("METRIC", "time"))
    k_max: int = field(default_factory=lambda: int(os.getenv("K_MAX", "10")))
    patience: int = field(default_factory=lambda: int(os.getenv("PATIENCE", "3")))
    base_rate_tps: int = field(default_factory=lambda: int(os.getenv("BASE_RATE_TPS", "15")))
    workers: int = field(default_factory=lambda: int(os.getenv("WORKERS", "16")))
    block_seconds: int = field(default_factory=lambda: int(os.getenv("BLOCK_SECONDS", "8")))
    max_blocks: int = field(default_factory=lambda: int(os.getenv("MAX_BLOCKS", "40")))
    min_samples_per_block: int = field(
        default_factory=lambda: int(os.getenv("MIN_SAMPLES_PER_BLOCK", "5"))
    )
    guard_profiles: list[str] = field(
        default_factory=lambda: [
            s.strip()
            for s in os.getenv("GUARD_PROFILES", "normal,flash5,flash10,flash20").split(",")
            if s.strip()
        ]
    )
    stmt_timeout_ms: int = field(default_factory=lambda: int(os.getenv("STMT_TIMEOUT_MS", "60000")))
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "mock"))
    openrouter_api_key: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""), repr=False
    )
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", ""))
    openrouter_base_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    )
    openrouter_temperature: float = field(
        default_factory=lambda: float(os.getenv("OPENROUTER_TEMPERATURE", "0.7"))
    )
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434")
    )
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", ""))
    llm_timeout_s: int = field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT_S", "120")))
    max_clones: int = field(default_factory=lambda: int(os.getenv("MAX_CLONES", "4")))
    max_guard_candidates: int = field(
        default_factory=lambda: int(os.getenv("MAX_GUARD_CANDIDATES", "3"))
    )
    guard_stat: str = field(default_factory=lambda: os.getenv("GUARD_STAT", "mean"))
    openrouter_json_mode: bool = field(
        default_factory=lambda: boolean(os.getenv("OPENROUTER_JSON_MODE", "1"))
    )
    monitor_interval_s: int = field(
        default_factory=lambda: int(os.getenv("MONITOR_INTERVAL_S", "60"))
    )
    monitor_min_calls: int = field(
        default_factory=lambda: int(os.getenv("MONITOR_MIN_CALLS", "30"))
    )
    monitor_consecutive: int = field(
        default_factory=lambda: int(os.getenv("MONITOR_CONSECUTIVE", "2"))
    )
    min_age_s: int = field(default_factory=lambda: int(os.getenv("MIN_AGE_S", "120")))
    baseline_seconds: int = field(default_factory=lambda: int(os.getenv("BASELINE_SECONDS", "60")))
    apply_timeout_s: int = field(default_factory=lambda: int(os.getenv("APPLY_TIMEOUT_S", "1800")))
    free_disk_gb: float | None = field(
        default_factory=lambda: float(v) if (v := os.getenv("FREE_DISK_GB")) else None
    )
    benchbase_jar: str = field(default_factory=lambda: os.getenv("BENCHBASE_JAR", ""))
    api_host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            s.strip()
            for s in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
            if s.strip()
        ]
    )

    def __post_init__(self) -> None:
        if not (0 < self.delta < 1 and 0 < self.alpha < 0.5):
            raise ValueError("Invalid delta or alpha")
        if self.min_runs < 3 or self.max_runs < self.min_runs:
            raise ValueError("Invalid run limits")
        if min(self.k_max, self.patience, self.race_batch) < 1:
            raise ValueError("Sampling and batch limits must be positive")
        if self.llm_provider not in {"mock", "openrouter", "ollama"}:
            raise ValueError("Unknown LLM provider")
        if self.llm_provider == "openrouter" and not (
            self.openrouter_api_key.strip() and self.openrouter_model.strip()
        ):
            raise ValueError("OpenRouter key and model required")
        if self.llm_provider == "ollama" and not self.ollama_model.strip():
            raise ValueError("Ollama model required")
        if self.metric not in {"time", "buffers"}:
            raise ValueError("Unknown metric")
        if not self.guard_profiles or not set(self.guard_profiles) <= {
            "normal",
            "flash5",
            "flash10",
            "flash20",
        }:
            raise ValueError("Unknown or empty guard profiles")
        if self.guard_stat not in {"mean", "p95"}:
            raise ValueError("Unknown guard statistic")
        if self.stmt_timeout_ms <= 0:
            raise ValueError("Statement timeout must be positive")

    @property
    def looks(self) -> int:
        """Number of scheduled sequential looks."""
        return ceil((self.max_runs - self.min_runs) / self.race_batch) + 1

    def public(self) -> dict:
        """Return configuration with credentials masked."""
        result = asdict(self)
        for key in ("pg_password", "openrouter_api_key"):
            result[key] = "***" if result[key] else ""
        return result


@lru_cache
def get_settings() -> Settings:
    """Return the process configuration; cache_clear supports isolated tests."""
    return Settings()
