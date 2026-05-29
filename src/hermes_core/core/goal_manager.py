"""
import logging

logger = logging.getLogger(__name__)


goal_manager.py — Goal Arbitration System for Hermes Core.

Provides a singleton GoalManager that tracks, prioritises, and arbitrates
goals across Hermes subsystems.  Integrates with the EventBus for lifecycle
notifications and supports interruption/preemption based on priority levels.

Dependencies: event_bus.py, event_logger.py, exceptions.py
"""
from __future__ import annotations
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional
try:
    from .event_bus import EventBus, Event
    _event_bus_available = True
except ImportError:
    _event_bus_available = False
try:
    from .exceptions import HermesCoreError
    _exceptions_available = True
except ImportError:
    _exceptions_available = False

    class HermesCoreError(Exception):
        pass

class GoalPriority(IntEnum):
    """Priority levels for goal arbitration.

    Higher values = higher priority.  A goal with higher priority can
    interrupt / preempt a running goal with lower priority.
    """
    RECOVERY = 100
    USER_TASK = 90
    PLANNED = 50
    MONITORING = 30
    CLEANUP = 10
    IDLE = 0

@dataclass
class Goal:
    """Represents a single tracked goal in the system.

    Attributes
    ----------
    goal_id : str
        UUID string uniquely identifying this goal.
    description : str
        Human-readable description of what the goal entails.
    priority : int
        Priority value (from ``GoalPriority`` or any integer).
    status : str
        One of ``pending|running|paused|suspended|completed|cancelled|failed``.
    created_at : str
        ISO-8601 timestamp of creation.
    started_at : str or None
        ISO-8601 timestamp when the goal was started (set by ``start_goal``).
    completed_at : str or None
        ISO-8601 timestamp when the goal reached a terminal state.
    parent_id : str or None
        For nested / sub-goals, the ``goal_id`` of the parent goal.
    source : str or None
        Origin of the goal — ``"user"``, ``"system"``, ``"ooda"``, ``"recovery"``.
    context : dict or None
        Arbitrary metadata / payload associated with the goal.
    """
    goal_id: str
    description: str
    priority: int
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    parent_id: Optional[str] = None
    source: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.context is None:
            self.context = {}

    @property
    def is_active(self) -> bool:
        """Return ``True`` if the goal is running, paused, or suspended."""
        return self.status in ('running', 'paused', 'suspended')

    @property
    def is_terminal(self) -> bool:
        """Return ``True`` if the goal is in a terminal state."""
        return self.status in ('completed', 'cancelled', 'failed')
_VALID_STATUSES = frozenset({'pending', 'running', 'paused', 'suspended', 'completed', 'cancelled', 'failed'})
_TERMINAL_STATUSES = frozenset({'completed', 'cancelled', 'failed'})
_goal_manager_lock = threading.Lock()
_goal_manager_instance: Optional['GoalManager'] = None

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

def _new_uuid() -> str:
    """Return a hex UUID string."""
    return str(uuid.uuid4())

def _now_float() -> float:
    """Return monotonic clock seconds (for duration calculation)."""
    return time.monotonic()

class GoalManager:
    """Central goal registry and arbitration system (singleton).

    Thread-safe goal lifecycle management with priority-based interruption.
    Publishes lifecycle events to the shared ``EventBus`` when available.

    Usage
    -----
    >>> from goal_manager import GoalManager, GoalPriority
    >>> mgr = GoalManager()
    >>> gid = mgr.register_goal("Process user query", GoalPriority.USER_TASK)
    >>> mgr.start_goal(gid)
    >>> current = mgr.get_current_goal()
    >>> mgr.complete_goal(gid, "Done")
    """
    _instance: Optional['GoalManager'] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> 'GoalManager':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, '_initialized', False):
            return
        self._lock = threading.Lock()
        self._goals: Dict[str, Goal] = {}
        self._start_times: Dict[str, float] = {}
        self._bus: Optional[EventBus] = None
        if _event_bus_available:
            try:
                self._bus = EventBus()
            except Exception:
                self._bus = None
        self._initialized = True


    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None

    def _publish(self, event_type: str, data: Optional[Dict[str, Any]]=None, severity: str='info') -> None:
        """Publish an event to the EventBus if available."""
        if self._bus is not None:
            try:
                self._bus.publish(event_type=event_type, data=data or {}, source='goal_manager', severity=severity)
            except Exception as exc:
                logger.debug('goal_manager: _publish: %s', exc)

    def _validate_status(self, status: str) -> None:
        """Raise ``ValueError`` if *status* is not a valid goal status."""
        if status not in _VALID_STATUSES:
            raise ValueError(f'Invalid status {status!r}. Must be one of {sorted(_VALID_STATUSES)}')

    def _check_goal_exists(self, goal_id: str) -> Goal:
        """Return the goal or raise ``KeyError``."""
        with self._lock:
            goal = self._goals.get(goal_id)
        if goal is None:
            raise KeyError(f'Goal {goal_id!r} not found')
        return goal

    def register_goal(self, description: str, priority: int, source: Optional[str]=None, parent_id: Optional[str]=None, context: Optional[Dict[str, Any]]=None) -> str:
        """Register a new goal and return its ``goal_id``.

        Parameters
        ----------
        description : str
            Human-readable description.
        priority : int
            Priority value (use ``GoalPriority`` members for standard levels).
        source : str or None
            Origin of the goal (e.g. ``"user"``, ``"system"``, ``"ooda"``).
        parent_id : str or None
            Optional parent goal ID for nesting.
        context : dict or None
            Arbitrary metadata.

        Returns
        -------
        str
            The UUID of the newly created goal.
        """
        goal_id = _new_uuid()
        now = _timestamp()
        goal = Goal(goal_id=goal_id, description=description, priority=priority, status='pending', created_at=now, parent_id=parent_id, source=source, context=context or {})
        with self._lock:
            self._goals[goal_id] = goal
        self._publish('goal.registered', {'goal_id': goal_id, 'description': description, 'priority': priority, 'source': source, 'parent_id': parent_id})
        return goal_id

    def register_goal_with_priority(self, description: str, priority: GoalPriority, source: Optional[str]=None, parent_id: Optional[str]=None, context: Optional[Dict[str, Any]]=None) -> str:
        """Convenience wrapper accepting a ``GoalPriority`` enum value."""
        return self.register_goal(description, priority.value, source, parent_id, context)

    def start_goal(self, goal_id: str) -> None:
        """Mark a pending goal as running.

        Sets status to ``"running"``, records ``started_at`` and publishes
        a ``goal.started`` event.

        Raises
        ------
        KeyError
            If *goal_id* does not exist.
        ValueError
            If the goal is not in ``"pending"`` status.
        """
        now = _timestamp()
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status != 'pending':
                raise ValueError(f"Cannot start goal {goal_id!r}: current status is {goal.status!r}, expected 'pending'")
            goal.status = 'running'
            goal.started_at = now
            self._start_times[goal_id] = _now_float()
        self._publish('goal.started', {'goal_id': goal_id, 'description': goal.description, 'priority': goal.priority})

    def complete_goal(self, goal_id: str, result: Optional[str]=None) -> None:
        """Mark a running/paused/suspended goal as completed."""
        now = _timestamp()
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status in _TERMINAL_STATUSES:
                raise ValueError(f'Cannot complete goal {goal_id!r}: already in terminal status {goal.status!r}')
            goal.status = 'completed'
            goal.completed_at = now
            self._start_times.pop(goal_id, None)
        self._publish('goal.completed', {'goal_id': goal_id, 'result': result, 'description': goal.description})

    def fail_goal(self, goal_id: str, reason: Optional[str]=None) -> None:
        """Mark a running/paused/suspended goal as failed."""
        now = _timestamp()
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status in _TERMINAL_STATUSES:
                raise ValueError(f'Cannot fail goal {goal_id!r}: already in terminal status {goal.status!r}')
            goal.status = 'failed'
            goal.completed_at = now
            self._start_times.pop(goal_id, None)
        self._publish('goal.failed', {'goal_id': goal_id, 'reason': reason, 'description': goal.description}, severity='error')

    def cancel_goal(self, goal_id: str) -> None:
        """Cancel a goal (any non-terminal status -> cancelled)."""
        now = _timestamp()
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status in _TERMINAL_STATUSES:
                raise ValueError(f'Cannot cancel goal {goal_id!r}: already in terminal status {goal.status!r}')
            goal.status = 'cancelled'
            goal.completed_at = now
            self._start_times.pop(goal_id, None)
        self._publish('goal.cancelled', {'goal_id': goal_id, 'description': goal.description})

    def suspend_goal(self, goal_id: str) -> None:
        """Suspend a running/paused goal (can be resumed later).

        Raises
        ------
        ValueError
            If the goal is not in ``"running"`` or ``"paused"`` status.
        """
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status not in ('running', 'paused'):
                raise ValueError(f"Cannot suspend goal {goal_id!r}: current status is {goal.status!r}, expected 'running' or 'paused'")
            goal.status = 'suspended'
        self._publish('goal.suspended', {'goal_id': goal_id, 'description': goal.description})

    def resume_goal(self, goal_id: str) -> None:
        """Resume a suspended goal (back to running)."""
        with self._lock:
            goal = self._goals.get(goal_id)
            if goal is None:
                raise KeyError(f'Goal {goal_id!r} not found')
            if goal.status != 'suspended':
                raise ValueError(f"Cannot resume goal {goal_id!r}: current status is {goal.status!r}, expected 'suspended'")
            goal.status = 'running'
            self._start_times[goal_id] = _now_float()
        self._publish('goal.resumed', {'goal_id': goal_id, 'description': goal.description})

    def get_current_goal(self) -> Optional[Goal]:
        """Return the highest-priority RUNNING goal, or ``None``.

        Goals with equal priority are resolved by earliest ``started_at``.
        """
        running: List[Goal] = []
        with self._lock:
            for goal in self._goals.values():
                if goal.status == 'running':
                    running.append(goal)
        if not running:
            return None
        running.sort(key=lambda g: (-g.priority, g.started_at or g.created_at))
        return running[0]

    def get_active_goals(self, priority: Optional[int]=None) -> List[Goal]:
        """Return all running + suspended goals, optionally filtered by priority.

        Parameters
        ----------
        priority : int or None
            If provided, only goals with this exact priority are returned.

        Returns
        -------
        list[Goal]
            Goals sorted by priority descending, then created_at ascending.
        """
        active: List[Goal] = []
        with self._lock:
            for goal in self._goals.values():
                if goal.status in ('running', 'suspended'):
                    if priority is None or goal.priority == priority:
                        active.append(goal)
        active.sort(key=lambda g: (-g.priority, g.created_at))
        return active

    def get_all_goals(self, status: Optional[str]=None) -> List[Goal]:
        """Return all goals, optionally filtered by status.

        Parameters
        ----------
        status : str or None
            If provided, only goals with this exact status are returned.

        Returns
        -------
        list[Goal]
            Goals sorted by created_at descending (most recent first).
        """
        if status is not None:
            self._validate_status(status)
        result: List[Goal] = []
        with self._lock:
            for goal in self._goals.values():
                if status is None or goal.status == status:
                    result.append(goal)
        result.sort(key=lambda g: g.created_at, reverse=True)
        return result

    def get_goal_by_id(self, goal_id: str) -> Optional[Goal]:
        """Look up a single goal by its ID.

        Returns the ``Goal`` or ``None`` if not found.
        """
        with self._lock:
            return self._goals.get(goal_id)

    def get_highest_pending(self) -> Optional[Goal]:
        """Return the highest-priority pending goal, or ``None``.

        Intended for the OODA loop to pick the next goal to execute.
        Ties are broken by earliest ``created_at``.
        """
        pending: List[Goal] = []
        with self._lock:
            for goal in self._goals.values():
                if goal.status == 'pending':
                    pending.append(goal)
        if not pending:
            return None
        pending.sort(key=lambda g: (-g.priority, g.created_at))
        return pending[0]

    def should_interrupt(self, new_priority: int) -> bool:
        """Determine whether a goal with *new_priority* should interrupt the
        currently running goal.

        Returns ``True`` if *new_priority* is strictly greater than the
        priority of the current running goal.  If no goal is running,
        returns ``False`` (nothing to interrupt).
        """
        current = self.get_current_goal()
        if current is None:
            return False
        return new_priority > current.priority

    def interrupt_current(self, interrupting_goal_id: str) -> bool:
        """Suspend the current running goal and prepare for the interrupting
        goal to start.

        Steps
        -----
        1. Finds the highest-priority running goal (the one to interrupt).
        2. Suspends it (status -> ``"suspended"``).
        3. Publishes a ``goal.interrupted`` event with details of both the
           interrupted and interrupting goals.
        4. Returns ``True`` if an interruption actually occurred.

        The caller is responsible for calling ``start_goal()`` on the
        interrupting goal after this returns.

        Parameters
        ----------
        interrupting_goal_id : str
            The ``goal_id`` of the goal that wants to interrupt.

        Returns
        -------
        bool
            ``True`` if a goal was interrupted, ``False`` if nothing was
            running.
        """
        current = self.get_current_goal()
        if current is None:
            return False
        interrupting = self._check_goal_exists(interrupting_goal_id)
        with self._lock:
            cur = self._goals.get(current.goal_id)
            if cur is not None and cur.status == 'running':
                cur.status = 'suspended'
        self._publish('goal.interrupted', {'interrupted_goal_id': current.goal_id, 'interrupted_description': current.description, 'interrupted_priority': current.priority, 'interrupting_goal_id': interrupting_goal_id, 'interrupting_description': interrupting.description, 'interrupting_priority': interrupting.priority}, severity='warning')
        return True

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics about all goals.

        Returns
        -------
        dict with keys:
            - ``total_goals`` : int
            - ``by_status`` : dict[str, int]
            - ``by_priority`` : dict[str, int]
            - ``avg_duration_seconds`` : float or None
            - ``active_count`` : int
            - ``pending_count`` : int
        """
        status_counts: Dict[str, int] = {}
        priority_counts: Dict[str, int] = {}
        total = 0
        completed_durations: List[float] = []
        with self._lock:
            now_float = _now_float()
            for goal in self._goals.values():
                total += 1
                status_counts[goal.status] = status_counts.get(goal.status, 0) + 1
                pkey = str(goal.priority)
                priority_counts[pkey] = priority_counts.get(pkey, 0) + 1
                if goal.status in _TERMINAL_STATUSES and goal.started_at is not None:
                    try:
                        started = datetime.fromisoformat(goal.started_at)
                        completed = datetime.fromisoformat(goal.completed_at or _timestamp())
                        delta = (completed - started).total_seconds()
                        if delta >= 0:
                            completed_durations.append(delta)
                    except Exception as exc:
                        logger.debug('goal_manager: get_stats: %s', exc)
                if goal.status == 'running' and goal.goal_id in self._start_times:
                    elapsed = now_float - self._start_times[goal.goal_id]
                    if elapsed >= 0:
                        completed_durations.append(elapsed)
        avg_dur = sum(completed_durations) / len(completed_durations) if completed_durations else None
        return {'total_goals': total, 'by_status': dict(sorted(status_counts.items())), 'by_priority': dict(sorted(priority_counts.items(), key=lambda x: int(x[0]), reverse=True)), 'avg_duration_seconds': avg_dur, 'active_count': status_counts.get('running', 0) + status_counts.get('suspended', 0), 'pending_count': status_counts.get('pending', 0)}

def get_goal_manager() -> GoalManager:
    """Return the singleton ``GoalManager`` instance.

    This is the recommended way to access the goal manager from other
    modules, as it makes the dependency explicit and testable.
    """
    return GoalManager()