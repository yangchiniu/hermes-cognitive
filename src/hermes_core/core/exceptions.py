"""
Hermes Core — Unified Exception Hierarchy.

All exceptions raised by Hermes Core subsystems inherit from HermesCoreError,
enabling clean catch-and-handle patterns at the top-level supervisor loop.
"""

from typing import Any, Optional


class HermesCoreError(Exception):
    """Base exception for all Hermes Core errors."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        self.context = context or {}
        super().__init__(message)

    @property
    def msg(self) -> str:
        """Return the human-readable message."""
        return self.args[0] if self.args else ""


class WorldStateError(HermesCoreError):
    """Raised when the world model encounters an inconsistency or failure."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class PolicyViolation(HermesCoreError):
    """Raised when a policy check fails (safety, alignment, guardrails)."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class TaskExecutionError(HermesCoreError):
    """Raised when a task graph node fails during execution."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class ResourceLimitExceeded(HermesCoreError):
    """Raised by the runtime supervisor when a resource budget is exhausted."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class ToolNotFoundError(HermesCoreError):
    """Raised when a requested tool is not registered in the tool registry."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class ReflectionError(HermesCoreError):
    """Raised when the reflection subsystem fails to introspect or self-correct."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class RecoveryError(HermesCoreError):
    """Raised when a recovery action from a prior failure itself fails."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class HermesMemoryError(HermesCoreError):
    """Raised by the memory manager on storage, retrieval, or consolidation failures."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


class EventLogError(HermesCoreError):
    """Raised when the event log / audit trail subsystem encounters a problem."""

    def __init__(self, message: str = "", context: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message, context)


# Convenience registry: map exception class names to classes for dynamic lookup.
_EXCEPTION_REGISTRY: dict[str, type[HermesCoreError]] = {
    "HermesCoreError": HermesCoreError,
    "WorldStateError": WorldStateError,
    "PolicyViolation": PolicyViolation,
    "TaskExecutionError": TaskExecutionError,
    "ResourceLimitExceeded": ResourceLimitExceeded,
    "ToolNotFoundError": ToolNotFoundError,
    "ReflectionError": ReflectionError,
    "RecoveryError": RecoveryError,
    "HermesMemoryError": HermesMemoryError,
    "EventLogError": EventLogError,
}


def exception_from_name(name: str, message: str = "", context: Optional[dict[str, Any]] = None) -> HermesCoreError:
    """Look up an exception class by name and instantiate it.

    Useful for cross-process / serialization scenarios where the exception
    type is carried as a string.
    """
    cls = _EXCEPTION_REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown exception name: {name!r} — valid options are {set(_EXCEPTION_REGISTRY)}")
    return cls(message, context)
