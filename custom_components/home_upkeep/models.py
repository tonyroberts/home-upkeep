"""Storage models for the Home Upkeep integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


@dataclass
class StoredTask:
    """A single to-do task."""

    id: int
    list_id: int
    title: str
    description: str | None
    completed: bool
    due_date: date | None
    reschedule_period: str | None
    reschedule_base: Literal["completed", "due"] | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    prohibited_months: list[int]
    constraints: list[str] = field(default_factory=list)

    def to_storage(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for HA's Store."""
        return {
            "id": self.id,
            "list_id": self.list_id,
            "title": self.title,
            "description": self.description,
            "completed": self.completed,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "reschedule_period": self.reschedule_period,
            "reschedule_base": self.reschedule_base,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "prohibited_months": self.prohibited_months,
            "constraints": self.constraints,
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> StoredTask:
        """Deserialize from a dict loaded from HA's Store."""
        return cls(
            id=data["id"],
            list_id=data["list_id"],
            title=data["title"],
            description=data["description"],
            completed=data["completed"],
            due_date=date.fromisoformat(data["due_date"]) if data["due_date"] else None,
            reschedule_period=data["reschedule_period"],
            reschedule_base=data["reschedule_base"],
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data["completed_at"]
            else None,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            prohibited_months=data["prohibited_months"],
            constraints=data.get("constraints", []),
        )


@dataclass
class StoredList:
    """A task list."""

    id: int
    name: str
    created_at: datetime
    updated_at: datetime

    def to_storage(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for HA's Store."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_storage(cls, data: dict[str, Any]) -> StoredList:
        """Deserialize from a dict loaded from HA's Store."""
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
