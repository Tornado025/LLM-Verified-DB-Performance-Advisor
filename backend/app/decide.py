"""Select the cheapest candidate supported by race, equivalence and guard evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from app.config import Settings
from app.errors import BudgetExceeded
from app.guard import run_guard
from app.models import Candidate, Decision, RaceResult
from app.sandbox import InvalidDDL


def _guard_seed(shape, candidate: Candidate) -> int:
    """Derive a stable workload seed from the shape/candidate identity."""
    key = f"{getattr(shape, 'id', shape)}:{candidate.id}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big")


def decide(
    survivors: list[Candidate],
    race: RaceResult,
    shape,
    settings: Settings,
    *,
    progress: Callable[[dict], None] | None = None,
) -> Decision:
    """Prefer a tested rewrite, otherwise the first guarded index, else block."""
    ranked = [
        candidate
        for candidate in survivors
        if candidate.id not in race.dropped
        and (race_summary := race.states.get(candidate.id)) is not None
        and race_summary.state == "PASS"
    ]
    ranked.sort(key=lambda candidate: race.states[candidate.id].upper)
    if not ranked:
        return Decision("block", None, "no candidate improves the report")

    def summary_dict(candidate: Candidate) -> dict:
        return race.states[candidate.id].to_dict()

    best = ranked[0]
    rewrites = [
        candidate
        for candidate in ranked
        if candidate.kind == "rewrite"
        and (candidate.equiv or {}).get("label") == "tested"
        and summary_dict(candidate)["state"] == "PASS"
    ]
    rewrite = rewrites[0] if rewrites else None
    if rewrite is not None:
        best_change = float(summary_dict(best)["mean_rel"])
        rewrite_change = float(summary_dict(rewrite)["mean_rel"])
        if (
            best_change < 0
            and rewrite_change < 0
            and -rewrite_change + 1e-12 >= settings.prefer_ratio * -best_change
        ):
            weak = bool((rewrite.equiv or {}).get("weak", False))
            rewrite.status = "selected"
            reason = (
                f"verified rewrite within {settings.prefer_ratio:.2f} of the best option; "
                "zero write cost"
            )
            return Decision("apply_rewrite", rewrite.id, reason, weak)

    guard_attempts = 0
    for candidate in ranked:
        if candidate.kind not in {"index", "stats"}:
            continue
        if guard_attempts >= settings.max_guard_candidates:
            candidate.reason = "max_guard_candidates_reached"
            continue
        guard_attempts += 1
        if progress is not None:
            progress({"candidate_id": candidate.id, "stage": "guard", "attempt": guard_attempts})
        try:
            result = run_guard(
                candidate,
                settings.guard_profiles,
                settings,
                seed=_guard_seed(shape, candidate),
                progress=progress,
            )
        except BudgetExceeded:
            raise
        except InvalidDDL as exc:
            candidate.status, candidate.reason = "rejected_invalid_ddl", str(exc)
            continue
        except Exception as exc:
            # A failed measurement cannot support applying any database change.
            candidate.status = "blocked_unproven"
            candidate.reason = f"guard_error:{type(exc).__name__}"
            return Decision(
                "block", None, f"guard could not complete for {candidate.id}: {type(exc).__name__}"
            )
        candidate.guard = result.to_dict()
        if result.passed:
            candidate.status = "selected"
            profiles = ",".join(settings.guard_profiles)
            return Decision(
                "apply_index",
                candidate.id,
                f"passed non-inferiority under {profiles}",
            )
        if result.failed_cell is not None:
            candidate.status = "failed_guard"
            candidate.reason = f"failed at {result.failed_cell[0]}/{result.failed_cell[1]}"
        else:
            candidate.status = "blocked_unproven"
            candidate.reason = "guard evidence was inconclusive"

    if rewrite is not None:
        weak = bool((rewrite.equiv or {}).get("weak", False))
        rewrite.status = "selected"
        return Decision(
            "apply_rewrite",
            rewrite.id,
            "no index was proven safe; verified rewrite is the best safe option",
            weak,
        )

    reasons = [
        f"{candidate.id}: {candidate.reason or candidate.status}"
        for candidate in ranked
        if candidate.kind in {"index", "stats"}
    ]
    reason = "no candidate proven safe within budget"
    if reasons:
        reason += " (" + "; ".join(reasons) + ")"
    return Decision("block", None, reason)
