"""Alert evaluation rules and business logic."""

from .hedged import (
    HedgedPair,
    HedgedSpreadAlert,
    HedgedSpreadConfig,
    HedgedSpreadEngine,
)
from .spread import SpreadAlert, SpreadConfig, SpreadEngine, VenuePair

__all__ = [
    "HedgedPair",
    "HedgedSpreadAlert",
    "HedgedSpreadConfig",
    "HedgedSpreadEngine",
    "SpreadAlert",
    "SpreadConfig",
    "SpreadEngine",
    "VenuePair",
]
from .spread import SpreadAlert, SpreadConfig, SpreadEngine, VenuePair

__all__ = ["SpreadAlert", "SpreadConfig", "SpreadEngine", "VenuePair"]
"""Alert evaluation rules and business logic.

This package will house the arbitrage math, filtering, and debounce logic.
"""
