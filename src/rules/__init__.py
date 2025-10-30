"""Alert evaluation rules and business logic."""

from .spread import SpreadAlert, SpreadConfig, SpreadEngine, VenuePair

__all__ = ["SpreadAlert", "SpreadConfig", "SpreadEngine", "VenuePair"]
"""Alert evaluation rules and business logic.

This package will house the arbitrage math, filtering, and debounce logic.
"""
