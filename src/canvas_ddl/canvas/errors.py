"""Stable, credential-free application errors."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ApplicationError(Exception):
    code: str
    message: str
    candidates: tuple[Any, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        return self.message

