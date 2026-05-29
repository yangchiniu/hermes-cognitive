"""
kernel.py — Agent Orchestrator Kernel for Hermes Core.

Ties all subsystems together: WorldModel, ToolRegistry, PolicyEngine,
TaskGraph, RuntimeSupervisor, ReflectionEngine, ExperienceManager,
MemoryManager, StateManager, RecoveryManager, EventLogger, SchemaManager.

Provides pre-task / post-task pipelines, planning, health checks,
self-diagnosis, and graceful startup/shutdown orchestration.

Standard library only: threading, time, datetime, uuid.
Version: 0.1.0
"""
from __future__ import annotations
import sys
import threading
import time
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

__version__ = '0.1.0'
_KERNEL_START_EVENT = 'kernel.start'
_KERNEL_SHUTDOWN_EVENT = 'kernel.shutdown'
_KERNEL_TASK_BEFORE_EVENT = 'kernel.task.before'
_KERNEL_TASK_AFTER_EVENT = 'kernel.task.after'
_IMPORT_LOCK = threading.Lock()
_import_cache: Dict[str, Any] = {}


def _get_mod(name: str) -> Any:
    """Import and cache a hermes core submodule by name.

    Tries ``importlib.import_module('hermes.core.<name>')`` first (package
    import), then falls back to a bare ``import <name>`` via a temporary
    sys.path insertion of ``~/.hermes/core/``.  Gracefully degrades to
    ``None`` on failure.

    Thread-safe via ``_IMPORT_LOCK``.
    """
    if name not in _import_cache:
        with _IMPORT_LOCK:
            if name not in _import_cache:
                import importlib as _ilib

                # Attempt 1: subpackage of hermes.core
                try:
                    _import_cache[name] = _ilib.import_module(
                        f"hermes.core.{name}"
                    )
                    return _import_cache[name]
                except ImportError:
                    pass

                # Attempt 2: standalone module with temporary sys.path
                _core_dir = str(
                    Path(__file__).resolve().parent
                )
                _added = False
                try:
                    if _core_dir not in sys.path:
                        sys.path.insert(0, _core_dir)
                        _added = True
                    _import_cache[name] = _ilib.import_module(name)
                except ImportError as exc:
                    logger.debug("kernel: import %s failed: %s", name, exc)
                    _import_cache[name] = None
                finally:
                    if _added:
                        sys.path.remove(_core_dir)
    return _import_cache[name]

def _new_uuid() -> str:
    """Return a fresh UUID4 hex string."""
    return str(_uuid.uuid4())

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()
_instance: Optional['AgentKernel'] = None
_instance_lock = threading.RLock()

class AgentKernel:
    """Agent Orchestrator Kernel — ties all Hermes Core subsystems together.

    Implements the singleton pattern.  Use ``get_kernel()`` to obtain the
    application-wide instance.  All heavy initialisation is deferred to
    ``initialize()``.

    Usage
    -----
    >>> kernel = get_kernel()
    >>> report = kernel.initialize()
    >>> result = kernel.before_task("terminal_exec", "List directory", {})
    >>> # ... execute the task ...
    >>> kernel.after_task(result["task_id"], {"success": True})
    >>> status = kernel.get_status()
    >>> kernel.shutdown()
    """
    VERSION = __version__

    def __new__(cls) -> 'AgentKernel':
        with _instance_lock:
            if _instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                _instance_holder = obj
                globals()['_instance'] = obj
            return globals()['_instance']

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None


    def __init__(self) -> None:
        """Lazy init — does NOT import any subsystem modules."""
        if getattr(self, '_initialized', False):
            return
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._schema_mgr: Any = None
        self._event_logger: Any = None
        self._world_model: Any = None
        self._tool_registry: Any = None
        self._policy_engine: Any = None
        self._task_graph_engine: Any = None
        self._runtime_supervisor: Any = None
        self._reflection_engine: Any = None
        self._experience_manager: Any = None
        self._memory_manager: Any = None
        self._state_manager: Any = None
        self._recovery_manager: Any = None
        self._started_at: Optional[str] = None
        self._initialized_flag = False
        self._initialized = True

    def initialize(self) -> Dict[str, Any]:
        """Initialise all subsystems in dependency order (idempotent).

        Startup sequence:
          1. SchemaManager — create all databases
          2. EventLogger — start event log
          3. WorldModel — create initial snapshot
          4. ToolRegistry — register defaults
          5. PolicyEngine — load policy
          6. TaskGraphEngine — init
          7. RuntimeSupervisor — start monitoring
          8. ReflectionEngine — init
          9. ExperienceManager — init
          10. MemoryManager — init
          11. StateManager — capture initial state
          12. RecoveryManager — check health

        Returns
        -------
        dict
            Initialization report with status of each subsystem.
        """
        with self._init_lock:
            if self._initialized_flag:
                return self._build_init_report(all_ok=True, already=True)
            report: Dict[str, Any] = {}
            all_ok = True
            start_ts = _timestamp()
            self._started_at = start_ts
            try:
                mod = _get_mod('db_schema')
                mgr = mod.get_manager()
                mgr.initialize_all()
                self._schema_mgr = mgr
                report['schema_manager'] = 'ok'
            except Exception as exc:
                report['schema_manager'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('event_logger')
                logger = mod.get_logger()
                self._event_logger = logger
                report['event_logger'] = 'ok'
            except Exception as exc:
                report['event_logger'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('world_model')
                wm = mod.get_world_model()
                wm.snapshot()
                self._world_model = wm
                report['world_model'] = 'ok'
            except Exception as exc:
                report['world_model'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('tool_registry')
                tr = mod.ToolRegistry()
                tr.register_defaults()
                self._tool_registry = tr
                report['tool_registry'] = 'ok'
            except Exception as exc:
                report['tool_registry'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('policy_engine')
                pe = mod.get_policy_engine()
                self._policy_engine = pe
                report['policy_engine'] = 'ok'
            except Exception as exc:
                report['policy_engine'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('task_graph')
                tge = mod.get_engine()
                self._task_graph_engine = tge
                report['task_graph_engine'] = 'ok'
            except Exception as exc:
                report['task_graph_engine'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('runtime_supervisor')
                sup = mod.get_supervisor()
                sup.start()
                self._runtime_supervisor = sup
                report['runtime_supervisor'] = 'ok'
            except Exception as exc:
                report['runtime_supervisor'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('reflection_engine')
                refl = mod.get_reflection()
                self._reflection_engine = refl
                report['reflection_engine'] = 'ok'
            except Exception as exc:
                report['reflection_engine'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('experience_manager')
                em = mod.get_experience()
                self._experience_manager = em
                report['experience_manager'] = 'ok'
            except Exception as exc:
                report['experience_manager'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('memory_manager')
                mm = mod.MemoryManager()
                self._memory_manager = mm
                report['memory_manager'] = 'ok'
            except Exception as exc:
                report['memory_manager'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('state_manager')
                sm = mod.get_state_manager()
                sm.capture_state(description='kernel initial state')
                self._state_manager = sm
                report['state_manager'] = 'ok'
            except Exception as exc:
                report['state_manager'] = f'FAIL: {exc}'
                all_ok = False
            try:
                mod = _get_mod('recovery_manager')
                rm = mod.RecoveryManager()
                health = rm.check_health()
                self._recovery_manager = rm
                report['recovery_manager'] = 'ok'
                report['recovery_health'] = health.get('healthy', False)
            except Exception as exc:
                report['recovery_manager'] = f'FAIL: {exc}'
                all_ok = False
            try:
                if self._event_logger is not None:
                    self._event_logger.log(_KERNEL_START_EVENT, {'version': self.VERSION, 'report': report}, severity='info')
            except Exception as exc:
                logger.debug('kernel: initialize: %s', exc)
            self._initialized_flag = all_ok
            return self._build_init_report(all_ok, already=False, extra=report)

    def _build_init_report(self, all_ok: bool, already: bool=False, extra: Optional[Dict]=None) -> Dict[str, Any]:
        """Build a standardised initialisation report dict."""
        result: Dict[str, Any] = {'success': all_ok, 'already_initialized': already, 'version': self.VERSION, 'started_at': self._started_at}
        if extra:
            result['subsystems'] = extra
        return result

    def is_initialized(self) -> bool:
        """Return ``True`` if ``initialize()`` has completed successfully."""
        return self._initialized_flag

    def shutdown(self) -> None:
        """Gracefully shut down all subsystems."""
        with self._lock:
            if not self._initialized_flag:
                return
            try:
                if self._runtime_supervisor is not None:
                    self._runtime_supervisor.stop()
            except Exception as exc:
                logger.debug('kernel: shutdown: %s', exc)
            try:
                if self._state_manager is not None:
                    self._state_manager.capture_state(description='kernel shutdown')
            except Exception as exc:
                logger.debug('kernel: shutdown: %s', exc)
            try:
                if self._event_logger is not None:
                    self._event_logger.log(_KERNEL_SHUTDOWN_EVENT, {'version': self.VERSION, 'uptime_s': self.uptime_seconds()}, severity='info')
            except Exception as exc:
                logger.debug('kernel: shutdown: %s', exc)
            try:
                if self._event_logger is not None:
                    self._event_logger.close()
            except Exception as exc:
                logger.debug('kernel: shutdown: %s', exc)
            try:
                if self._schema_mgr is not None:
                    self._schema_mgr.close_all()
            except Exception as exc:
                logger.debug('kernel: shutdown: %s', exc)
            self._initialized_flag = False

    def uptime_seconds(self) -> float:
        """Return the number of seconds since the kernel was started."""
        if self._started_at is None:
            return 0.0
        try:
            started = datetime.fromisoformat(self._started_at)
            return (datetime.now(timezone.utc) - started).total_seconds()
        except (ValueError, TypeError):
            return 0.0

    def get_status(self) -> Dict[str, Any]:
        """Return a comprehensive status dict for ALL subsystems.

        Returns
        -------
        dict
            Keys: ``kernel``, ``world_model``, ``tool_registry``,
            ``policy_engine``, ``runtime_supervisor``, ``memory_manager``,
            ``recovery``, plus any other subsystem that can provide a summary.
        """
        status: Dict[str, Any] = {'kernel': {'version': self.VERSION, 'uptime_s': round(self.uptime_seconds(), 2), 'initialized': self._initialized_flag, 'started_at': self._started_at}, 'world_model': {}, 'tool_registry': {}, 'policy_engine': {}, 'runtime_supervisor': {}, 'memory_manager': {}, 'recovery': {}}
        try:
            if self._world_model is not None:
                status['world_model'] = self._world_model.get_summary()
        except Exception:
            status['world_model'] = {'error': 'unavailable'}
        try:
            if self._tool_registry is not None:
                status['tool_registry'] = self._tool_registry.get_stats()
        except Exception:
            status['tool_registry'] = {'error': 'unavailable'}
        try:
            if self._policy_engine is not None:
                status['policy_engine'] = self._policy_engine.get_summary()
        except Exception:
            status['policy_engine'] = {'error': 'unavailable'}
        try:
            if self._runtime_supervisor is not None:
                status['runtime_supervisor'] = self._runtime_supervisor.get_status()
        except Exception:
            status['runtime_supervisor'] = {'error': 'unavailable'}
        try:
            if self._memory_manager is not None:
                focus = self._memory_manager.get_focus()
                goal = self._memory_manager.get_goal()
                active_task = self._memory_manager.get_active_task()
                recent_count = len(self._memory_manager.get_recent_actions(5))
                status['memory_manager'] = {'focus': focus, 'goal': goal, 'active_task': active_task, 'recent_actions_count': recent_count}
        except Exception:
            status['memory_manager'] = {'error': 'unavailable'}
        try:
            if self._recovery_manager is not None:
                health = self._recovery_manager.check_health()
                status['recovery'] = {'healthy': health.get('healthy', False), 'issues': health.get('issues', []), 'warnings': health.get('warnings', [])}
        except Exception:
            status['recovery'] = {'error': 'unavailable'}
        return status

    def before_task(self, task_type: str, description: str, context: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        """Run BEFORE any task execution.

        Pipeline:
        1. Observe environment via WorldModel.get_world_state(refresh=True)
        2. Policy check via PolicyEngine.check_action(task_type, context)
        3. Record task in WorldModel (provides a task_id)
        4. Set working memory focus
        5. Log the pre-task event

        Parameters
        ----------
        task_type : str
            The type/capability of the task (e.g. ``"terminal_exec"``).
        description : str
            Human-readable description of what this task does.
        context : dict or None
            Optional context for policy checks and world model.

        Returns
        -------
        dict
            ``{"allowed": bool, "world_state": dict, "task_id": str, "violations": list}``.
            If ``allowed`` is ``False``, the task should NOT proceed.
        """
        ctx = context or {}
        violations: List[str] = []
        world_state: Dict[str, Any] = {}
        try:
            if self._world_model is not None:
                world_state = self._world_model.get_world_state(refresh=True)
        except Exception as exc:
            violations.append(f'world_state_refresh_failed: {exc}')
        allowed = True
        try:
            if self._policy_engine is not None:
                policy_result = self._policy_engine.check_action(task_type, ctx)
                allowed = policy_result.get('allowed', True)
                if not allowed:
                    violations.append(f"policy_denied: {policy_result.get('reason', 'unknown')}")
        except Exception as exc:
            violations.append(f'policy_check_failed: {exc}')
            allowed = False
        task_id = ''
        try:
            if self._world_model is not None:
                status = 'blocked' if not allowed else 'started'
                task_id = self._world_model.record_task(task_type, description, status=status)
        except Exception as exc:
            violations.append(f'task_record_failed: {exc}')
        try:
            if self._memory_manager is not None:
                self._memory_manager.set_focus(description)
                if task_id:
                    self._memory_manager.set_active_task(task_id)
                if ctx.get('goal'):
                    self._memory_manager.set_goal(str(ctx['goal']))
        except Exception as exc:
            violations.append(f'memory_focus_failed: {exc}')
        try:
            if self._event_logger is not None:
                self._event_logger.log(_KERNEL_TASK_BEFORE_EVENT, {'task_type': task_type, 'description': description, 'task_id': task_id, 'allowed': allowed, 'violations': violations}, severity='warning' if not allowed else 'info')
        except Exception as exc:
            logger.debug('kernel: before_task: %s', exc)
        return {'allowed': allowed, 'world_state': world_state, 'task_id': task_id, 'violations': violations}

    def after_task(self, task_id: str, result: Dict[str, Any], context: Optional[Dict[str, Any]]=None) -> None:
        """Run AFTER any task execution.

        Pipeline:
        1. Update task in WorldModel (status, result, duration)
        2. Record tool usage in ExperienceManager
        3. Run ReflectionEngine.reflect_on_task()
        4. Learn from experience (record_success/failure)
        5. Update working memory
        6. Log completion event

        Parameters
        ----------
        task_id : str
            The task id returned by ``before_task()``.
        result : dict
            Task result payload.  Keys frequently used: ``success``,
            ``summary``, ``error``, ``duration_s``, ``tools_used``.
        context : dict or None
            Optional execution context.
        """
        ctx = context or {}
        success = bool(result.get('success', False))
        result_summary = result.get('summary') or result.get('result_summary') or ''
        error_message = result.get('error') or result.get('error_message') or ''
        duration_s = result.get('duration_s', 0.0)
        try:
            if self._world_model is not None:
                status = 'completed' if success else 'failed'
                self._world_model.update_task(task_id, status=status, result_summary=result_summary or status, error_message=error_message if not success else None)
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)
        try:
            if self._experience_manager is not None:
                tools_used = result.get('tools_used') or result.get('tools') or []
                if isinstance(tools_used, list):
                    for t in tools_used:
                        if isinstance(t, dict):
                            self._experience_manager.record_tool_usage(tool_name=t.get('name', 'unknown'), success=t.get('success', success), cost=t.get('cost', 0.0), duration_s=t.get('duration_s', duration_s))
                        elif isinstance(t, str):
                            self._experience_manager.record_tool_usage(tool_name=t, success=success, duration_s=duration_s)
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)
        try:
            if self._reflection_engine is not None:
                goal = ctx.get('goal') or self._get_current_goal() or description_from_wm(task_id, self._world_model) or 'unknown'
                self._reflection_engine.reflect_on_task(task_id=task_id, goal=goal, result=result, context=ctx)
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)
        try:
            if self._experience_manager is not None:
                domain = ctx.get('domain') or result.get('domain') or 'general'
                if success:
                    actions = []
                    steps = result.get('steps') or result.get('actions') or []
                    if isinstance(steps, list):
                        for s in steps:
                            if isinstance(s, dict):
                                actions.append(s.get('name') or s.get('action') or str(s))
                            else:
                                actions.append(str(s))
                    pattern_name = ctx.get('goal', '')[:200] or f'task_{task_id[:8]}'
                    self._experience_manager.record_success(pattern_name=pattern_name, action_sequence=actions or [pattern_name], duration_s=duration_s, domain=domain, tags=ctx.get('tags'))
                else:
                    error_type = 'task_failure'
                    self._experience_manager.record_failure(domain=domain, error_type=error_type, error_message=error_message or 'Unknown failure')
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)
        try:
            if self._memory_manager is not None:
                self._memory_manager.push_action(action=ctx.get('task_type', 'task'), result_summary=result_summary or ('success' if success else 'failed'))
                active_id = self._memory_manager.get_active_task()
                if active_id == task_id:
                    self._memory_manager.set_active_task('')
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)
        try:
            if self._event_logger is not None:
                self._event_logger.log(_KERNEL_TASK_AFTER_EVENT, {'task_id': task_id, 'success': success, 'duration_s': duration_s, 'result_summary': result_summary}, severity='info')
        except Exception as exc:
            logger.debug('kernel: after_task: %s', exc)

    def _get_current_goal(self) -> Optional[str]:
        """Read the current goal from working memory."""
        try:
            if self._memory_manager is not None:
                return self._memory_manager.get_goal()
        except Exception as exc:
            logger.debug('kernel: _get_current_goal: %s', exc)
        return None

    def plan(self, goal: str, context: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        """High-level planning: generate a task graph from a goal.

        Pipeline:
        1. Get current world state
        2. Query experience for best strategies (if ExperienceManager available)
        3. Query tool registry for available capabilities
        4. Generate task graph nodes based on goal and available tools
        5. Return plan with task graph and estimated costs

        Parameters
        ----------
        goal : str
            The high-level goal to plan for.
        context : dict or None
            Optional context (constraints, preferences, domain).

        Returns
        -------
        dict
            Keys: ``graph_id`` (str or None), ``nodes`` (list of TaskNode dicts),
            ``estimated_cost`` (str), ``estimated_duration_s`` (int),
            ``strategies`` (list), ``available_tools`` (list).
        """
        ctx = context or {}
        world_state: Dict[str, Any] = {}
        try:
            if self._world_model is not None:
                world_state = self._world_model.get_world_state(refresh=True)
        except Exception as exc:
            logger.debug('kernel: plan: %s', exc)
        strategies: List[Dict[str, Any]] = []
        try:
            if self._experience_manager is not None:
                domain = ctx.get('domain')
                strategies = self._experience_manager.get_strategies(domain=domain, min_success_rate=0.3)
        except Exception as exc:
            logger.debug('kernel: plan: %s', exc)
        available_tools: List[Dict[str, Any]] = []
        try:
            if self._tool_registry is not None:
                caps = self._tool_registry.find(query=ctx.get('capability_query', ''), filters=ctx.get('filters'))
                available_tools = [c.to_dict() for c in caps]
        except Exception as exc:
            logger.debug('kernel: plan: %s', exc)
        nodes: List[Dict[str, Any]] = []
        try:
            from .task_graph import TaskNode
        except ImportError:
            import sys as _sys, os as _os
            _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
            if _pkg_dir not in _sys.path:
                _sys.path.insert(0, _pkg_dir)
            from task_graph import TaskNode
        if strategies:
            best_strategy = strategies[0]
            action_sequence = best_strategy.get('action_sequence', [])
            if isinstance(action_sequence, list) and action_sequence:
                for i, action_name in enumerate(action_sequence):
                    node_id = _new_uuid()
                    dep_ids = [nodes[j]['node_id'] for j in range(i)] if i > 0 else []
                    nodes.append(TaskNode(node_id=node_id, action=str(action_name), params={'goal': goal, **ctx}, depends_on=dep_ids, timeout=120))
        if not nodes:
            node_id = _new_uuid()
            nodes.append(TaskNode(node_id=node_id, action=ctx.get('default_action', 'unknown'), params={'goal': goal, **ctx}, depends_on=[], timeout=300))
        graph_id: Optional[str] = None
        try:
            if self._task_graph_engine is not None and nodes:
                graph_id = self._task_graph_engine.create_graph(name=goal[:100], nodes=nodes)
        except Exception as exc:
            logger.debug('kernel: plan: %s', exc)
        estimated_cost = 'medium'
        estimated_duration_s = 120
        try:
            if available_tools:
                costs = [t.get('cost', 'medium') for t in available_tools]
                if 'high' in costs:
                    estimated_cost = 'high'
                elif 'medium' in costs:
                    estimated_cost = 'medium'
                else:
                    estimated_cost = 'low'
                timeouts = [t.get('timeout_s', 30) for t in available_tools]
                estimated_duration_s = min(sum(timeouts), 3600)
        except Exception as exc:
            logger.debug('kernel: plan: %s', exc)
        node_dicts = []
        try:
            for n in nodes:
                node_dicts.append({'node_id': n.node_id, 'action': n.action, 'params': n.params, 'depends_on': n.depends_on, 'timeout': n.timeout})
        except Exception:
            node_dicts = [{'node_id': n.node_id, 'action': str(n.action)} for n in nodes]
        return {'graph_id': graph_id, 'nodes': node_dicts, 'estimated_cost': estimated_cost, 'estimated_duration_s': estimated_duration_s, 'strategies': strategies[:5], 'available_tools': available_tools, 'world_state_summary': {k: world_state.get(k) for k in ('cpu', 'memory', 'disk', 'network') if k in world_state}}

    def health_check(self) -> Dict[str, Any]:
        """Run a full system health check across all subsystems.

        Returns
        -------
        dict
            Keys: ``healthy`` (bool), ``kernel``, ``database``,
            ``event_log``, ``world_model``, ``supervisor``, ``recovery``,
            ``issues`` (list), ``warnings`` (list).
        """
        healthy = True
        issues: List[str] = []
        warnings: List[str] = []
        kernel_info: Dict[str, Any] = {'initialized': self._initialized_flag}
        if not self._initialized_flag:
            issues.append('Kernel not initialized')
        db_status = 'unknown'
        try:
            if self._schema_mgr is not None:
                try:
                    from .db_schema import DATABASE_SCHEMAS
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    from db_schema import DATABASE_SCHEMAS

                for db_name in DATABASE_SCHEMAS:
                    conn = self._schema_mgr.get_connection(db_name)
                    cursor = conn.execute('PRAGMA integrity_check;')
                    result = cursor.fetchone()
                    if result and result[0] != 'ok':
                        issues.append(f'DB integrity issue in {db_name}: {result[0]}')
                db_status = 'ok'
        except Exception as exc:
            db_status = f'error: {exc}'
            issues.append(f'Database check failed: {exc}')
        log_status = 'unknown'
        try:
            if self._event_logger is not None:
                _ = self._event_logger.replay(limit=1)
                log_status = 'ok'
        except Exception as exc:
            log_status = f'error: {exc}'
            issues.append(f'Event log check failed: {exc}')
        wm_status = 'unknown'
        try:
            if self._world_model is not None:
                state = self._world_model.get_world_state(refresh=False)
                wm_status = 'has_data' if state.get('snapshot_at') else 'empty'
        except Exception as exc:
            wm_status = f'error: {exc}'
            issues.append(f'World model check failed: {exc}')
        sup_status = 'unknown'
        try:
            if self._runtime_supervisor is not None:
                sup_status = 'running' if self._runtime_supervisor._running else 'stopped'
        except Exception as exc:
            sup_status = f'error: {exc}'
        recovery_healthy = None
        try:
            if self._recovery_manager is not None:
                health = self._recovery_manager.check_health()
                recovery_healthy = health.get('healthy', False)
                warnings.extend(health.get('warnings', []))
        except Exception as exc:
            recovery_healthy = False
            warnings.append(f'Recovery health check failed: {exc}')
        healthy = self._initialized_flag and 'ok' in db_status and ('ok' in log_status) and (len(issues) == 0)
        return {'healthy': healthy, 'kernel': kernel_info, 'database': db_status, 'event_log': log_status, 'world_model': wm_status, 'supervisor': sup_status, 'recovery': recovery_healthy, 'issues': issues, 'warnings': warnings}

    def self_diagnose(self) -> List[str]:
        """Identify potential issues with the kernel and its subsystems.

        Returns
        -------
        list[str]
            Human-readable diagnostic messages.  Empty list = all clear.
        """
        diags: List[str] = []
        if not self._initialized_flag:
            diags.append('Kernel is not initialized — call initialize() first')
            return diags
        uptime = self.uptime_seconds()
        if uptime > 3600:
            diags.append(f'Kernel has been running for {uptime:.0f}s — consider periodic restart')
        try:
            if self._runtime_supervisor is not None:
                alerts = self._runtime_supervisor.get_alerts(clear=False)
                if alerts:
                    diags.append(f'RuntimeSupervisor has {len(alerts)} active alert(s)')
        except Exception:
            diags.append('RuntimeSupervisor unavailable')
        try:
            if self._memory_manager is not None:
                actions = self._memory_manager.get_recent_actions(1)
                if not actions:
                    diags.append('No recent actions recorded — working memory is empty')
        except Exception:
            diags.append('MemoryManager unavailable')
        try:
            if self._tool_registry is not None:
                stats = self._tool_registry.get_stats()
                if stats.get('total_capabilities', 0) == 0:
                    diags.append('Tool registry has no registered capabilities')
        except Exception:
            diags.append('ToolRegistry unavailable')
        try:
            if self._world_model is not None:
                state = self._world_model.get_world_state(refresh=False)
                if not state.get('snapshot_at'):
                    diags.append('World model has no snapshots')
        except Exception:
            diags.append('WorldModel unavailable')
        try:
            if self._event_logger is not None:
                stats = self._event_logger.get_stats()
                if stats.get('total_events', 0) == 0:
                    diags.append('Event log is empty — no events recorded yet')
        except Exception:
            diags.append('EventLogger unavailable')
        try:
            if self._recovery_manager is not None:
                health = self._recovery_manager.check_health()
                if not health.get('healthy', True):
                    diags.append(f"Recovery health check issues: {health.get('issues', [])}")
        except Exception:
            diags.append('RecoveryManager unavailable')
        return diags

def description_from_wm(task_id: str, wm: Any) -> str:
    """Try to fetch a task description from the world model DB."""
    if wm is None:
        return ''
    try:
        conn = wm._mgr.get_connection('world_state')
        cursor = conn.execute('SELECT description FROM task_history WHERE id = ?', (int(task_id),))
        row = cursor.fetchone()
        if row and row['description']:
            return row['description']
    except Exception as exc:
        logger.debug('kernel: self_diagnose: %s', exc)
    return ''

def get_kernel() -> AgentKernel:
    """Return the application-wide ``AgentKernel`` singleton.

    The kernel is lazily initialised on first call; subsystems are not
    started until ``initialize()`` is called.
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = AgentKernel()
        return _instance

def initialize() -> Dict[str, Any]:
    """Shorthand: initialise the kernel and return the init report.

    Equivalent to ``get_kernel().initialize()``.
    """
    return get_kernel().initialize()

def before_action(task_type: str, description: str, context: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    """Shorthand: run the pre-task pipeline.

    Equivalent to ``get_kernel().before_task(task_type, description, context)``.
    """
    return get_kernel().before_task(task_type, description, context)

def after_action(task_id: str, result: Dict[str, Any], context: Optional[Dict[str, Any]]=None) -> None:
    """Shorthand: run the post-task pipeline.

    Equivalent to ``get_kernel().after_task(task_id, result, context)``.
    """
    return get_kernel().after_task(task_id, result, context)
__all__ = ['AgentKernel', 'get_kernel', 'initialize', 'before_action', 'after_action']