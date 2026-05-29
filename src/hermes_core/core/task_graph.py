"""
task_graph.py — Task DAG engine for Hermes Core.

Provides data structures (TaskNode, TaskGraph) and a singleton
TaskGraphEngine for managing directed acyclic graphs of task nodes with
dependency resolution, parallel execution batches, retry policies,
checkpointing via EventLogger, and rollback support.

Standard library only: uuid, datetime, json, copy, time, threading.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:
    from .event_logger import get_logger
    from .exceptions import TaskExecutionError
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from event_logger import get_logger
    from exceptions import TaskExecutionError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TASK_GRAPH_EVENT_TYPE = "task_graph.checkpoint"
_VALID_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "skipped", "rolled_back"}
)
_VALID_GRAPH_STATUSES = frozenset({"pending", "running", "completed", "failed", "paused"})

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TaskNode:
    """A single node in a task dependency graph.

    Attributes
    ----------
    node_id : str
        UUID identifying this node.
    action : str
        What action to perform (e.g. ``"web_scrape"``, ``"file_read"``).
    params : dict
        Parameters for the action.
    depends_on : list[str]
        node_ids that must complete before this node runs.
    retry_policy : dict or None
        Optional retry config::

            {"max_retries": 3, "backoff": "exponential", "delay": 2}

        Supported backoff modes: ``"fixed"``, ``"linear"``, ``"exponential"``.
    timeout : int
        Maximum execution time in seconds (default 300).
    validation : str or None
        Optional validation expression string (interpreted by executor).
    status : str
        One of ``pending|running|completed|failed|skipped|rolled_back``.
    result : dict or None
        Output produced by the executor.
    error : str or None
        Error message if the node failed.
    started_at : str or None
        ISO-8601 timestamp when execution began.
    completed_at : str or None
        ISO-8601 timestamp when execution finished.
    retry_count : int
        How many times this node has been retried.
    """

    node_id: str  # UUID
    action: str
    params: dict
    depends_on: list[str]
    retry_policy: Optional[dict] = None
    timeout: int = 300
    validation: Optional[str] = None
    status: str = "pending"
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_count: int = 0

    def __post_init__(self) -> None:
        if self.retry_policy is None:
            self.retry_policy = {}
        if self.params is None:
            self.params = {}


@dataclass
class TaskGraph:
    """A directed acyclic graph of TaskNodes.

    Attributes
    ----------
    graph_id : str
        UUID identifying this graph.
    name : str
        Human-readable label.
    nodes : dict[str, TaskNode]
        Map of node_id -> TaskNode.
    created_at : str
        ISO-8601 creation timestamp.
    status : str
        One of ``pending|running|completed|failed|paused``.
    current_node : str or None
        node_id of the node currently executing (if any).
    checkpoint_id : str or None
        event_id of the most recent checkpoint written to the event log.
    """

    graph_id: str
    name: str
    nodes: dict[str, TaskNode]
    created_at: str
    status: str
    current_node: Optional[str] = None
    checkpoint_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a fresh UUID4 hex string."""
    return str(_uuid.uuid4())


def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Singleton machinery
# ---------------------------------------------------------------------------

_engine_instance: Optional["TaskGraphEngine"] = None
_engine_lock = threading.Lock()

# ---------------------------------------------------------------------------
# TaskGraphEngine
# ---------------------------------------------------------------------------


class TaskGraphEngine:
    """Singleton engine that manages task graph lifecycle.

    All graph and node state is held in-memory and persisted to the
    ``EventLogger`` append-only event stream for crash recovery.

    Thread-safe via an internal ``threading.Lock``.
    """

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._graphs: dict[str, TaskGraph] = {}
        self._lock = threading.RLock()  # reentrant — needed because
                                         # _update_graph_status calls
                                         # check_graph_complete while the
                                         # lock is already held
        self._logger = get_logger()
        self._initialized = True

    # -- Graph lifecycle ---------------------------------------------------------

    def create_graph(self, name: str, nodes: list[TaskNode]) -> str:
        """Create a new task graph from a list of *nodes*.

        Validates the nodes (type checks, cycle detection, dependency
        reference integrity) and raises ``ValueError`` on any violation.

        Returns the new *graph_id* (UUID).
        """
        errors = validate_nodes(nodes)
        if errors:
            raise ValueError(f"Invalid nodes: {'; '.join(errors)}")

        if detect_cycle(nodes):
            raise ValueError("Graph contains a cycle — node dependencies form a loop")

        graph_id = _new_uuid()
        graph = TaskGraph(
            graph_id=graph_id,
            name=name,
            nodes={n.node_id: copy.deepcopy(n) for n in nodes},
            created_at=_timestamp(),
            status="pending",
        )
        with self._lock:
            self._graphs[graph_id] = graph
        self.save_checkpoint(graph_id)
        return graph_id

    def add_node(self, graph_id: str, node: TaskNode) -> None:
        """Add a single *node* to an existing graph.

        Raises ``KeyError`` if the graph does not exist,
        ``ValueError`` if a node with the same ``node_id`` already exists.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            if node.node_id in graph.nodes:
                raise ValueError(
                    f"Node {node.node_id!r} already exists in graph {graph_id!r}"
                )
            graph.nodes[node.node_id] = copy.deepcopy(node)

    def get_graph(self, graph_id: str) -> TaskGraph:
        """Return a **deep copy** of the graph (safe for external mutation)."""
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            return copy.deepcopy(graph)

    # -- Dependency resolution --------------------------------------------------

    def get_ready_nodes(self, graph_id: str) -> list[TaskNode]:
        """Return all nodes whose dependencies are all ``completed``.

        Only nodes with ``status == "pending"`` are considered.  Returns
        deep copies so the caller can inspect them without holding the lock.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            ready: list[TaskNode] = []
            for node in graph.nodes.values():
                if node.status != "pending":
                    continue
                deps_met = all(
                    graph.nodes[d].status == "completed"
                    for d in node.depends_on
                    if d in graph.nodes
                )
                if deps_met:
                    ready.append(copy.deepcopy(node))
            return ready

    def get_dependency_order(self, graph_id: str) -> list[list[TaskNode]]:
        """Topological sort returning levels (parallel batches).

        Uses Kahn's algorithm.  Each inner list contains nodes that can
        execute in parallel; each outer list is a sequential phase.

        Returns deep copies.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")

            # Build in-degree map and adjacency list
            in_degree: dict[str, int] = {}
            adjacency: dict[str, list[str]] = {}
            for nid, node in graph.nodes.items():
                in_degree.setdefault(nid, 0)
                adjacency.setdefault(nid, [])
                for dep in node.depends_on:
                    if dep in graph.nodes:
                        adjacency.setdefault(dep, []).append(nid)
                        in_degree[nid] = in_degree.get(nid, 0) + 1

            # Kahn's algorithm
            queue = [nid for nid, deg in in_degree.items() if deg == 0]
            levels: list[list[TaskNode]] = []
            visited: set[str] = set()

            while queue:
                current_level: list[TaskNode] = []
                next_queue: list[str] = []
                for nid in queue:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    current_level.append(copy.deepcopy(graph.nodes[nid]))
                    for neighbor in adjacency.get(nid, []):
                        in_degree[neighbor] -= 1
                        if in_degree[neighbor] == 0:
                            next_queue.append(neighbor)
                if current_level:
                    levels.append(current_level)
                queue = next_queue

            return levels

    # -- Node execution ---------------------------------------------------------

    def execute_node(
        self,
        graph_id: str,
        node_id: str,
        executor_fn: Callable[[TaskNode], Any],
    ) -> dict:
        """Execute a single node with retry policy support.

        *executor_fn* receives a **deep copy** of the node (safe to mutate)
        and must return a JSON-serialisable value.

        The engine:
        1. Marks the node ``running`` and writes a checkpoint.
        2. Calls *executor_fn* (up to ``max_retries + 1`` attempts).
        3. On success, marks ``completed`` and stores the result.
        4. On failure, marks ``failed`` and stores the error message.
        5. Writes a final checkpoint.
        6. Updates the overall graph status.

        Returns the node's result dict, or ``{"error": ...}`` on failure.
        """
        # Set running state (under lock)
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            node = graph.nodes.get(node_id)
            if node is None:
                raise KeyError(f"Node {node_id!r} not found in graph {graph_id!r}")

            node.status = "running"
            node.started_at = _timestamp()
            graph.current_node = node_id
            graph.checkpoint_id = self._save_node_event(
                graph_id, node_id, "running"
            )

            # Deep copy for executor (safe to use outside lock)
            node_copy = copy.deepcopy(node)

        # Execute (outside lock to avoid deadlocks with callbacks)
        max_retries = node_copy.retry_policy.get("max_retries", 0)
        backoff = node_copy.retry_policy.get("backoff", "fixed")
        delay = node_copy.retry_policy.get("delay", 2)

        last_error: Optional[str] = None
        result: Any = None

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    sleep_time = self._calculate_backoff(backoff, delay, attempt)
                    time.sleep(sleep_time)

                result = executor_fn(node_copy)
                last_error = None  # successful — clear any prior error
                break
            except Exception as exc:
                last_error = str(exc)
                # Update retry count in the in-memory node
                with self._lock:
                    g = self._graphs.get(graph_id)
                    if g and node_id in g.nodes:
                        g.nodes[node_id].retry_count = attempt + 1

        # Finalise (under lock)
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                return {"status": "failed", "error": "Graph deleted during execution"}

            node = graph.nodes.get(node_id)
            if node is None:
                return {"status": "failed", "error": "Node deleted during execution"}

            node.completed_at = _timestamp()
            graph.current_node = None

            if last_error is None:
                node.status = "completed"
                node.result = (
                    result
                    if isinstance(result, dict)
                    else {"value": result}
                )
            else:
                node.status = "failed"
                node.error = last_error

            graph.checkpoint_id = self._save_node_event(
                graph_id,
                node_id,
                node.status,
                result=node.result,
                error=node.error,
            )

            # Update overall graph status
            self._update_graph_status(graph)

        return node.result if last_error is None else {"error": last_error}

    def execute_graph(
        self,
        graph_id: str,
        executor_fn: Callable[[TaskNode], Any],
        parallel: bool = True,
    ) -> list[list[TaskNode]]:
        """Execute all ready nodes, returning batches for parallel execution.

        Parameters
        ----------
        graph_id : str
            The graph to execute.
        executor_fn : Callable
            Function that receives a ``TaskNode`` and returns a result.
        parallel : bool
            If ``True`` (default), returns ready nodes as a single batch so
            the caller can execute them concurrently.  If ``False``, runs
            nodes sequentially via ``execute_node``.

        Returns
        -------
        list[list[TaskNode]]
            Batches of nodes (deep copies).  Empty list if nothing is ready.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            if graph.status == "pending":
                graph.status = "running"
                self.save_checkpoint(graph_id)

        if parallel:
            ready = self.get_ready_nodes(graph_id)
            return [ready] if ready else []
        else:
            batches = self.get_dependency_order(graph_id)
            for batch in batches:
                for node in batch:
                    self.execute_node(graph_id, node.node_id, executor_fn)
            return batches

    # -- Graph status checks ----------------------------------------------------

    def check_graph_complete(self, graph_id: str) -> bool:
        """Return ``True`` when every node has a terminal status.

        Terminal statuses: ``completed``, ``failed``, ``skipped``.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            terminal = {"completed", "failed", "skipped"}
            return all(node.status in terminal for node in graph.nodes.values())

    def get_graph_status(self, graph_id: str) -> dict:
        """Return a detailed status summary dict with status counts."""
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            counts: dict[str, int] = {}
            for node in graph.nodes.values():
                counts[node.status] = counts.get(node.status, 0) + 1
            return {
                "graph_id": graph.graph_id,
                "name": graph.name,
                "status": graph.status,
                "total_nodes": len(graph.nodes),
                "counts": counts,
                "current_node": graph.current_node,
                "checkpoint_id": graph.checkpoint_id,
                "created_at": graph.created_at,
            }

    # -- Pause / Resume ---------------------------------------------------------

    def pause_graph(self, graph_id: str) -> None:
        """Pause a running graph (sets status to ``paused``)."""
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            graph.status = "paused"
            self.save_checkpoint(graph_id)

    def resume_graph(self, graph_id: str) -> None:
        """Resume a paused graph (sets status back to ``running``)."""
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            graph.status = "running"
            self.save_checkpoint(graph_id)

    # -- Rollback ---------------------------------------------------------------

    def rollback_node(self, graph_id: str, node_id: str) -> None:
        """Mark a node for re-execution and recursively reset dependents.

        The target node is set to ``rolled_back`` status, and all nodes
        that (transitively) depend on it are reset to ``pending`` so they
        will be re-executed.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")
            node = graph.nodes.get(node_id)
            if node is None:
                raise KeyError(f"Node {node_id!r} not found")

            node.status = "rolled_back"
            node.result = None
            node.error = None
            node.completed_at = None
            node.started_at = None

            # Recursively reset dependents
            self._mark_dependents_pending(graph, node_id)

            self.save_checkpoint(graph_id)

    def _mark_dependents_pending(self, graph: TaskGraph, node_id: str) -> None:
        """Recursively mark all nodes that depend on *node_id* as pending."""
        for n in graph.nodes.values():
            if node_id in n.depends_on and n.status in (
                "completed",
                "running",
                "failed",
            ):
                n.status = "pending"
                n.result = None
                n.error = None
                n.completed_at = None
                n.started_at = None
                self._mark_dependents_pending(graph, n.node_id)

    # -- Checkpointing ----------------------------------------------------------

    def save_checkpoint(self, graph_id: str) -> str:
        """Persist the full graph state to the event log.

        Stores: graph_id, graph_name, graph_status, current_node, and
        a serialised dict of all node states (status, result, error,
        timestamps, retry_count).

        Returns the checkpoint *event_id*.
        """
        with self._lock:
            graph = self._graphs.get(graph_id)
            if graph is None:
                raise KeyError(f"Graph {graph_id!r} not found")

            node_states: dict[str, dict[str, Any]] = {}
            for nid, node in graph.nodes.items():
                node_states[nid] = {
                    "status": node.status,
                    "result": node.result,
                    "error": node.error,
                    "started_at": node.started_at,
                    "completed_at": node.completed_at,
                    "retry_count": node.retry_count,
                }

            data = {
                "graph_id": graph.graph_id,
                "graph_name": graph.name,
                "graph_status": graph.status,
                "current_node": graph.current_node,
                "node_states": node_states,
            }

            event_id = self._logger.log(
                event_type=_TASK_GRAPH_EVENT_TYPE,
                data=data,
                severity="info",
            )
            graph.checkpoint_id = event_id
            return event_id

    def load_checkpoint(self, graph_id: str) -> dict:
        """Restore graph state from the event log.

        Replays all checkpoint events for *graph_id* (newest-first) and
        returns the latest reconstructed state.  Does **not** mutate the
        in-memory graph.

        Returns an empty dict if no checkpoints are found.
        """
        events = self._logger.replay(
            event_types=[_TASK_GRAPH_EVENT_TYPE],
            limit=10000,
        )

        # Filter to this graph_id (events are already newest-first)
        graph_events = [
            e
            for e in events
            if e.get("data", {}).get("graph_id") == graph_id
        ]

        if not graph_events:
            return {}

        latest = graph_events[0]
        data = latest.get("data", {})
        return {
            "graph_id": data.get("graph_id"),
            "graph_name": data.get("graph_name"),
            "graph_status": data.get("graph_status"),
            "current_node": data.get("current_node"),
            "node_states": data.get("node_states", {}),
        }

    # -- Listing ----------------------------------------------------------------

    def list_graphs(self) -> list[str]:
        """Return a list of all known graph IDs."""
        with self._lock:
            return list(self._graphs.keys())

    # -- Internal helpers -------------------------------------------------------

    def _save_node_event(
        self,
        graph_id: str,
        node_id: str,
        status: str,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> str:
        """Log a per-node checkpoint event to the event log."""
        data = {
            "graph_id": graph_id,
            "node_id": node_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": _timestamp(),
        }
        return self._logger.log(
            event_type=f"{_TASK_GRAPH_EVENT_TYPE}.node",
            data=data,
            severity="error" if status == "failed" else "info",
        )

    def _update_graph_status(self, graph: TaskGraph) -> None:
        """Recompute the overall graph status from node states.

        If all nodes are terminal, the graph is ``completed`` (or ``failed``
        if any node failed).
        """
        if self.check_graph_complete(graph.graph_id):
            has_failure = any(n.status == "failed" for n in graph.nodes.values())
            graph.status = "failed" if has_failure else "completed"

    @staticmethod
    def _calculate_backoff(backoff_type: str, delay: float, attempt: int) -> float:
        """Calculate the sleep duration before a retry attempt.

        Supports ``"fixed"``, ``"linear"``, and ``"exponential"`` modes.
        """
        if backoff_type == "exponential":
            return delay * (2 ** (attempt - 1))
        elif backoff_type == "linear":
            return delay * attempt
        else:  # fixed
            return delay


# ---------------------------------------------------------------------------
# Module-level helpers (cycle detection & validation)
# ---------------------------------------------------------------------------


def detect_cycle(nodes: list[TaskNode]) -> bool:
    """Detect whether the given nodes contain a dependency cycle.

    Uses DFS with node colouring (white/grey/black).
    Returns ``True`` if a cycle exists, ``False`` otherwise.
    """
    # Build adjacency: dep -> [node_id]
    adj: dict[str, list[str]] = {}
    for node in nodes:
        adj.setdefault(node.node_id, [])
        for dep in node.depends_on:
            adj.setdefault(dep, [])
            adj[dep].append(node.node_id)

    # 0 = unvisited, 1 = in-progress, 2 = done
    colour: dict[str, int] = {nid: 0 for nid in adj}

    def dfs(nid: str) -> bool:
        if colour[nid] == 1:
            return True  # back edge → cycle
        if colour[nid] == 2:
            return False
        colour[nid] = 1
        for neighbour in adj.get(nid, []):
            if neighbour in colour and dfs(neighbour):
                return True
        colour[nid] = 2
        return False

    for nid in list(adj.keys()):
        if colour[nid] == 0:
            if dfs(nid):
                return True
    return False


def validate_nodes(nodes: list[TaskNode]) -> list[str]:
    """Validate a list of ``TaskNode`` instances.

    Checks performed:
    - Non-empty node list.
    - Every node has a non-empty ``node_id`` (no duplicates).
    - Every node has a non-empty ``action``.
    - No node depends on itself.
    - ``status`` is one of the valid values.
    - ``retry_policy`` fields are well-typed.
    - ``timeout`` is a positive int.
    - ``params`` is a dict.
    - All ``depends_on`` references resolve to existing node IDs.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not nodes:
        errors.append("Node list is empty")
        return errors

    node_ids: set[str] = set()
    for idx, node in enumerate(nodes):
        # node_id
        if not node.node_id:
            errors.append(f"Node at index {idx} has empty node_id")
        else:
            if node.node_id in node_ids:
                errors.append(f"Duplicate node_id: {node.node_id!r}")
            node_ids.add(node.node_id)

        # action
        if not node.action:
            errors.append(f"Node {node.node_id!r} has empty action")

        # self-dependency
        for dep in node.depends_on:
            if dep == node.node_id:
                errors.append(f"Node {node.node_id!r} depends on itself")

        # status
        if node.status not in _VALID_STATUSES:
            errors.append(
                f"Node {node.node_id!r} has invalid status {node.status!r}"
            )

        # retry_policy
        if node.retry_policy:
            if "max_retries" in node.retry_policy:
                if not isinstance(node.retry_policy["max_retries"], int):
                    errors.append(
                        f"Node {node.node_id!r}: retry_policy.max_retries must be int"
                    )
            if "backoff" in node.retry_policy:
                valid_backoffs = {"fixed", "linear", "exponential"}
                if node.retry_policy["backoff"] not in valid_backoffs:
                    errors.append(
                        f"Node {node.node_id!r}: retry_policy.backoff must be one of "
                        f"{sorted(valid_backoffs)}"
                    )
            if "delay" in node.retry_policy:
                if not isinstance(node.retry_policy["delay"], (int, float)):
                    errors.append(
                        f"Node {node.node_id!r}: retry_policy.delay must be a number"
                    )

        # timeout
        if not isinstance(node.timeout, int) or node.timeout <= 0:
            errors.append(f"Node {node.node_id!r}: timeout must be a positive int")

        # params
        if not isinstance(node.params, dict):
            errors.append(f"Node {node.node_id!r}: params must be a dict")

    # Cross-reference depends_on
    for node in nodes:
        for dep in node.depends_on:
            if dep not in node_ids:
                errors.append(
                    f"Node {node.node_id!r} depends on unknown node {dep!r}"
                )

    return errors


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def create_task_graph(name: str, nodes: list[TaskNode]) -> str:
    """Create a task graph and return its *graph_id*.

    Convenience wrapper around ``TaskGraphEngine.create_graph()``.
    """
    return get_engine().create_graph(name, nodes)


def get_engine() -> TaskGraphEngine:
    """Return the application-wide ``TaskGraphEngine`` singleton.

    Lazily initialised on first call.
    """
    global _engine_instance
    with _engine_lock:
        if _engine_instance is None:
            _engine_instance = TaskGraphEngine()
        return _engine_instance
