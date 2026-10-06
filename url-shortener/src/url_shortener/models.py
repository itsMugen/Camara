"""Domain model."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Link:
    code: str
    url: str
    key: str
    created_at: datetime
    expires_at: datetime

    def is_active(self, now: datetime) -> bool:
        """A link is valid strictly before ``expires_at``."""
        return now < self.expires_at
