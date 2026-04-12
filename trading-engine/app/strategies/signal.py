from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class BaseSignal:
    signal: SignalType
