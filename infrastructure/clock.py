from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """現在時刻取得を差し替えるための抽象。"""

    def now(self) -> datetime:
        """現在時刻を返す。

        Args:
            なし。

        Returns:
            現在時刻。
        """


class RealClock:
    """本番実行で利用する時計。"""

    def now(self) -> datetime:
        """UTCの現在時刻を返す。

        Args:
            なし。

        Returns:
            UTC現在時刻。
        """

        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MockClock:
    """テストやバックテストで固定時刻を返す時計。"""

    fixed_datetime: datetime

    def now(self) -> datetime:
        """固定時刻を返す。

        Args:
            なし。

        Returns:
            固定時刻。
        """

        return self.fixed_datetime


SystemClock = RealClock
FixedClock = MockClock
