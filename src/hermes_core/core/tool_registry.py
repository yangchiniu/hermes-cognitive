"""
tool_registry.py — Capability-based tool registration and discovery system.

Provides a singleton ``ToolRegistry`` that maps tool capabilities to the
underlying tool implementations (tools).  Supports registration, fuzzy
lookup, usage tracking, and persistence to disk.

Typical usage::

    from hermes.core.tool_registry import ToolRegistry, ToolCapability

    registry = ToolRegistry()
    registry.register_defaults()
    results = registry.find("web", filters={"max_risk": "low"})
    cap = registry.get("web_search")
    registry.record_outcome("web_scrape", success=True, duration_s=2.5)
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path.home() / ".hermes" / "core" / "data"
_STATE_FILE = _DATA_DIR / "tool_registry.json"

_RISK_ORDER: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}
_COST_ORDER: dict[str, int] = {"free": 0, "low": 1, "medium": 2, "high": 3}


# ---------------------------------------------------------------------------
# Risk / cost level helpers
# ---------------------------------------------------------------------------

def risk_level(r: str) -> int:
    """Map a risk string to an integer rank.

    Returns
    -------
    int
        ``0`` for ``"none"``, ``1`` for ``"low"``, ``2`` for ``"medium"``,
        ``3`` for ``"high"``.  Unknown values are treated as ``"high"``.
    """
    return _RISK_ORDER.get(r.strip().lower(), 3)


def cost_level(c: str) -> int:
    """Map a cost string to an integer rank.

    Returns
    -------
    int
        ``0`` for ``"free"``, ``1`` for ``"low"``, ``2`` for ``"medium"``,
        ``3`` for ``"high"``.  Unknown values are treated as ``"high"``.
    """
    return _COST_ORDER.get(c.strip().lower(), 3)


# ---------------------------------------------------------------------------
# ToolCapability
# ---------------------------------------------------------------------------

@dataclass
class ToolCapability:
    """Definition of a tool capability.

    Attributes
    ----------
    name : str
        Unique capability name, e.g. ``"web_scrape"``.
    description : str
        Human-readable description of what this capability does.
    tools : list[str]
        Names of the underlying tool implementations that satisfy this
        capability (e.g. ``["hermes-scrape", "browser_navigate"]``).
    cost : str
        Resource cost: ``"free"``, ``"low"``, ``"medium"``, or ``"high"``.
    risk : str
        Risk level: ``"none"``, ``"low"``, ``"medium"``, or ``"high"``.
    requires_auth : bool
        Whether the capability needs authentication.
    requires_network : bool
        Whether the capability needs network access.
    requires_browser : bool
        Whether the capability needs a browser environment.
    timeout_s : int
        Default timeout in seconds.
    success_rate : float
        Fraction of successful invocations (learned from experience).
        Ranges from ``0.0`` to ``1.0``.
    call_count : int
        Total number of times this capability has been invoked.
    """

    name: str
    description: str
    tools: list[str]
    cost: str = "medium"
    risk: str = "medium"
    requires_auth: bool = False
    requires_network: bool = False
    requires_browser: bool = False
    timeout_s: int = 30
    success_rate: float = 0.0
    call_count: int = 0

    def __post_init__(self) -> None:
        """Normalise string fields on construction."""
        self.name = self.name.strip()
        self.description = self.description.strip()
        self.cost = self.cost.strip().lower()
        self.risk = self.risk.strip().lower()
        # Clamp success_rate
        if self.success_rate < 0.0:
            self.success_rate = 0.0
        elif self.success_rate > 1.0:
            self.success_rate = 1.0
        if self.call_count < 0:
            self.call_count = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "cost": self.cost,
            "risk": self.risk,
            "requires_auth": self.requires_auth,
            "requires_network": self.requires_network,
            "requires_browser": self.requires_browser,
            "timeout_s": self.timeout_s,
            "success_rate": self.success_rate,
            "call_count": self.call_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCapability":
        """Deserialize from a dictionary (produced by ``to_dict``)."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            tools=data.get("tools", []),
            cost=data.get("cost", "medium"),
            risk=data.get("risk", "medium"),
            requires_auth=data.get("requires_auth", False),
            requires_network=data.get("requires_network", False),
            requires_browser=data.get("requires_browser", False),
            timeout_s=data.get("timeout_s", 30),
            success_rate=float(data.get("success_rate", 0.0)),
            call_count=int(data.get("call_count", 0)),
        )


# ---------------------------------------------------------------------------
# Singleton machinery
# ---------------------------------------------------------------------------

_instances: dict[str, "ToolRegistry"] = {}
_instances_lock = threading.Lock()


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Capability-based tool registry (singleton).

    Manages a collection of ``ToolCapability`` records, supports fuzzy
    lookup, usage tracking, and persistence to ``~/.hermes/core/data/``.

    Usage
    -----
    >>> registry = ToolRegistry()
    >>> registry.register_defaults()
    >>> caps = registry.find("web")
    >>> reg = registry.get("web_search")
    >>> registry.record_outcome("web_search", success=True, duration_s=0.5)
    >>> registry.save()
    """

    def __new__(cls) -> "ToolRegistry":
        key = "default"
        with _instances_lock:
            if key not in _instances:
                obj = super().__new__(cls)
                obj._initialized = False
                _instances[key] = obj
            return _instances[key]

    def __init__(self) -> None:
        """Lazy initialisation: loads persisted state on first access.

        The registry is populated from ``~/.hermes/core/data/tool_registry.json``
        if it exists.  If the file is missing or corrupt, an empty registry
        is created.
        """
        if getattr(self, "_initialized", False):
            return

        self._lock = threading.Lock()
        self._capabilities: dict[str, ToolCapability] = {}
        self._loaded = False
        self._reg_count = 0  # auto-save counter
        self._load()
        self._initialized = True

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instances_lock:
            _instances.clear()

    # -- public API ---------------------------------------------------------

    def register(self, capability: ToolCapability) -> None:
        """Add a new capability or update an existing one.

        If a capability with the same ``name`` already exists, the existing
        record is replaced (including accumulated ``call_count`` and
        ``success_rate`` — the new record's values take precedence).

        Auto-saves every 10 registrations for crash safety.
        """
        with self._lock:
            self._capabilities[capability.name] = capability
            self._reg_count += 1
        if self._reg_count >= 10:
            self._reg_count = 0
            try:
                self.save()
            except Exception:
                pass  # save failure must not block registration

    def register_defaults(self) -> None:
        """Register the built-in set of Hermes tool capabilities.

        This is safe to call multiple times — it will overwrite any
        capabilities that have the same name, but leave custom
        capabilities untouched.
        """
        defaults = [
            ToolCapability(
                name="web_scrape",
                description="Scrape and extract content from web pages",
                tools=["hermes-scrape", "hermes-scrape", "browser_navigate"],
                cost="low",
                risk="medium",
                requires_network=True,
                timeout_s=60,
            ),
            ToolCapability(
                name="web_search",
                description="Search the web via search engines",
                tools=["ddgs", "web_search"],
                cost="free",
                risk="low",
                requires_network=True,
                timeout_s=30,
            ),
            ToolCapability(
                name="terminal_exec",
                description="Execute shell commands in the terminal",
                tools=["terminal"],
                cost="medium",
                risk="high",
                timeout_s=120,
            ),
            ToolCapability(
                name="file_read",
                description="Read file contents and search files",
                tools=["read_file", "search_files"],
                cost="free",
                risk="none",
                timeout_s=15,
            ),
            ToolCapability(
                name="file_edit",
                description="Edit files using targeted patch or full rewrite",
                tools=["write_file", "patch"],
                cost="free",
                risk="medium",
                timeout_s=30,
            ),
            ToolCapability(
                name="browser_interact",
                description="Interact with web pages via browser automation",
                tools=["browser_navigate", "browser_click", "browser_type"],
                cost="low",
                risk="medium",
                requires_browser=True,
                timeout_s=60,
            ),
            ToolCapability(
                name="code_exec",
                description="Execute code snippets in isolated environments",
                tools=["execute_code"],
                cost="medium",
                risk="high",
                timeout_s=60,
            ),
            ToolCapability(
                name="db_query",
                description="Query SQLite databases",
                tools=["db_query"],
                cost="low",
                risk="medium",
                timeout_s=30,
            ),
            ToolCapability(
                name="vision_analyze",
                description="Analyze images and visual content",
                tools=["vision"],
                cost="high",
                risk="low",
                timeout_s=60,
            ),
            ToolCapability(
                name="delegation",
                description="Delegate tasks to sub-agents for parallel work",
                tools=["delegate_task"],
                cost="medium",
                risk="low",
                timeout_s=300,
            ),
        ]

        with self._lock:
            for cap in defaults:
                # Preserve learned stats if the capability already exists
                existing = self._capabilities.get(cap.name)
                if existing is not None:
                    cap.call_count = existing.call_count
                    cap.success_rate = existing.success_rate
                self._capabilities[cap.name] = cap

    def find(
        self,
        query: str = "",
        filters: Optional[dict[str, Any]] = None,
    ) -> List[ToolCapability]:
        """Search capabilities by name/description and optional filters.

        Parameters
        ----------
        query : str
            Case-insensitive substring matched against ``name`` and
            ``description``.  If empty or blank, all capabilities are
            candidates (subject to ``filters``).
        filters : dict or None
            Optional filter criteria.  Supported keys:

            * ``requires_auth`` (bool) — filter by auth requirement.
            * ``requires_network`` (bool) — filter by network requirement.
            * ``requires_browser`` (bool) — filter by browser requirement.
            * ``max_risk`` (str) — maximum risk level (e.g. ``"low"``).
            * ``max_cost`` (str) — maximum cost level (e.g. ``"medium"``).

        Returns
        -------
        list[ToolCapability]
            Matching capabilities sorted alphabetically by name.
        """
        query_lower = query.strip().lower()
        filters = filters or {}

        with self._lock:
            candidates = list(self._capabilities.values())

        # -- text search ----------------------------------------------------
        if query_lower:
            candidates = [
                c for c in candidates
                if query_lower in c.name.lower()
                or query_lower in c.description.lower()
            ]

        # -- boolean filters ------------------------------------------------
        for bool_key in ("requires_auth", "requires_network", "requires_browser"):
            val = filters.get(bool_key)
            if val is not None:
                candidates = [c for c in candidates if getattr(c, bool_key) == val]

        # -- risk filter ----------------------------------------------------
        max_risk = filters.get("max_risk")
        if max_risk is not None:
            max_r = risk_level(str(max_risk))
            candidates = [c for c in candidates if risk_level(c.risk) <= max_r]

        # -- cost filter ----------------------------------------------------
        max_cost = filters.get("max_cost")
        if max_cost is not None:
            max_c = cost_level(str(max_cost))
            candidates = [c for c in candidates if cost_level(c.cost) <= max_c]

        candidates.sort(key=lambda c: c.name)
        return candidates

    def get(self, name: str) -> Optional[ToolCapability]:
        """Look up a capability by exact name.

        Returns ``None`` if no capability with that name is registered.
        """
        name = name.strip()
        with self._lock:
            return self._capabilities.get(name)

    def list_all(self) -> List[ToolCapability]:
        """Return a sorted list of all registered capabilities."""
        with self._lock:
            return sorted(
                self._capabilities.values(),
                key=lambda c: c.name,
            )

    def get_stats(self) -> dict[str, Any]:
        """Aggregate usage statistics across all capabilities.

        Returns
        -------
        dict
            Keys: ``total_capabilities``, ``total_calls``,
            ``by_risk``, ``by_cost``, ``by_network``, ``by_browser``,
            ``average_success_rate``.
        """
        with self._lock:
            caps = list(self._capabilities.values())

        total_calls = sum(c.call_count for c in caps)
        total_capabilities = len(caps)

        by_risk: dict[str, int] = {}
        by_cost: dict[str, int] = {}
        by_network = 0
        by_browser = 0

        for c in caps:
            by_risk[c.risk] = by_risk.get(c.risk, 0) + 1
            by_cost[c.cost] = by_cost.get(c.cost, 0) + 1
            if c.requires_network:
                by_network += 1
            if c.requires_browser:
                by_browser += 1

        # Weighted average success rate (weighted by call_count)
        if total_calls > 0:
            weighted_sum = sum(c.success_rate * c.call_count for c in caps)
            avg_success = weighted_sum / total_calls
        else:
            avg_success = 0.0

        return {
            "total_capabilities": total_capabilities,
            "total_calls": total_calls,
            "by_risk": dict(sorted(by_risk.items())),
            "by_cost": dict(sorted(by_cost.items())),
            "requires_network_count": by_network,
            "requires_browser_count": by_browser,
            "average_success_rate": round(avg_success, 4),
        }

    def record_outcome(
        self,
        tool_name: str,
        success: bool,
        duration_s: float,
    ) -> None:
        """Record a usage outcome to update success rate and call count.

        The success rate is computed as a running average:

            new_rate = (old_rate * old_count + (1 if success else 0)) / (old_count + 1)

        Parameters
        ----------
        tool_name : str
            The capability name (not the tool implementation name).
        success : bool
            Whether the invocation succeeded.
        duration_s : float
            Wall-clock duration of the invocation in seconds.
        """
        tool_name = tool_name.strip()
        with self._lock:
            cap = self._capabilities.get(tool_name)
            if cap is None:
                raise KeyError(
                    f"No capability registered with name {tool_name!r}. "
                    f"Available: {sorted(self._capabilities)}"
                )

            old_count = cap.call_count
            old_rate = cap.success_rate

            cap.call_count = old_count + 1
            cap.success_rate = (
                (old_rate * old_count + (1.0 if success else 0.0))
                / cap.call_count
            )
            # Update timeout hint based on duration (weighted average)
            # Slightly bias toward the current timeout to avoid runaway growth.
            cap.timeout_s = max(
                5,
                int(round(cap.timeout_s * 0.8 + duration_s * 0.2)),
            )

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        """Persist the current registry state to disk as JSON.

        The file is written atomically (write to temp, then rename) to
        avoid corruption on partial writes.
        """
        with self._lock:
            data = {
                "version": 1,
                "capabilities": [
                    cap.to_dict() for cap in self._capabilities.values()
                ],
            }

        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            tmp.replace(_STATE_FILE)
        except OSError as exc:
            raise OSError(
                f"Failed to save tool registry to {_STATE_FILE}: {exc}"
            ) from exc

    def _load(self) -> None:
        """Load persisted state from disk (called once during ``__init__``).

        If the file does not exist or is corrupt, the registry starts
        empty.
        """
        if self._loaded:
            return
        self._loaded = True

        if not _STATE_FILE.exists():
            return

        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # File corrupt or unreadable — start fresh
            return

        raw_caps = data.get("capabilities", []) if isinstance(data, dict) else []
        for raw in raw_caps:
            try:
                cap = ToolCapability.from_dict(raw)
                self._capabilities[cap.name] = cap
            except (TypeError, ValueError, KeyError):
                continue

    # -- dunder convenience ------------------------------------------------

    def __len__(self) -> int:
        """Return the number of registered capabilities."""
        with self._lock:
            return len(self._capabilities)

    def __contains__(self, name: str) -> bool:
        """Check if a capability is registered by name."""
        with self._lock:
            return name.strip() in self._capabilities

    def __repr__(self) -> str:
        caps = self.list_all()
        return (
            f"<ToolRegistry count={len(caps)} "
            f"calls={sum(c.call_count for c in caps)}>"
        )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_instance: Optional[ToolRegistry] = None
_instance_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    """Return the application-wide ToolRegistry singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ToolRegistry()
        return _instance
