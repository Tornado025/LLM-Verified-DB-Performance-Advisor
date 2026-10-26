"""Shared data contracts for advisor modules."""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Self


class Model:
    """Serialize dataclasses without sharing mutable state."""

    def to_dict(self) -> dict[str, Any]:
        """Return an independent JSON-compatible mapping."""
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Restore a contract object from its serialized fields."""
        return cls(**data)


@dataclass(frozen=True)
class Bounds(Model):  # one-sided bounds on RELATIVE change (fractions)
    """Bounds data contract."""

    lower: float
    upper: float


@dataclass
class Summary(Model):  # paired-run summary
    """Summary data contract."""

    n: int
    mean_rel: float  # expm1(mean(d))
    lower: float
    upper: float
    sd_log: float  # std(d, ddof=1)
    state: str  # State


@dataclass
class RawCandidate(Model):  # what an LLM provider returns (untrusted)
    """RawCandidate data contract."""

    kind: str
    sql: str
    rationale: str


@dataclass
class Candidate(Model):
    """Candidate data contract."""

    id: str
    job_id: str
    shape_id: str
    kind: str  # CandidateKind
    sql: str
    rationale: str
    sample_idx: int
    status: str = "proposed"  # CandidateStatus
    reason: str | None = None
    signature: str | None = None  # plan fingerprint (index/rewrite) or normalised DDL (stats)
    plan_before: dict | None = None
    plan_after: dict | None = None
    equiv: dict | None = None  # EquivResult as dict
    speedup: dict | None = None  # Summary as dict + metric + n_runs
    guard: dict | None = None  # GuardResult as dict


@dataclass
class EquivResult(Model):
    """EquivResult data contract."""

    label: str  # EquivLabel
    rewrite_class: int | None  # 1, 2, 3
    tier_reached: int  # 0..3
    reason: str | None
    counterexample: dict | None  # {"tz": "...", "db": {...}, "orig_rows": [...], "new_rows": [...]}
    cx_dbs: int  # number of counterexample DBs run
    nonvacuous_fraction: float | None
    weak: bool


@dataclass
class Decision(Model):
    """Decision data contract."""

    action: str  # DecisionAction
    candidate_id: str | None
    reason: str


@dataclass
class ProfileSpec(Model):
    """ProfileSpec data contract."""

    name: str  # ProfileName
    rate_mult: float  # 1, 5, 10, 20
    zipf_s: float  # product skew exponent
    hot_set_size: int  # number of "hot SKUs"
    hot_share: float  # fraction of order lines hitting the hot set
    status_poll_mult: float  # relative rate of order_status reads
    dashboard_refresh_s: float  # background report refresh period
    pre_sale_load: bool  # bulk catalogue load before the block
    analyze_after_load: bool


@dataclass
class Arrival(Model):
    """Arrival data contract."""

    t: float
    txn: str
    params: dict


@dataclass
class BlockResult(Model):
    """BlockResult data contract."""

    seconds: float
    latencies: dict[str, list[float]]
    errors: dict[str, int]
    dropped: int
    metadata: dict = field(default_factory=dict)


@dataclass
class PairedResult(Model):
    """PairedResult data contract."""

    d: list[float]
    summary: Summary
    seconds: float
    metric: str
    a_vals: list[float]
    b_vals: list[float]


@dataclass
class ClonePair(Model):
    """ClonePair data contract."""

    a: str
    b: str


@dataclass
class GuardResult(Model):
    """GuardResult data contract."""

    passed: bool
    profiles: dict
    seconds: float
    blocks: dict
    failed_cell: tuple | None


@dataclass
class RaceResult(Model):
    """RaceResult data contract."""

    states: dict[str, Summary]
    dropped: dict[str, str]
    seconds: float
    runs: int


@dataclass
class ProposeResult(Model):
    """ProposeResult data contract."""

    kept: list
    samples: int
    saturated: bool
    usage: dict
    rejected: list
    distinct_plan_curve: list[int] = field(default_factory=list)


CandidateKind = Literal["index", "stats", "rewrite"]
CandidateStatus = Literal[
    "proposed",
    "rejected_safety",
    "rejected_invalid_ddl",
    "skipped_duplicate",
    "skipped_noplan",
    "rejected_equiv",
    "dropped_race",
    "failed_guard",
    "blocked_unproven",
    "selected",
    "approved",
    "applied",
    "reverted",
    "blocked_by_human",
]
State = Literal["CONTINUE", "PASS", "FAIL", "BLOCK"]
Metric = Literal["time", "buffers"]
ProfileName = Literal["normal", "flash5", "flash10", "flash20"]
TxnType = Literal[
    "place_order", "stock_decrement", "update_status", "order_status", "picker_queue_read"
]
Step = Literal[
    "system",
    "observe",
    "propose",
    "filter",
    "equivalence",
    "race",
    "guard",
    "decide",
    "apply",
    "monitor",
]
Level = Literal["info", "warn", "error", "result"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
AppliedState = Literal["healthy", "watching", "reverted", "revert_failed"]
DecisionAction = Literal["apply_rewrite", "apply_index", "block"]
EquivLabel = Literal["tested", "rejected", "unknown"]
