"""Engine package: gates, models, rr_core, verdict, and RREngine orchestrator."""

from rr_engine.engine.models import (
    EvaluateRequest,
    MoneyGeometry,
    OptionGeometry,
    Provenance,
    ReasonItem,
    RREvaluateResponse,
    SetupDetails,
    SpotGeometry,
    StatusResponse,
)
from rr_engine.engine.rr_core import RRParams, compute_rr, scenario_ladder
from rr_engine.engine.rr_engine import RREngine
from rr_engine.engine.verdict import aggregate_verdict

__all__ = [
    "EvaluateRequest",
    "MoneyGeometry",
    "OptionGeometry",
    "Provenance",
    "RREngine",
    "RREvaluateResponse",
    "RRParams",
    "ReasonItem",
    "SetupDetails",
    "SpotGeometry",
    "StatusResponse",
    "aggregate_verdict",
    "compute_rr",
    "scenario_ladder",
]
