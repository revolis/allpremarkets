"""Shared data models and enums used across the project."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Enumeration of normalised ingest event types."""

    BOOK = "book"
    TRADE = "trade"
    LISTING = "listing"
    ORDER = "order"
    FILL = "fill"


class MarketEvent(BaseModel):
    """Canonical representation for market data emitted by ingestors."""

    token: str = Field(..., description="Human readable token identifier, e.g. TNSR")
    venue: Literal[
        "MEXC",
        "WHALES",
    ] = Field(..., description="Source venue identifier")
    instrument: str = Field(..., description="Venue specific symbol, e.g. TNSR_USDT")
    event_type: EventType = Field(..., description="Type of update received from venue")
    best_bid: Optional[float] = Field(
        None, description="Best bid price if known for this update"
    )
    best_ask: Optional[float] = Field(
        None, description="Best ask price if known for this update"
    )
    last_price: Optional[float] = Field(
        None, description="Last traded price where provided by venue"
    )
    size: Optional[float] = Field(None, description="Size associated with the event")
    notional: Optional[float] = Field(
        None, description="Price multiplied by size for quick filtering"
    )
    timestamp_ms: int = Field(..., description="Event timestamp in milliseconds")
    listing_info: Optional[dict[str, Any]] = Field(
        None, description="Listing specific metadata when event_type=listing"
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Original payload for debugging"
    )


__all__ = ["EventType", "MarketEvent"]
