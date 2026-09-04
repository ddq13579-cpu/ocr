from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    @abstractmethod
    def extract(self, raw_text: str, fields: list[dict[str, Any]]) -> dict[str, Any]:
        """Extract strictly the requested fields from plain document text."""
