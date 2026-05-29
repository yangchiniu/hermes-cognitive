"""
Hermes Core — Core runtime abstractions for the Hermes agent framework.

This package provides the foundation for world modelling, task graphs,
tool registration, policy enforcement, reflection, recovery, memory,
and event logging.

Usage:
    from hermes.core import HermesCoreError, WorldStateError, ToolNotFoundError
    from hermes.core import get_kernel, initialize
"""

from __future__ import annotations

import typing as _t

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Re-export all exception classes
# ---------------------------------------------------------------------------
from .exceptions import (
    HermesCoreError,
    WorldStateError,
    PolicyViolation,
    TaskExecutionError,
    ResourceLimitExceeded,
    ToolNotFoundError,
    ReflectionError,
    RecoveryError,
    HermesMemoryError as MemoryErrorBase,
    EventLogError,
    exception_from_name,
)

# ---------------------------------------------------------------------------
# Lazy-imported submodule proxies
# ---------------------------------------------------------------------------


class _LazyModule:
    """Descriptor that imports a submodule on first access."""

    def __init__(self, module_path: str, name: str | None = None) -> None:
        self._path = module_path
        self._name = name
        self._mod: _t.Any = None

    def _load(self) -> _t.Any:
        if self._mod is None:
            import importlib as _ilib

            self._mod = _ilib.import_module(self._path)
        return self._mod

    def __get__(self, obj: _t.Any, objtype: type | None = None) -> _t.Any:
        return self._load()

    def __getattr__(self, name: str) -> _t.Any:
        return getattr(self._load(), name)


# Lazy references — the underlying modules are loaded only when first accessed.
world: _t.Any = _LazyModule("hermes.core.world_model")
task_graph: _t.Any = _LazyModule("hermes.core.task_graph")
tool_registry: _t.Any = _LazyModule("hermes.core.tool_registry")
policy: _t.Any = _LazyModule("hermes.core.policy_engine")
reflection: _t.Any = _LazyModule("hermes.core.reflection_engine")
recovery: _t.Any = _LazyModule("hermes.core.recovery_manager")
memory: _t.Any = _LazyModule("hermes.core.memory_manager")
event_log: _t.Any = _LazyModule("hermes.core.event_logger")
supervisor: _t.Any = _LazyModule("hermes.core.runtime_supervisor")
experience: _t.Any = _LazyModule("hermes.core.experience_manager")
state: _t.Any = _LazyModule("hermes.core.state_manager")
self_observation: _t.Any = _LazyModule("hermes.core.self_observation")
kernel: _t.Any = _LazyModule("hermes.core.kernel")
db_schema: _t.Any = _LazyModule("hermes.core.db_schema")
drift: _t.Any = _LazyModule("hermes.core.drift_analyzer")
goal: _t.Any = _LazyModule("hermes.core.goal_manager")
telemetry: _t.Any = _LazyModule("hermes.core.telemetry")
watchdog: _t.Any = _LazyModule("hermes.core.watchdog")
semantic_retrieval: _t.Any = _LazyModule("hermes.core.semantic_retrieval")
telemetry_replay: _t.Any = _LazyModule("hermes.core.telemetry_replay")

# ---------------------------------------------------------------------------
# Convenience re-exports: pull key module-level functions to the top
# ---------------------------------------------------------------------------

# These are resolved at access time via the lazy references above.

def get_world_model(*args, **kwargs):
    """Shorthand: return the world model singleton."""
    return world.get_world_model(*args, **kwargs)


def get_tool_registry(*args, **kwargs):
    """Shorthand: return the tool registry singleton."""
    return tool_registry.get_registry(*args, **kwargs)


def get_policy_engine(*args, **kwargs):
    """Shorthand: return the policy engine singleton."""
    return policy.get_policy_engine(*args, **kwargs)


def get_kernel_singleton(*args, **kwargs) -> "AgentKernel":
    """Shorthand: return the agent kernel singleton."""
    return kernel.get_kernel(*args, **kwargs)


def get_supervisor_singleton(*args, **kwargs):
    """Shorthand: return the runtime supervisor singleton."""
    return supervisor.get_supervisor(*args, **kwargs)


def get_memory_manager_singleton(*args, **kwargs):
    """Shorthand: return the memory manager singleton."""
    return memory.get_memory_manager(*args, **kwargs)


def get_observer_singleton(*args, **kwargs):
    """Shorthand: return the self-observation loop singleton."""
    return self_observation.get_observer(*args, **kwargs)


def get_drift_analyzer(*args, **kwargs):
    """Shorthand: return the drift analyzer singleton."""
    return drift.get_drift_analyzer(*args, **kwargs)


def get_goal_manager_singleton(*args, **kwargs):
    """Shorthand: return the goal manager singleton."""
    return goal.get_goal_manager(*args, **kwargs)


def get_telemetry_singleton(*args, **kwargs):
    """Shorthand: return the telemetry singleton."""
    return telemetry.get_telemetry(*args, **kwargs)


def get_watchdog_singleton(*args, **kwargs):
    """Shorthand: return the watchdog singleton."""
    return watchdog.get_watchdog(*args, **kwargs)


def core_initialize() -> dict:
    """Initialize the entire Hermes Core stack. Idempotent."""
    return kernel.initialize()


def core_health_check() -> dict:
    """Run a full health check across all subsystems."""
    return get_kernel_singleton().health_check()


def core_status() -> dict:
    """Get comprehensive status of all subsystems."""
    return get_kernel_singleton().get_status()


# ---------------------------------------------------------------------------
# __all__ — explicit public API
# ---------------------------------------------------------------------------
__all__ = [
    # version
    "__version__",
    # exceptions
    "HermesCoreError",
    "WorldStateError",
    "PolicyViolation",
    "TaskExecutionError",
    "ResourceLimitExceeded",
    "ToolNotFoundError",
    "ReflectionError",
    "RecoveryError",
    "MemoryErrorBase",
    "EventLogError",
    "exception_from_name",
    # lazy submodules
    "world",
    "task_graph",
    "tool_registry",
    "policy",
    "reflection",
    "recovery",
    "memory",
    "event_log",
    "supervisor",
    "experience",
    "state",
    "self_observation",
    "kernel",
    "db_schema",
    "drift",
    "goal",
    "telemetry",
    "watchdog",
    # convenience functions
    "get_world_model",
    "get_tool_registry",
    "get_policy_engine",
    "get_kernel_singleton",
    "get_supervisor_singleton",
    "get_memory_manager_singleton",
    "get_observer_singleton",
    "get_drift_analyzer",
    "get_goal_manager_singleton",
    "get_telemetry_singleton",
    "get_watchdog_singleton",
    "core_initialize",
    "core_health_check",
    "core_status",
]
