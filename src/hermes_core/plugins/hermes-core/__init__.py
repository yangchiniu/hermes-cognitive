"""hermes-core plugin — registers hooks, tools, and slash commands.

Exposes Hermes vNext core modules (event_logger, telemetry, planner, etc.)
via the standard Hermes plugin API.  All imports are try/except — a missing
or broken core module merely disables that feature instead of crashing.

Usage:
    # Enable in config.yaml:
    plugins:
      enabled:
        - hermes-core

    # Verify with:
    hermes plugins list
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helper — temporarily add ~/.hermes/core/ to sys.path for bare imports.
# Called by hooks.py and tools.py at import time, never at module level.
# ---------------------------------------------------------------------------

_HERMES_HOME = Path.home() / ".hermes"
_CORE_DIR = str(_HERMES_HOME / "core")


# ---------------------------------------------------------------------------
# Lazy core module references (filled on first use)
# ---------------------------------------------------------------------------

_core_modules: dict[str, Any] = {}
_core_lock = threading.Lock()


def _lazy_core(name: str) -> Any:
    """Import and cache a core module, returning None on failure.

    Tries ``importlib.import_module('hermes.core.<name>')`` first (package
    import), then falls back to a bare ``import <name>`` via a temporary
    sys.path insertion of ``~/.hermes/core/``.

    Thread-safe via ``_core_lock`` — the sys.path manipulation happens
    inside the same critical section so concurrent calls to *different*
    modules can't interfere.
    """
    if name not in _core_modules:
        with _core_lock:
            if name not in _core_modules:
                import importlib as _ilib

                # Attempt 1: subpackage of hermes.core
                try:
                    _core_modules[name] = _ilib.import_module(
                        f"hermes.core.{name}"
                    )
                    return _core_modules[name]
                except ImportError:
                    pass

                # Attempt 2: standalone module with temporary sys.path
                _added = False
                try:
                    if _CORE_DIR not in sys.path:
                        sys.path.insert(0, _CORE_DIR)
                        _added = True
                    _core_modules[name] = _ilib.import_module(name)
                except ImportError as exc:
                    logger.warning(
                        "hermes-core: failed to import %s: %s", name, exc
                    )
                    _core_modules[name] = None
                finally:
                    if _added:
                        sys.path.remove(_CORE_DIR)
    return _core_modules[name]


def _get_event_logger():
    m = _lazy_core("event_logger")
    if m is not None and hasattr(m, "get_logger"):
        try:
            return m.get_logger()
        except Exception as exc:
            logger.warning("hermes-core: get_logger() failed: %s", exc)
    return None


def _get_telemetry():
    m = _lazy_core("telemetry")
    if m is not None and hasattr(m, "Telemetry"):
        try:
            return m.Telemetry()
        except Exception as exc:
            logger.warning("hermes-core: Telemetry() failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# register() — called by PluginManager on load
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register hooks, tools, and slash commands with the plugin context."""

    # Import hook and tool modules (they close over ctx)
    from . import hooks
    from . import tools as plugin_tools

    # ── Hooks ──────────────────────────────────────────────────────────

    ctx.register_hook("on_session_start", hooks.on_session_start)
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("post_llm_call", hooks.post_llm_call)
    ctx.register_hook("on_session_end", hooks.on_session_end)

    logger.info("hermes-core: registered 5 lifecycle hooks")

    # ── Tools ──────────────────────────────────────────────────────────

    _register_core_tools(ctx, plugin_tools)

    # ── Slash commands ────────────────────────────────────────────────

    ctx.register_command(
        "field-test",
        plugin_tools.slash_field_test,
        description="Run a Hermes Core field test",
        args_hint="[hours]",
    )
    ctx.register_command(
        "health",
        plugin_tools.slash_health,
        description="Show current telemetry health status",
    )

    logger.info("hermes-core: registered tools and slash commands")


# ---------------------------------------------------------------------------
# Tool registration helper
# ---------------------------------------------------------------------------


def _register_core_tools(ctx, pt) -> None:
    """Register all core module tools."""

    # ── planner ────────────────────────────────────────────────────
    ctx.register_tool(
        name="core_plan",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_plan",
                "description": "Decompose a high-level goal into a structured plan with steps, risks, and cost estimates",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "The high-level goal to decompose",
                        },
                        "context": {
                            "type": "string",
                            "description": "Additional context or constraints",
                        },
                    },
                    "required": ["goal"],
                },
            },
        },
        handler=pt.handle_core_plan,
        is_async=False,
    )

    # ── ooda ──────────────────────────────────────────────────────
    ctx.register_tool(
        name="core_ooda",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_ooda",
                "description": "Execute an OODA (Observe-Orient-Decide-Act) cycle for autonomous decision-making",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "goal": {
                            "type": "string",
                            "description": "The goal or objective",
                        },
                        "context": {
                            "type": "string",
                            "description": "Observation data and context",
                        },
                    },
                    "required": ["goal"],
                },
            },
        },
        handler=pt.handle_core_ooda,
        is_async=False,
    )

    # ── kernel ────────────────────────────────────────────────────
    ctx.register_tool(
        name="core_kernel_exec",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_kernel_exec",
                "description": "Execute a task through the Hermes Core kernel — start, stop, or check task status",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["start", "stop", "status", "list"],
                            "description": "Kernel action",
                        },
                        "task_id": {
                            "type": "string",
                            "description": "Task ID (optional for 'status' and 'stop')",
                        },
                        "description": {
                            "type": "string",
                            "description": "Task description (required for 'start')",
                        },
                    },
                    "required": ["action"],
                },
            },
        },
        handler=pt.handle_core_kernel,
        is_async=False,
    )

    # ── field test ────────────────────────────────────────────────
    ctx.register_tool(
        name="core_field_test",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_field_test",
                "description": "Run a Hermes Core field test to validate system stability and performance",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hours": {
                            "type": "number",
                            "description": "Test duration in hours (default: 1)",
                        },
                        "simulate": {
                            "type": "boolean",
                            "description": "If true, run a quick simulation instead of a real test",
                            "default": True,
                        },
                    },
                },
            },
        },
        handler=pt.handle_core_field_test,
        is_async=False,
    )

    # ── health ────────────────────────────────────────────────────
    ctx.register_tool(
        name="core_health",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_health",
                "description": "Show the current telemetry health status of the Hermes core system",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        handler=pt.handle_core_health,
        is_async=False,
    )

    # ── experience ────────────────────────────────────────────────
    ctx.register_tool(
        name="core_experience",
        toolset="hermes-core",
        schema={
            "type": "function",
            "function": {
                "name": "core_experience",
                "description": "Query the experience manager for past patterns, successes, and failures",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for past experiences",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results to return (default: 10)",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        handler=pt.handle_core_experience,
        is_async=False,
    )

    logger.info("hermes-core: registered 6 tools in toolset 'hermes-core'")
