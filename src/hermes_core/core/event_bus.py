"""
event_bus.py — Publish/subscribe event system for Hermes Core.

Provides a singleton EventBus that decouples modules via typed events.
Every published event is logged to EventLogger (NDJSON) for persistence.
Thread-safe, pure stdlib, zero external dependencies.
"""
from __future__ import annotations
import json
import threading
import time
import uuid as _uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
try:
    from .event_logger import get_logger
    _logger_available = True
except ImportError:
    _logger_available = False
try:
    from .exceptions import EventLogError
    _exceptions_available = True
except ImportError:
    _exceptions_available = False
_MAX_HISTORY = 500
EVENT_TASK_STARTED = 'task.started'
EVENT_TASK_COMPLETED = 'task.completed'
EVENT_TASK_FAILED = 'task.failed'
EVENT_POLICY_BLOCKED = 'policy.blocked'
EVENT_MEMORY_UPDATED = 'memory.updated'
EVENT_RESOURCE_WARNING = 'resource.warning'
EVENT_RECOVERY_TRIGGERED = 'recovery.triggered'
EVENT_REFLECTION_COMPLETED = 'reflection.completed'
EVENT_PLAN_CREATED = 'plan.created'
EVENT_PLAN_STEP_STARTED = 'plan.step.started'
EVENT_PLAN_STEP_COMPLETED = 'plan.step.completed'
EVENT_PLAN_STEP_FAILED = 'plan.step.failed'
EVENT_PLAN_COMPLETED = 'plan.completed'
EVENT_PLAN_FAILED = 'plan.failed'
EVENT_OBSERVATION_CYCLE = 'observation.cycle'
EVENT_SYSTEM_STARTUP = 'system.startup'
EVENT_SYSTEM_SHUTDOWN = 'system.shutdown'
EVENT_BROWSER_CREATED = 'browser.created'
EVENT_BROWSER_CLOSED = 'browser.closed'
_ALL_STANDARD_EVENTS = frozenset({EVENT_TASK_STARTED, EVENT_TASK_COMPLETED, EVENT_TASK_FAILED, EVENT_POLICY_BLOCKED, EVENT_MEMORY_UPDATED, EVENT_RESOURCE_WARNING, EVENT_RECOVERY_TRIGGERED, EVENT_REFLECTION_COMPLETED, EVENT_PLAN_CREATED, EVENT_PLAN_STEP_STARTED, EVENT_PLAN_STEP_COMPLETED, EVENT_PLAN_STEP_FAILED, EVENT_PLAN_COMPLETED, EVENT_PLAN_FAILED, EVENT_OBSERVATION_CYCLE, EVENT_SYSTEM_STARTUP, EVENT_SYSTEM_SHUTDOWN, EVENT_BROWSER_CREATED, EVENT_BROWSER_CLOSED})
_VALID_SEVERITIES = frozenset({'info', 'warning', 'error', 'critical'})

class EventPriority:
    """Event priority levels (lower number = higher priority)."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
PRIORITY_CRITICAL = EventPriority.CRITICAL
PRIORITY_HIGH = EventPriority.HIGH
PRIORITY_MEDIUM = EventPriority.MEDIUM
PRIORITY_LOW = EventPriority.LOW

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

def _new_uuid() -> str:
    """Return a hex UUID string."""
    return str(_uuid.uuid4())

@dataclass
class Event:
    """A typed event payload flowing through the EventBus.

    Attributes
    ----------
    event_type : str
        Dot-separated event type, e.g. ``"task.completed"``.
    data : dict or None
        Arbitrary payload attached to the event.
    source : str or None
        Name of the module / subsystem that published the event.
    timestamp : str
        ISO-8601 UTC timestamp (auto-set at creation).
    event_id : str
        Unique UUID string (auto-set at creation).
    severity : str
        One of ``"info"``, ``"warning"``, ``"error"``, ``"critical"``.
    """
    event_type: str
    data: dict = None
    source: str = None
    timestamp: str = None
    event_id: str = None
    severity: str = 'info'
    priority: int = PRIORITY_MEDIUM

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = _timestamp()
        if self.event_id is None:
            self.event_id = _new_uuid()
        if self.data is None:
            self.data = {}
        if self.severity not in _VALID_SEVERITIES:
            self.severity = 'info'
        if self.priority not in (EventPriority.CRITICAL, EventPriority.HIGH, EventPriority.MEDIUM, EventPriority.LOW):
            self.priority = PRIORITY_MEDIUM

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation / logging."""
        return {'event_type': self.event_type, 'data': self.data, 'source': self.source, 'timestamp': self.timestamp, 'event_id': self.event_id, 'severity': self.severity, 'priority': self.priority}

@dataclass
class _Subscription:
    """Internal record for a registered subscriber."""
    subscription_id: str
    event_type: str
    callback: Callable[[Event], None]
    filter_fn: Optional[Callable[[Event], bool]] = None

class EventBus:
    """Publish/subscribe event bus (singleton).

    Decouples Hermes Core modules: any module can publish events; any module
    can subscribe to events of interest.  Events are persisted via EventLogger
    and kept in an in-memory ring buffer for fast history queries.

    Usage
    -----
    >>> bus = EventBus()
    >>> sid = bus.subscribe("task.completed", my_callback)
    >>> eid = bus.publish("task.completed", {"task_id": "abc"}, source="kernel")
    >>> bus.unsubscribe(sid)
    """
    _instance: Optional['EventBus'] = None
    _instance_lock = threading.Lock()
    _global_init_lock = threading.Lock()

    def __new__(cls) -> 'EventBus':
        with cls._instance_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                cls._instance = obj
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None


    def __init__(self, max_history: int=_MAX_HISTORY) -> None:
        """Initialise the event bus singleton.

        Parameters
        ----------
        max_history : int
            Maximum number of events to keep in-memory history.
            Default: 500.
        """
        if getattr(self, '_initialized', False):
            return
        self._max_history = max_history
        self._subscribers: Dict[str, List[_Subscription]] = {}
        self._wildcard_subscribers: List[_Subscription] = []
        self._lock = threading.Lock()
        self._history: List[Event] = []
        self._counts: Dict[str, int] = {}
        self._cv = threading.Condition(self._lock)
        self._logger = None
        if _logger_available:
            try:
                self._logger = get_logger()
            except Exception:
                self._logger = None
        self._rate_limits: Dict[str, float] = {}
        self._rate_limit_buckets: Dict[str, List[float]] = defaultdict(list)
        self._rate_limited_queue: List[Event] = []
        self._rate_limited_dropped: int = 0
        self._rate_limited_queued_count: int = 0
        self._dead_letter_queue: List[Event] = []
        self._dedup_window_s: float = 1.0
        self._recent_event_ids: deque = deque()
        self._dedup_dropped: Dict[str, int] = {}
        self._total_published: int = 0
        self._delivered: int = 0
        self._initialized = True

    def publish(self, event_type: str, data: Optional[Dict[str, Any]]=None, source: Optional[str]=None, severity: str='info', priority: int=PRIORITY_MEDIUM) -> str:
        """Create an Event, dispatch to subscribers, and persist it.

        Parameters
        ----------
        event_type : str
            Dot-separated event type, e.g. ``"task.completed"``.
        data : dict or None
            Arbitrary payload.
        source : str or None
            Module identifier that published this event.
        severity : str
            One of ``"info"``, ``"warning"``, ``"error"``, ``"critical"``.
        priority : int
            Event priority level (default: PRIORITY_MEDIUM).

        Returns
        -------
        str
            The UUID assigned to this event (event_id).
        """
        event = Event(event_type=event_type, data=data, source=source, severity=severity, priority=priority)
        dedup_key = (event.event_type, json.dumps(event.data, sort_keys=True, default=str))
        if self._is_duplicate(dedup_key):
            with self._lock:
                self._dedup_dropped[event_type] = self._dedup_dropped.get(event_type, 0) + 1
            return event.event_id
        if self._is_rate_limited(event.event_type):
            if event.priority <= PRIORITY_HIGH:
                with self._lock:
                    self._rate_limited_queue.append(event)
                    self._rate_limited_queued_count += 1
                return event.event_id
            else:
                with self._lock:
                    self._rate_limited_dropped += 1
                return event.event_id
        with self._lock:
            self._prune_dedup_window()
            self._recent_event_ids.append((dedup_key, time.time()))
            self._total_published += 1
        if self._logger is not None:
            try:
                self._logger.log(event_type=event_type, data=event.to_dict(), severity=severity)
            except Exception as exc:
                logger.debug('event_bus: publish: %s', exc)
        with self._lock:
            self._counts[event_type] = self._counts.get(event_type, 0) + 1
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history.pop(0)
            self._cv.notify_all()
            subs = list(self._subscribers.get(event_type, []))
            wild_subs = list(self._wildcard_subscribers)
        delivered = self._dispatch(event, subs)
        delivered += self._dispatch(event, wild_subs)
        if delivered > 0:
            with self._lock:
                self._delivered += 1
        self._drain_rate_limited_queue()
        return event.event_id

    def subscribe(self, event_type: str, callback: Callable[[Event], None], filter_fn: Optional[Callable[[Event], bool]]=None) -> str:
        """Register a callback for a specific event type.

        Parameters
        ----------
        event_type : str
            The exact event type to subscribe to.
        callback : callable
            Function taking a single ``Event`` argument.
        filter_fn : callable or None
            Optional function ``(Event) -> bool``.  If provided, the callback
            is only invoked when the filter returns ``True``.

        Returns
        -------
        str
            A subscription ID that can be passed to ``unsubscribe()``.
        """
        subscription_id = _new_uuid()
        sub = _Subscription(subscription_id=subscription_id, event_type=event_type, callback=callback, filter_fn=filter_fn)
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(sub)
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by its ID.

        Parameters
        ----------
        subscription_id : str
            The ID returned by ``subscribe()`` or ``subscribe_all()``.

        Returns
        -------
        bool
            ``True`` if a subscription was removed, ``False`` otherwise.
        """
        with self._lock:
            for event_type in list(self._subscribers.keys()):
                subs = self._subscribers[event_type]
                for i, sub in enumerate(subs):
                    if sub.subscription_id == subscription_id:
                        subs.pop(i)
                        if not subs:
                            del self._subscribers[event_type]
                        return True
            for i, sub in enumerate(self._wildcard_subscribers):
                if sub.subscription_id == subscription_id:
                    self._wildcard_subscribers.pop(i)
                    return True
        return False

    def subscribe_all(self, callback: Callable[[Event], None]) -> str:
        """Subscribe to **all** events (for monitoring / debugging).

        Parameters
        ----------
        callback : callable
            Function taking a single ``Event`` argument.

        Returns
        -------
        str
            A subscription ID for ``unsubscribe()``.
        """
        subscription_id = _new_uuid()
        sub = _Subscription(subscription_id=subscription_id, event_type='*', callback=callback, filter_fn=None)
        with self._lock:
            self._wildcard_subscribers.append(sub)
        return subscription_id

    def get_history(self, event_type: Optional[str]=None, limit: int=50) -> List[Event]:
        """Return recent in-memory events, newest-first.

        Parameters
        ----------
        event_type : str or None
            If provided, only return events of this type.
        limit : int
            Maximum number of events to return (default: 50).

        Returns
        -------
        list[Event]
            Events in reverse chronological order (newest first).
        """
        with self._lock:
            events = list(self._history)
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        events.reverse()
        return events[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics about published events.

        Returns
        -------
        dict
            Keys: ``total_events``, ``by_type`` (dict of type -> count),
            ``history_size``, ``subscriber_count``.
        """
        with self._lock:
            total = sum(self._counts.values())
            by_type = dict(sorted(self._counts.items()))
            history_size = len(self._history)
            sub_count = sum((len(subs) for subs in self._subscribers.values())) + len(self._wildcard_subscribers)
        return {'total_events': total, 'by_type': by_type, 'history_size': history_size, 'subscriber_count': sub_count}

    def clear_history(self) -> None:
        """Clear the in-memory event history.

        Note: events already persisted to EventLogger (NDJSON) are NOT
        affected — they remain available via EventLogger.replay().
        """
        with self._lock:
            self._history.clear()
            self._counts.clear()

    def set_rate_limit(self, event_type: str, max_per_second: float=10.0) -> None:
        """Set a rate limit for *event_type*.

        Parameters
        ----------
        event_type : str
            The event type to rate-limit.
        max_per_second : float
            Maximum number of events of this type allowed per second.
        """
        with self._lock:
            self._rate_limits[event_type] = max_per_second

    def remove_rate_limit(self, event_type: str) -> None:
        """Remove the rate limit for *event_type* (if any)."""
        with self._lock:
            self._rate_limits.pop(event_type, None)
            self._rate_limit_buckets.pop(event_type, None)

    def replay_dead_letters(self) -> int:
        """Re-attempt delivery of all events in the dead letter queue.

        Returns
        -------
        int
            Number of events successfully re-delivered.
        """
        with self._lock:
            pending = list(self._dead_letter_queue)
            self._dead_letter_queue.clear()
        replayed = 0
        for event in pending:
            with self._lock:
                subs = list(self._subscribers.get(event.event_type, []))
                wild_subs = list(self._wildcard_subscribers)
            d = self._dispatch(event, subs)
            d += self._dispatch(event, wild_subs)
            if d > 0:
                replayed += 1
            else:
                with self._lock:
                    self._dead_letter_queue.append(event)
        return replayed

    def get_dead_letter_count(self) -> int:
        """Return the number of events currently in the dead letter queue."""
        with self._lock:
            return len(self._dead_letter_queue)

    def clear_dead_letters(self) -> None:
        """Remove all events from the dead letter queue."""
        with self._lock:
            self._dead_letter_queue.clear()

    def get_dedup_stats(self) -> Dict[str, int]:
        """Return deduplication drop counts by event type.

        Returns
        -------
        dict
            Keys are event type strings, values are the number of
            duplicate events dropped for that type.
        """
        with self._lock:
            return dict(self._dedup_dropped)

    def set_dedup_window(self, window_s: float) -> None:
        """Set the deduplication time window in seconds.

        Parameters
        ----------
        window_s : float
            Events with identical type + data arriving within this
            window are considered duplicates and dropped.
        """
        with self._lock:
            self._dedup_window_s = window_s
            self._prune_dedup_window()

    def get_reliability_stats(self) -> Dict[str, Any]:
        """Return aggregate reliability statistics.

        Returns
        -------
        dict
            Keys: ``total_published``, ``delivered``, ``dropped``,
            ``rate_limited``, ``dead_letter``, ``dedup_stats``.
        """
        with self._lock:
            return {'total_published': self._total_published, 'delivered': self._delivered, 'dropped': self._total_published - self._delivered, 'rate_limited': {'dropped': self._rate_limited_dropped, 'queued': self._rate_limited_queued_count}, 'dead_letter': len(self._dead_letter_queue), 'dedup_stats': dict(self._dedup_dropped)}

    def wait_for(self, event_type: str, timeout: float=30.0) -> Optional[Event]:
        """Block the calling thread until an event of *event_type* is published.

        Parameters
        ----------
        event_type : str
            The event type to wait for.
        timeout : float
            Maximum seconds to wait (default: 30.0).  ``0`` or negative means
            poll once and return immediately.

        Returns
        -------
        Event or None
            The matching Event, or ``None`` if the timeout expired.
        """

        def _latest_of_type(typ: str) -> Optional[Event]:
            for ev in reversed(self._history):
                if ev.event_type == typ:
                    return ev
            return None
        with self._cv:
            if timeout <= 0:
                return _latest_of_type(event_type)
            found = _latest_of_type(event_type)
            if found is not None:
                return found
            self._cv.wait(timeout=timeout)
            return _latest_of_type(event_type)

    @staticmethod
    def _dispatch(event: Event, subscriptions: List[_Subscription]) -> int:
        """Invoke callbacks for *subscriptions* that pass the filter.

        Returns
        -------
        int
            Number of subscribers that successfully handled the event.
        """
        delivered = 0
        for sub in subscriptions:
            try:
                if sub.filter_fn is None or sub.filter_fn(event):
                    sub.callback(event)
                    delivered += 1
            except Exception:
                bus = EventBus()
                try:
                    bus._dead_letter_queue.append(event)
                except Exception as exc:
                    logger.debug('event_bus: _dispatch: %s', exc)
        return delivered

    def _is_rate_limited(self, event_type: str) -> bool:
        """Check if *event_type* is currently rate-limited.

        Returns True if the event type has a rate limit and the number of
        events in the sliding 1-second window >= max_per_second.
        """
        with self._lock:
            if event_type not in self._rate_limits:
                return False
            max_per_second = self._rate_limits[event_type]
            now = time.time()
            bucket = self._rate_limit_buckets[event_type]
            cutoff = now - 1.0
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= max_per_second:
                return True
            bucket.append(now)
            return False

    def _is_duplicate(self, dedup_key: Tuple[str, str]) -> bool:
        """Check if an event with this (type, canonical-data) key was
        recently published within the dedup window."""
        with self._lock:
            now = time.time()
            for key, ts in self._recent_event_ids:
                if key == dedup_key and now - ts <= self._dedup_window_s:
                    return True
            return False

    def _prune_dedup_window(self) -> None:
        """Remove entries from the dedup window that have expired."""
        now = time.time()
        while self._recent_event_ids:
            key, ts = self._recent_event_ids[0]
            if now - ts > self._dedup_window_s:
                self._recent_event_ids.popleft()
            else:
                break

    def _drain_rate_limited_queue(self) -> None:
        """Re-attempt delivery of rate-limited events that are no
        longer rate-limited.  Higher-priority events are drained first."""
        with self._lock:
            if not self._rate_limited_queue:
                return
            self._rate_limited_queue.sort(key=lambda e: (e.priority, e.timestamp))
            still_limited: List[Event] = []
            to_dispatch: List[Event] = []
            for event in self._rate_limited_queue:
                if event.event_type not in self._rate_limits:
                    to_dispatch.append(event)
                else:
                    max_per_second = self._rate_limits[event.event_type]
                    now = time.time()
                    bucket = self._rate_limit_buckets[event.event_type]
                    cutoff = now - 1.0
                    while bucket and bucket[0] < cutoff:
                        bucket.pop(0)
                    if len(bucket) < max_per_second:
                        bucket.append(now)
                        to_dispatch.append(event)
                    else:
                        still_limited.append(event)
            self._rate_limited_queue = still_limited
        for event in to_dispatch:
            with self._lock:
                subs = list(self._subscribers.get(event.event_type, []))
                wild_subs = list(self._wildcard_subscribers)
            d = self._dispatch(event, subs)
            d += self._dispatch(event, wild_subs)
            if d > 0:
                with self._lock:
                    self._delivered += 1

    @staticmethod
    def reset() -> None:
        """Reset the singleton (for testing / teardown)."""
        with EventBus._instance_lock:
            EventBus._instance = None
_module_bus: Optional[EventBus] = None
_module_bus_lock = threading.Lock()

def get_bus() -> EventBus:
    """Return the application-wide EventBus singleton.

    This is the recommended way to access the bus from any module::

        from event_bus import get_bus
import logging

logger = logging.getLogger(__name__)

        bus = get_bus()
        bus.publish("my.event", {"key": "value"})
    """
    global _module_bus
    with _module_bus_lock:
        if _module_bus is None:
            _module_bus = EventBus()
        return _module_bus

def publish(event_type: str, data: Optional[Dict[str, Any]]=None, source: Optional[str]=None, severity: str='info', priority: int=PRIORITY_MEDIUM) -> str:
    """Shorthand for ``get_bus().publish(...)``."""
    return get_bus().publish(event_type=event_type, data=data, source=source, severity=severity, priority=priority)

def subscribe(event_type: str, callback: Callable[[Event], None], filter_fn: Optional[Callable[[Event], bool]]=None) -> str:
    """Shorthand for ``get_bus().subscribe(...)``."""
    return get_bus().subscribe(event_type=event_type, callback=callback, filter_fn=filter_fn)

def on_startup(callback: Callable[[Event], None]) -> str:
    """Shorthand for subscribing to ``EVENT_SYSTEM_STARTUP``.

    Parameters
    ----------
    callback : callable
        Function taking a single ``Event`` argument.

    Returns
    -------
    str
        A subscription ID for ``unsubscribe()``.
    """
    return get_bus().subscribe(event_type=EVENT_SYSTEM_STARTUP, callback=callback)
__all__ = ['Event', 'EventBus', 'EventPriority', 'PRIORITY_CRITICAL', 'PRIORITY_HIGH', 'PRIORITY_MEDIUM', 'PRIORITY_LOW', 'EVENT_TASK_STARTED', 'EVENT_TASK_COMPLETED', 'EVENT_TASK_FAILED', 'EVENT_POLICY_BLOCKED', 'EVENT_MEMORY_UPDATED', 'EVENT_RESOURCE_WARNING', 'EVENT_RECOVERY_TRIGGERED', 'EVENT_REFLECTION_COMPLETED', 'EVENT_PLAN_CREATED', 'EVENT_PLAN_STEP_STARTED', 'EVENT_PLAN_STEP_COMPLETED', 'EVENT_PLAN_STEP_FAILED', 'EVENT_PLAN_COMPLETED', 'EVENT_PLAN_FAILED', 'EVENT_OBSERVATION_CYCLE', 'EVENT_SYSTEM_STARTUP', 'EVENT_SYSTEM_SHUTDOWN', 'EVENT_BROWSER_CREATED', 'EVENT_BROWSER_CLOSED', 'get_bus', 'publish', 'subscribe', 'on_startup']