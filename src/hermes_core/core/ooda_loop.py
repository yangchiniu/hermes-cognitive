"""
ooda_loop.py — OODA (Observe-Orient-Decide-Act) autonomous cycle for Hermes Core.

Ties together all Hermes Core subsystems into a closed-loop decision cycle:

  Observe  →  Orient  →  Decide  →  Act
     ↓          ↓          ↓         ↓
  WorldModel  Memory    Planner   Step Executor
  Supervisor  Experience           → Reflection
                                   → Experience Update
                                   → Memory Update

Standard library only + existing core modules.
"""
from __future__ import annotations
import json
import time
import uuid
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
try:
    from .world_model import get_world_model, WorldModel
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from world_model import get_world_model, WorldModel
try:
    from .event_bus import get_bus as _get_event_bus, publish as _publish_event
    from .event_bus import EVENT_PLAN_CREATED, EVENT_PLAN_STEP_STARTED, EVENT_PLAN_STEP_COMPLETED, EVENT_PLAN_STEP_FAILED, EVENT_PLAN_COMPLETED, EVENT_PLAN_FAILED
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from event_bus import get_bus as _get_event_bus, publish as _publish_event
    from event_bus import EVENT_PLAN_CREATED, EVENT_PLAN_STEP_STARTED, EVENT_PLAN_STEP_COMPLETED, EVENT_PLAN_STEP_FAILED, EVENT_PLAN_COMPLETED, EVENT_PLAN_FAILED
try:
    from .planner import get_planner, Planner, Plan, PlanStep
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from planner import get_planner, Planner, Plan, PlanStep
try:
    from .reflection_engine import get_reflection, ReflectionEngine, Reflection
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from reflection_engine import get_reflection, ReflectionEngine, Reflection
try:
    from .experience_manager import get_experience, ExperienceManager
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from experience_manager import get_experience, ExperienceManager
try:
    from .memory_manager import get_memory_manager, MemoryManager
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from memory_manager import get_memory_manager, MemoryManager
try:
    from .runtime_supervisor import get_supervisor, check_resources as _check_supervisor_resources
    from .runtime_supervisor import ResourceStatus
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from runtime_supervisor import get_supervisor, check_resources as _check_supervisor_resources
    from runtime_supervisor import ResourceStatus
EVENT_OODA_CYCLE_STARTED = 'ooda.cycle.started'
EVENT_OODA_CYCLE_COMPLETED = 'ooda.cycle.completed'
EVENT_OODA_CYCLE_FAILED = 'ooda.cycle.failed'
EVENT_OODA_OBSERVATION = 'ooda.observation'
EVENT_OODA_ORIENTATION = 'ooda.orientation'
EVENT_OODA_DECISION = 'ooda.decision'
EVENT_OODA_AUTONOMOUS_STARTED = 'ooda.autonomous.started'
EVENT_OODA_AUTONOMOUS_STOPPED = 'ooda.autonomous.stopped'

@dataclass
class OODAResult:
    """Structured output of a single OODA cycle.

    Attributes
    ----------
    cycle_id : str
        UUID identifying this cycle.
    goal : str
        The goal that drove this cycle.
    observation : dict
        Output of the Observe phase — world state snapshot + resource check.
    orientation : dict
        Output of the Orient phase — interpreted context, memories, patterns.
    plan : dict
        The plan produced during the Decide phase (Plan.to_dict()).
    execution_results : list
        Per-step results from the Act phase.
    reflection : dict
        Reflection produced after task execution.
    experience_updated : bool
        Whether the ExperienceManager was updated with results.
    success : bool
        Whether the overall cycle succeeded.
    duration_s : float
        Wall-clock duration of the cycle in seconds.
    errors : list[str]
        Any errors collected during the cycle.
    """
    cycle_id: str
    goal: str
    observation: dict = field(default_factory=dict)
    orientation: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    execution_results: list = field(default_factory=list)
    reflection: dict = field(default_factory=dict)
    experience_updated: bool = False
    success: bool = False
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)
_instance: Optional['OODALoop'] = None
_instance_lock = threading.Lock()

class OODALoop:
    """Autonomous Observe → Orient → Decide → Act cycle.

    A singleton that orchestrates all Hermes Core subsystems in a
    closed-loop decision cycle, optionally running in the background
    on a timer.
    """

    def __init__(self) -> None:
        """Initialise the OODA loop singleton.

        All subsystem singletons are resolved lazily on first access
        via the module-level getter functions, so it is safe to create
        this before the full stack is initialised.
        """
        if getattr(self, '_initialized', False):
            return
        self._world_model: Optional[WorldModel] = None
        self._planner: Optional[Planner] = None
        self._reflection_engine: Optional[ReflectionEngine] = None
        self._experience_manager: Optional[ExperienceManager] = None
        self._memory_manager: Optional[MemoryManager] = None
        self._running = False
        self._stop_event = threading.Event()
        self._autonomous_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cycle_count = 0
        self._last_result: Optional[OODAResult] = None
        self._status: Dict[str, Any] = {'running': False, 'cycle_count': 0, 'last_cycle_id': None, 'last_cycle_success': None, 'last_cycle_at': None, 'autonomous_running': False, 'interval_s': None}
        self._initialized = True

    @property
    def world_model(self) -> WorldModel:
        if self._world_model is None:
            self._world_model = get_world_model()
        return self._world_model

    @property
    def planner(self) -> Planner:
        if self._planner is None:
            self._planner = get_planner()
        return self._planner

    @property
    def reflection_engine(self) -> ReflectionEngine:
        if self._reflection_engine is None:
            self._reflection_engine = get_reflection()
        return self._reflection_engine

    @property
    def experience_manager(self) -> ExperienceManager:
        if self._experience_manager is None:
            self._experience_manager = get_experience()
        return self._experience_manager

    @property
    def memory_manager(self) -> MemoryManager:
        if self._memory_manager is None:
            self._memory_manager = get_memory_manager()
        return self._memory_manager

    def run(self, goal: str, context: Optional[Dict[str, Any]]=None, step_executor: Optional[Callable[[PlanStep], Dict[str, Any]]]=None) -> OODAResult:
        """Run one OODA cycle (alias for run_cycle).

        Parameters
        ----------
        goal : str
            The goal to plan and execute for.
        context : dict or None
            Optional context dict.
        step_executor : callable or None
            Optional step execution callback.

        Returns
        -------
        OODAResult
        """
        return self.run_cycle(goal=goal, context=context, step_executor=step_executor)

    def cycle(self, goal: str, context: Optional[Dict[str, Any]]=None, step_executor: Optional[Callable[[PlanStep], Dict[str, Any]]]=None) -> OODAResult:
        """Run one OODA cycle (alias for run_cycle).

        Parameters
        ----------
        goal : str
            The goal to plan and execute for.
        context : dict or None
            Optional context dict.
        step_executor : callable or None
            Optional step execution callback.

        Returns
        -------
        OODAResult
        """
        return self.run_cycle(goal=goal, context=context, step_executor=step_executor)

    def status(self) -> Dict[str, Any]:
        """Return current OODA loop status (alias for get_status).

        Returns
        -------
        dict
            Status snapshot.
        """
        return self.get_status()

    def run_cycle(self, goal: str, context: Optional[Dict[str, Any]]=None, step_executor: Optional[Callable[[PlanStep], Dict[str, Any]]]=None) -> OODAResult:
        """Execute ONE complete OODA cycle.

        Parameters
        ----------
        goal : str
            The goal to plan and execute for.
        context : dict or None
            Optional context dict (constraints, hints, etc.).
        step_executor : callable or None
            A callable ``fn(step: PlanStep) -> dict`` that executes a
            single plan step and returns a result dict with at minimum
            a ``"success"`` key.  If ``None``, steps are "simulated"
            (the step params are returned as the result).

        Returns
        -------
        OODAResult
            A structured record of the entire cycle.
        """
        cycle_id = f'ooda_{uuid.uuid4().hex[:12]}'
        t_start = time.monotonic()
        errors: List[str] = []
        success = False
        self._publish(EVENT_OODA_CYCLE_STARTED, {'cycle_id': cycle_id, 'goal': goal}, severity='info')
        try:
            observation = self._observe()
            self._publish(EVENT_OODA_OBSERVATION, {'cycle_id': cycle_id, 'observation': _sanitise(observation)}, severity='info')
        except Exception as exc:
            err_msg = f'Observe phase failed: {exc}'
            errors.append(err_msg)
            observation = {'error': err_msg}
            self._publish(EVENT_OODA_OBSERVATION, {'cycle_id': cycle_id, 'error': err_msg}, severity='error')
        orient_context: Dict[str, Any] = {}
        try:
            orient_context = self._orient(goal, observation, context)
            self._publish(EVENT_OODA_ORIENTATION, {'cycle_id': cycle_id, 'orientation': _sanitise(orient_context)}, severity='info')
        except Exception as exc:
            err_msg = f'Orient phase failed: {exc}'
            errors.append(err_msg)
            orient_context = {'error': err_msg}
            self._publish(EVENT_OODA_ORIENTATION, {'cycle_id': cycle_id, 'error': err_msg}, severity='error')
        plan_dict: Dict[str, Any] = {}
        plan_obj: Optional[Plan] = None
        try:
            plan_obj = self.planner.plan(goal=goal, world_state=observation.get('world_state', {}), context={**(context or {}), **orient_context})
            plan_dict = plan_obj.to_dict()
            self._publish(EVENT_PLAN_CREATED, {'cycle_id': cycle_id, 'plan_id': plan_obj.plan_id, 'goal': goal}, severity='info')
            self._publish(EVENT_OODA_DECISION, {'cycle_id': cycle_id, 'plan': _sanitise(plan_dict)}, severity='info')
        except Exception as exc:
            err_msg = f'Decide phase failed: {exc}'
            errors.append(err_msg)
            plan_dict = {'error': err_msg}
            self._publish(EVENT_OODA_DECISION, {'cycle_id': cycle_id, 'error': err_msg}, severity='error')
        execution_results: List[Dict[str, Any]] = []
        all_steps_succeeded = True
        if plan_obj is not None and plan_obj.steps:
            try:
                execution_results = self._execute_plan(plan_obj, step_executor, cycle_id)
                all_steps_succeeded = all((r.get('success', False) for r in execution_results))
            except Exception as exc:
                err_msg = f'Act phase failed: {exc}'
                errors.append(err_msg)
                all_steps_succeeded = False
        if plan_obj is not None:
            if all_steps_succeeded:
                self._publish(EVENT_PLAN_COMPLETED, {'cycle_id': cycle_id, 'plan_id': plan_obj.plan_id}, severity='info')
            else:
                self._publish(EVENT_PLAN_FAILED, {'cycle_id': cycle_id, 'plan_id': plan_obj.plan_id}, severity='warning')
        reflection_dict: Dict[str, Any] = {}
        try:
            reflection_result = self.reflection_engine.reflect_on_task(task_id=cycle_id, goal=goal, result={'success': all_steps_succeeded and (not errors), 'observation': observation, 'orientation': orient_context, 'plan': plan_dict, 'execution_results': execution_results, 'errors': errors}, context=context)
            reflection_dict = asdict(reflection_result)
        except Exception as exc:
            err_msg = f'Reflection phase failed: {exc}'
            errors.append(err_msg)
            reflection_dict = {'error': err_msg}
        experience_updated = False
        try:
            self._update_experience(goal=goal, plan_dict=plan_dict, execution_results=execution_results, all_succeeded=all_steps_succeeded and (not errors), reflection_dict=reflection_dict, duration_s=time.monotonic() - t_start)
            experience_updated = True
        except Exception as exc:
            err_msg = f'Experience update failed: {exc}'
            errors.append(err_msg)
        try:
            self._update_memory(cycle_id=cycle_id, goal=goal, observation=observation, orient_context=orient_context, plan_dict=plan_dict, execution_results=execution_results, reflection_dict=reflection_dict)
        except Exception as exc:
            err_msg = f'Memory update failed: {exc}'
            errors.append(err_msg)
        duration_s = time.monotonic() - t_start
        success = all_steps_succeeded and (not errors)
        result = OODAResult(cycle_id=cycle_id, goal=goal, observation=observation, orientation=orient_context, plan=plan_dict, execution_results=execution_results, reflection=reflection_dict, experience_updated=experience_updated, success=success, duration_s=duration_s, errors=errors)
        with self._lock:
            self._cycle_count += 1
            self._last_result = result
            self._status['cycle_count'] = self._cycle_count
            self._status['last_cycle_id'] = cycle_id
            self._status['last_cycle_success'] = success
            self._status['last_cycle_at'] = datetime.now(timezone.utc).isoformat()
        if success:
            self._publish(EVENT_OODA_CYCLE_COMPLETED, {'cycle_id': cycle_id, 'goal': goal, 'duration_s': duration_s}, severity='info')
        else:
            self._publish(EVENT_OODA_CYCLE_FAILED, {'cycle_id': cycle_id, 'goal': goal, 'duration_s': duration_s, 'errors': errors}, severity='warning')
        return result

    def run_autonomous(self, interval_s: int=300) -> None:
        """Start the OODA autonomous background loop.

        A daemon thread runs OODA cycles at the given interval:
          1. Observe the environment
          2. Check for pending issues (incomplete tasks, resource warnings)
          3. If issues found, generate a goal and run a cycle
          4. Sleep for *interval_s* seconds, repeat

        Parameters
        ----------
        interval_s : int
            Seconds between autonomous cycles (default 300 = 5 min).
        """
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._status['interval_s'] = interval_s
        self._autonomous_thread = threading.Thread(target=self._autonomous_loop, args=(interval_s,), daemon=True, name='ooda-autonomous')
        self._autonomous_thread.start()
        self._publish(EVENT_OODA_AUTONOMOUS_STARTED, {'interval_s': interval_s}, severity='info')

    def stop(self) -> None:
        """Stop the autonomous OODA background loop."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        t = self._autonomous_thread
        if t and t.is_alive():
            t.join(timeout=10)
        self._status['autonomous_running'] = False
        self._status['interval_s'] = None
        self._publish(EVENT_OODA_AUTONOMOUS_STOPPED, {}, severity='info')

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot of current OODA loop status.

        Returns
        -------
        dict
            Keys: running, cycle_count, last_cycle_id, last_cycle_success,
            last_cycle_at, autonomous_running, interval_s.
        """
        with self._lock:
            status = dict(self._status)
            status['running'] = self._running
            status['autonomous_running'] = self._running and self._autonomous_thread is not None and self._autonomous_thread.is_alive()
            return status

    def _observe(self) -> Dict[str, Any]:
        """Execute the Observe phase.

        Returns
        -------
        dict
            A snapshot containing world state, resource status, active
            tasks, and pending alerts.
        """
        world_state = self.world_model.get_world_state(refresh=True)
        resource_status = _check_supervisor_resources()
        alerts = resource_status.get('alerts', [])
        observation: Dict[str, Any] = {'world_state': world_state, 'resources': resource_status, 'alerts': alerts, 'observed_at': datetime.now(timezone.utc).isoformat()}
        return observation

    def _orient(self, goal: str, observation: Dict[str, Any], context: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        """Execute the Orient phase.

        Analyses the observation, retrieves relevant memories and past
        patterns, and builds an enriched orientation context.

        Returns
        -------
        dict
            Oriented context with memories, known failures, strategies,
            and notable changes.
        """
        orient: Dict[str, Any] = {'goal': goal, 'oriented_at': datetime.now(timezone.utc).isoformat()}
        world_state = observation.get('world_state', {})
        resources = observation.get('resources', {})
        alerts = observation.get('alerts', [])
        orient['resource_alerts'] = alerts
        orient['healthy'] = resources.get('healthy', True)
        orient['cpu_load'] = resources.get('cpu_load')
        orient['ram_percent'] = resources.get('ram_percent')
        orient['disk_percent'] = resources.get('disk_percent')
        orient['browser_count'] = resources.get('browser_count', 0)
        orient['task_count'] = resources.get('task_count', 0)
        try:
            memories = self.memory_manager.search_all(goal, limit=3)
            orient['memories'] = memories
        except Exception:
            orient['memories'] = {}
        try:
            known_failures = self.experience_manager.get_known_failures()
            orient['known_failures'] = known_failures
        except Exception:
            orient['known_failures'] = []
        try:
            strategies = self.experience_manager.get_strategies(min_success_rate=0.5)
            orient['strategies'] = strategies
        except Exception:
            orient['strategies'] = []
        return orient

    def _execute_plan(self, plan: Plan, step_executor: Optional[Callable[[PlanStep], Dict[str, Any]]], cycle_id: str) -> List[Dict[str, Any]]:
        """Execute each step of a plan sequentially.

        Parameters
        ----------
        plan : Plan
            The plan to execute.
        step_executor : callable or None
            Callback that executes a single step.  If None, simulates.
        cycle_id : str
            The current OODA cycle ID (for event correlation).

        Returns
        -------
        list[dict]
            Per-step result dicts.
        """
        results: List[Dict[str, Any]] = []
        for step in plan.steps:
            step_result: Dict[str, Any] = {'step_id': step.id, 'action': step.action, 'success': False}
            self._publish(EVENT_PLAN_STEP_STARTED, {'cycle_id': cycle_id, 'plan_id': plan.plan_id, 'step_id': step.id}, severity='info')
            try:
                if step_executor is not None:
                    output = step_executor(step)
                    step_result['output'] = _sanitise(output)
                    step_result['success'] = output.get('success', False)
                else:
                    logger.warning(
                        "DEPRECATED: OODA simulating step execution — "
                        "no step_executor provided.  Phase 3: replace with "
                        "PlanExecutor or RuntimeHotPath integration."
                    )
                    step_result['output'] = {'simulated': True, 'params': step.params}
                    step_result['success'] = True
                if step_result['success']:
                    self._publish(EVENT_PLAN_STEP_COMPLETED, {'cycle_id': cycle_id, 'plan_id': plan.plan_id, 'step_id': step.id}, severity='info')
                else:
                    fallback_used = False
                    if step.fallback and hasattr(plan, 'fallbacks'):
                        fallback_step_id = plan.fallbacks.get(step.id)
                        if fallback_step_id:
                            step_result['fallback_used'] = True
                            self._publish(EVENT_PLAN_STEP_FAILED, {'cycle_id': cycle_id, 'plan_id': plan.plan_id, 'step_id': step.id, 'fallback': fallback_step_id}, severity='warning')
                            fallback_used = True
                    if not fallback_used:
                        self._publish(EVENT_PLAN_STEP_FAILED, {'cycle_id': cycle_id, 'plan_id': plan.plan_id, 'step_id': step.id}, severity='warning')
            except Exception as exc:
                step_result['success'] = False
                step_result['error'] = str(exc)
                self._publish(EVENT_PLAN_STEP_FAILED, {'cycle_id': cycle_id, 'plan_id': plan.plan_id, 'step_id': step.id, 'error': str(exc)}, severity='error')
            results.append(step_result)
        return results

    def _update_experience(self, goal: str, plan_dict: Dict[str, Any], execution_results: List[Dict[str, Any]], all_succeeded: bool, reflection_dict: Dict[str, Any], duration_s: float) -> None:
        """Record results in the ExperienceManager."""
        action_sequence = [r.get('action', 'unknown') for r in execution_results]
        if all_succeeded and action_sequence:
            self.experience_manager.record_success(pattern_name=f'ooda_{goal[:40]}', action_sequence=action_sequence, duration_s=duration_s, domain='general', tags=['ooda', 'autonomous'])
        elif not all_succeeded:
            error_msg = reflection_dict.get('result_summary', goal)
            self.experience_manager.record_failure(domain='general', error_type='ooda_cycle_failure', error_message=error_msg, resolution=None)

    def _update_memory(self, cycle_id: str, goal: str, observation: Dict[str, Any], orient_context: Dict[str, Any], plan_dict: Dict[str, Any], execution_results: List[Dict[str, Any]], reflection_dict: Dict[str, Any]) -> None:
        """Store a record of this OODA cycle in episodic memory."""
        try:
            self.memory_manager.store_episodic(description=f'OODA cycle: {goal}', summary=reflection_dict.get('result_summary', f'Cycle {cycle_id} completed.'), outcome='success' if reflection_dict.get('success') else 'failure', tags=['ooda', 'autonomous'])
        except AttributeError:
            try:
                self.memory_manager.add_episodic(description=f'OODA cycle: {goal}', tags=['ooda', 'autonomous'])
            except AttributeError:
                pass

    @staticmethod
    def _publish(event_type: str, data: Dict[str, Any], severity: str='info') -> None:
        """Publish an event to the EventBus."""
        try:
            _publish_event(event_type=event_type, data=data, source='ooda_loop', severity=severity)
        except Exception as exc:
            logger.debug('ooda_loop: _publish: %s', exc)

    def _autonomous_loop(self, interval_s: int) -> None:
        """Background thread target for the autonomous loop."""
        while not self._stop_event.is_set():
            try:
                observation = self._observe()
                alerts = observation.get('alerts', [])
                resources = observation.get('resources', {})
                issues: List[str] = []
                if not resources.get('healthy', True):
                    issues.append('system_unhealthy')
                if alerts:
                    issues.append(f'{len(alerts)} alert(s) pending')
                task_count = resources.get('task_count', 0)
                if task_count > 0:
                    issues.append(f'{task_count} active task(s)')
                if issues:
                    goal = f"Autonomous: resolve {', '.join(issues)}"
                    self.run_cycle(goal=goal, context={'observation': observation, 'autonomous': True})
                else:
                    self.run_cycle(goal='Autonomous: maintain system state', context={'observation': observation, 'autonomous': True})
            except Exception as exc:
                try:
                    self._publish(EVENT_OODA_CYCLE_FAILED, {'error': f'Autonomous loop error: {exc}'}, severity='error')
                except Exception as exc:
                    logger.debug('ooda_loop: _autonomous_loop: %s', exc)
            if self._stop_event.wait(timeout=interval_s):
                break

def get_ooda() -> OODALoop:
    """Return the application-wide ``OODALoop`` singleton.

    Usage::

        from ooda_loop import get_ooda
import logging

logger = logging.getLogger(__name__)

        ooda = get_ooda()
        result = ooda.run_cycle("search the web for AI news")
        print(result.success)
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = OODALoop()
        return _instance


def reset_ooda_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None


def run_cycle(goal: str, step_executor: Optional[Callable]=None) -> Dict[str, Any]:
    """Convenience: run one OODA cycle and return the result as a dict.

    Parameters
    ----------
    goal : str
        The goal to plan and execute.
    step_executor : callable or None
        Optional step executor callback.

    Returns
    -------
    dict
        The OODAResult converted to a dictionary.
    """
    return asdict(get_ooda().run_cycle(goal=goal, step_executor=step_executor))

def _sanitise(obj: Any, max_depth: int=5) -> Any:
    """Recursively sanitise an object for safe serialisation.

    Converts non-serialisable types to strings, respects max recursion
    depth to prevent infinite loops.
    """
    if max_depth < 0:
        return '<max_depth>'
    if isinstance(obj, dict):
        return {str(k): _sanitise(v, max_depth - 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(item, max_depth - 1) for item in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if obj is None:
        return None
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return str(obj)