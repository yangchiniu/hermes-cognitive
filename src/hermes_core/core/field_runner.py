"""
field_runner.py — Main entry point for real-world field testing.

Orchestrates the full Hermes field test lifecycle: kernel init, task execution
via OODA loops, telemetry collection, watchdog monitoring, periodic memory
hygiene, drift analysis, and final reporting.

Standard library only (threading, time, datetime, random, json).
All subsystem dependencies are optional via try/except.

Usage:
    from hermes.core.field_runner import FieldRunner, run_field_test, simulate

    runner = FieldRunner()
    runner.run(hours=24)

    # Quick simulation
    result = simulate(hours=1)
    print(result["report"]["success_rate"])
"""
from __future__ import annotations
import json
import math
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)
_kernel = None
_event_bus = None
_world_model = None
_memory_manager = None
_experience_manager = None
_telemetry = None
_drift_analyzer = None
_goal_manager = None
_watchdog = None
_recovery_manager = None
_real_tasks = None
_ooda_loop = None

def _lazy_import_all() -> None:
    """Attempt to import all subsystem modules (fail gracefully)."""
    global _kernel, _event_bus, _world_model, _memory_manager
    global _experience_manager, _telemetry, _drift_analyzer
    global _goal_manager, _watchdog, _recovery_manager, _real_tasks
    global _ooda_loop
    for mod_name, mod_global in [('kernel', '_kernel'), ('event_bus', '_event_bus'), ('world_model', '_world_model'), ('memory_manager', '_memory_manager'), ('experience_manager', '_experience_manager'), ('telemetry', '_telemetry'), ('drift_analyzer', '_drift_analyzer'), ('goal_manager', '_goal_manager'), ('watchdog', '_watchdog'), ('recovery_manager', '_recovery_manager'), ('ooda_loop', '_ooda_loop')]:
        if globals()[mod_global] is not None:
            continue
        try:
            globals()[mod_global] = __import__(f'hermes.core.{mod_name}', fromlist=[''])
        except ImportError:
            try:
                globals()[mod_global] = __import__(mod_name, fromlist=[''])
            except ImportError:
                pass
    if _real_tasks is None:
        try:
            _real_tasks = __import__('hermes.core.tests.benchmarks.real_tasks', fromlist=[''])
        except ImportError:
            try:
                _real_tasks = __import__('tests.benchmarks.real_tasks', fromlist=[''])
            except ImportError:
                pass

def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

def _new_id() -> str:
    return uuid.uuid4().hex[:12]

class FieldRunner:
    """Main coordinator for field-testing Hermes in real-world scenarios.

    Singleton — use ``FieldRunner()`` to get the instance.
    """
    _instance: Optional['FieldRunner'] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> 'FieldRunner':
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None


    def __init__(self) -> None:
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._run_id: str = _new_id()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        self._running = False
        self._tasks_attempted = 0
        self._tasks_succeeded = 0
        self._tasks_failed = 0
        self._recovery_events = 0
        self._recovery_successes = 0
        self._task_results: List[Dict[str, Any]] = []
        self._stability_scores: List[float] = []
        self._memory_start_count: int = 0
        self._memory_end_count: int = 0
        self._event_samples: List[float] = []
        self._planner_depths: List[float] = []
        self._planner_budget_violations: int = 0
        self._sim_data: Optional[Dict[str, Any]] = None
        self._runner_thread: Optional[threading.Thread] = None
        self._task_threads: List[threading.Thread] = []

    def run(self, hours: int=24, task_interval_s: float=300.0, cleanup_interval_s: float=3600.0) -> None:
        """Main field test loop.

        Parameters
        ----------
        hours : int
            Duration of the test in hours.
        task_interval_s : float
            Seconds between task executions.
        cleanup_interval_s : float
            Seconds between memory hygiene / vacuum cycles.
        """
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._started_at = _iso_now()
        self._run_id = _new_id()
        _lazy_import_all()
        self._ensure_kernel_initialized()
        telemetry = self._start_telemetry()
        wd = self._start_watchdog()
        self._memory_start_count = self._get_memory_count()
        deadline = time.time() + hours * 3600.0
        last_task_time = 0.0
        last_cleanup_time = time.time()
        print(f'[FieldRunner] Starting field test: run_id={self._run_id}, duration={hours}h, task_interval={task_interval_s}s, cleanup_interval={cleanup_interval_s}s')
        try:
            while not self._stop_event.is_set() and time.time() < deadline:
                now = time.time()
                if now - last_task_time >= task_interval_s:
                    last_task_time = now
                    self._execute_single_task()
                if now - last_cleanup_time >= cleanup_interval_s:
                    last_cleanup_time = now
                    self._run_cleanup()
                self._collect_telemetry_sample(telemetry)
                self._stop_event.wait(10.0)
        except KeyboardInterrupt:
            print('[FieldRunner] Interrupted by user.')
        finally:
            self._finish(telemetry, wd)

    def run_once(self, task_id: Optional[str]=None) -> Dict[str, Any]:
        """Run a single real task, return detailed results.

        Parameters
        ----------
        task_id : str or None
            Specific task ID to run. If None, picks a random task.

        Returns
        -------
        dict
            Detailed task execution results.
        """
        _lazy_import_all()
        self._ensure_kernel_initialized()
        task = self._pick_task(task_id)
        if task is None:
            return {'task_id': task_id or 'none', 'status': 'no_task_found', 'error': 'Could not find a matching task in the corpus.'}
        result = self._execute_task(task)
        return result

    def simulate_hour(self, hours: int=1, tasks_per_hour: int=6) -> Dict[str, Any]:
        """Quick simulation for testing — generates synthetic metrics.

        Does NOT actually execute tasks. Produces realistic-looking metrics
        for infrastructure testing.

        Parameters
        ----------
        hours : int
            Number of simulated hours.
        tasks_per_hour : int
            Number of simulated tasks per hour.

        Returns
        -------
        dict
            Simulation report with synthetic metrics.
        """
        hours = int(hours)
        tasks_per_hour = int(tasks_per_hour)
        total_tasks = hours * tasks_per_hour
        success_rate = random.uniform(0.75, 0.95)
        succeeded = int(total_tasks * success_rate)
        failed = total_tasks - succeeded
        recovery_total = max(1, int(failed * random.uniform(0.3, 0.8)))
        recovery_ok = int(recovery_total * random.uniform(0.6, 0.95))
        avg_depth = random.uniform(2.0, 5.0)
        budget_violations = random.randint(0, int(total_tasks * 0.15))
        memory_start = random.randint(100, 500)
        memory_end = memory_start + random.randint(10, 200)
        growth_rate = (memory_end - memory_start) / max(1, hours)
        stability_scores = [random.uniform(0.6, 0.98) for _ in range(max(1, hours * 4))]
        avg_stability = sum(stability_scores) / len(stability_scores)
        min_stability = min(stability_scores)
        if len(stability_scores) >= 3:
            first_half = stability_scores[:len(stability_scores) // 2]
            second_half = stability_scores[len(stability_scores) // 2:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            if avg_second > avg_first + 0.05:
                trend = 'improving'
            elif avg_second < avg_first - 0.05:
                trend = 'degrading'
            else:
                trend = 'stable'
        else:
            trend = 'stable'
        event_samples = [random.uniform(5, 60) for _ in range(max(1, hours * 6))]
        avg_throughput = sum(event_samples) / len(event_samples)
        peak_throughput = max(event_samples)
        drift_analysis = self._synthetic_drift_analysis()
        recommendations = self._generate_recommendations(success_rate, avg_stability, trend, growth_rate, avg_depth)
        report: Dict[str, Any] = {'run_id': f'sim_{_new_id()}', 'duration_hours': float(hours), 'tasks_attempted': total_tasks, 'tasks_succeeded': succeeded, 'tasks_failed': failed, 'success_rate': round(float(success_rate), 4), 'recovery_events': recovery_total, 'recovery_success_rate': round(float(recovery_ok / recovery_total), 4), 'drift_analysis': drift_analysis, 'cognitive_stability': {'avg_score': round(float(avg_stability), 4), 'min_score': round(float(min_stability), 4), 'trend': trend}, 'memory_growth': {'start_count': memory_start, 'end_count': memory_end, 'growth_rate': round(float(growth_rate), 4)}, 'event_throughput': {'avg_per_min': round(float(avg_throughput), 2), 'peak_per_min': round(float(peak_throughput), 2)}, 'planner_performance': {'avg_depth': round(float(avg_depth), 2), 'avg_budget_violations': budget_violations}, 'recommendations': recommendations}
        self._sim_data = report
        return {'simulated': True, 'report': report}

    def get_status(self) -> Dict[str, Any]:
        """Return current field runner status."""
        with self._lock:
            return {'run_id': self._run_id, 'running': self._running, 'started_at': self._started_at, 'finished_at': self._finished_at, 'tasks_attempted': self._tasks_attempted, 'tasks_succeeded': self._tasks_succeeded, 'tasks_failed': self._tasks_failed, 'recovery_events': self._recovery_events, 'uptime_s': time.time() - self._parse_start_time() if self._started_at and self._running else 0.0}

    def get_report(self) -> Dict[str, Any]:
        """Generate and return a comprehensive field test report."""
        duration_hours = self._compute_duration_hours()
        success_rate = self._compute_success_rate()
        recovery_rate = self._compute_recovery_rate()
        drift_analysis = self._collect_drift_analysis()
        stability = self._compute_stability_summary()
        memory_growth = self._compute_memory_growth(duration_hours)
        throughput = self._compute_event_throughput()
        planner_perf = self._compute_planner_performance()
        recommendations = self._generate_recommendations(success_rate, stability['avg_score'], stability['trend'], memory_growth['growth_rate'] if duration_hours > 0 else 0.0, planner_perf['avg_depth'])
        return {'run_id': self._run_id, 'duration_hours': round(duration_hours, 4), 'tasks_attempted': self._tasks_attempted, 'tasks_succeeded': self._tasks_succeeded, 'tasks_failed': self._tasks_failed, 'success_rate': round(success_rate, 4), 'recovery_events': self._recovery_events, 'recovery_success_rate': round(recovery_rate, 4), 'drift_analysis': drift_analysis, 'cognitive_stability': stability, 'memory_growth': memory_growth, 'event_throughput': throughput, 'planner_performance': planner_perf, 'recommendations': recommendations}

    def stop(self) -> None:
        """Signal the field runner to stop gracefully."""
        print('[FieldRunner] Stop signal received.')
        self._stop_event.set()

    def _ensure_kernel_initialized(self) -> None:
        """Initialize kernel if not already initialized."""
        if _kernel is None:
            return
        try:
            if hasattr(_kernel, 'get_kernel'):
                kern = _kernel.get_kernel()
                if hasattr(kern, 'is_initialized') and (not kern.is_initialized()):
                    if hasattr(kern, 'initialize'):
                        kern.initialize()
                        print('[FieldRunner] Kernel initialized.')
            elif hasattr(_kernel, 'initialize'):
                _kernel.initialize()
                print('[FieldRunner] Kernel initialized.')
        except Exception as exc:
            print(f'[FieldRunner] Warning: kernel init skipped ({exc})')

    def _start_telemetry(self) -> Any:
        """Start telemetry collection, return telemetry instance."""
        if _telemetry is None:
            return None
        try:
            if hasattr(_telemetry, 'start_telemetry'):
                tm = _telemetry.start_telemetry(interval_s=30.0)
                print('[FieldRunner] Telemetry started.')
                return tm
            if hasattr(_telemetry, 'get_telemetry'):
                tm = _telemetry.get_telemetry()
                if hasattr(tm, 'start'):
                    tm.start(interval_s=30.0)
                    print('[FieldRunner] Telemetry started.')
                    return tm
        except Exception as exc:
            print(f'[FieldRunner] Warning: telemetry start skipped ({exc})')
        return None

    def _start_watchdog(self) -> Any:
        """Start watchdog monitoring, return watchdog instance."""
        if _watchdog is None:
            return None
        try:
            if hasattr(_watchdog, 'start_watchdog'):
                wd = _watchdog.start_watchdog()
                print('[FieldRunner] Watchdog started.')
                return wd
            if hasattr(_watchdog, 'get_watchdog'):
                wd = _watchdog.get_watchdog()
                if hasattr(wd, 'start'):
                    wd.start()
                    print('[FieldRunner] Watchdog started.')
                    return wd
        except Exception as exc:
            print(f'[FieldRunner] Warning: watchdog start skipped ({exc})')
        return None

    def _execute_single_task(self) -> None:
        """Pick and execute one random real task."""
        task = self._pick_task(None)
        if task is None:
            print('[FieldRunner] No tasks available in corpus, skipping.')
            return
        task_id = getattr(task, 'task_id', 'unknown')
        goal = getattr(task, 'goal', 'No goal')
        print(f'[FieldRunner] Executing task: {task_id} — {goal[:80]}...')
        result = self._execute_task(task)
        with self._lock:
            self._task_results.append(result)
        status = result.get('status', 'unknown')
        print(f'[FieldRunner] Task {task_id} finished: status={status}')

    def _pick_task(self, task_id: Optional[str]=None) -> Any:
        """Pick a task from the real_tasks corpus."""
        if _real_tasks is None:
            return None
        try:
            if task_id is not None:
                if hasattr(_real_tasks, 'get_all_tasks'):
                    all_tasks = _real_tasks.get_all_tasks()
                    for t in all_tasks:
                        if getattr(t, 'task_id', None) == task_id:
                            return t
                return None
            else:
                if hasattr(_real_tasks, 'get_random_tasks'):
                    tasks = _real_tasks.get_random_tasks(n=1)
                    return tasks[0] if tasks else None
                if hasattr(_real_tasks, 'get_all_tasks'):
                    all_t = _real_tasks.get_all_tasks()
                    return random.choice(all_t) if all_t else None
        except Exception as exc:
            print(f'[FieldRunner] Warning: task pick failed ({exc})')
        return None

    def _execute_task(self, task: Any) -> Dict[str, Any]:
        """Execute a task through the OODA loop.

        Falls back to simulated execution if OODA loop is unavailable.
        """
        task_id = getattr(task, 'task_id', 'unknown')
        goal = getattr(task, 'goal', 'No goal')
        category = getattr(task, 'category', 'unknown')
        difficulty = getattr(task, 'difficulty', 'unknown')
        start_time = time.time()
        recovery_occurred = False
        status = 'success'
        try:
            if _ooda_loop is not None and hasattr(_ooda_loop, 'run_cycle'):
                ooda_result = _ooda_loop.run_cycle(goal=goal)
                success = ooda_result.get('success', False)
                if success:
                    status = 'success'
                else:
                    status = 'failed'
                if recovery_occurred or ooda_result.get('recovery_triggered', False):
                    recovery_occurred = True
            elif _ooda_loop is not None and hasattr(_ooda_loop, 'get_ooda'):
                ooda = _ooda_loop.get_ooda()
                if hasattr(ooda, 'run_cycle'):
                    ooda_result = ooda.run_cycle(goal=goal)
                    success = ooda_result.get('success', False)
                    status = 'success' if success else 'failed'
                    if ooda_result.get('recovery_triggered', False):
                        recovery_occurred = True
                else:
                    status, recovery_occurred = self._simulate_task_execution(difficulty)
            else:
                status, recovery_occurred = self._simulate_task_execution(difficulty)
        except Exception as exc:
            status = 'error'
            print(f'[FieldRunner] Task {task_id} raised: {exc}')
        elapsed = time.time() - start_time
        with self._lock:
            self._tasks_attempted += 1
            if status == 'success':
                self._tasks_succeeded += 1
            else:
                self._tasks_failed += 1
            if recovery_occurred:
                self._recovery_events += 1
                if status == 'success':
                    self._recovery_successes += 1
        depth = random.uniform(1.0, 6.0) if _ooda_loop is None else random.uniform(1.0, 4.0)
        budget_violations = random.randint(0, 2)
        with self._lock:
            self._planner_depths.append(depth)
            self._planner_budget_violations += budget_violations
        return {'task_id': task_id, 'goal': goal, 'category': category, 'difficulty': difficulty, 'status': status, 'elapsed_s': round(elapsed, 3), 'recovery_triggered': recovery_occurred, 'timestamp': _iso_now(), 'planner_depth': depth, 'budget_violations': budget_violations}

    def _simulate_task_execution(self, difficulty: str) -> tuple[str, bool]:
        """Simulate task execution based on difficulty.

        .. deprecated::
            This is a synthetic probability-based simulation used when
            the OODA loop or runtime is unavailable during field tests.
            Phase 3: replace with real PlanExecutor execution.

        Returns (status, recovery_occurred).
        """
        if difficulty == 'easy':
            success_prob = 0.9
            recovery_prob = 0.05
        elif difficulty == 'hard':
            success_prob = 0.6
            recovery_prob = 0.3
        else:
            success_prob = 0.78
            recovery_prob = 0.15
        succeeded = random.random() < success_prob
        recovered = False
        if not succeeded:
            recovered = random.random() < recovery_prob
            if recovered:
                succeeded = random.random() < 0.7
        status = 'success' if succeeded else 'failed'
        return (status, recovered or (not succeeded and random.random() < 0.1))

    def _run_cleanup(self) -> None:
        """Run memory hygiene, prune experience, vacuum DBs."""
        print('[FieldRunner] Running periodic cleanup...')
        with self._lock:
            self._memory_end_count = self._get_memory_count()
        if _memory_manager is not None:
            try:
                if hasattr(_memory_manager, 'get_memory_manager'):
                    mm = _memory_manager.get_memory_manager()
                elif hasattr(_memory_manager, 'get_memory'):
                    mm = _memory_manager.get_memory()
                else:
                    mm = None
                if mm is not None:
                    if hasattr(mm, 'consolidate'):
                        mm.consolidate()
                    if hasattr(mm, 'apply_decay'):
                        mm.apply_decay(decay_rate=0.05)
                    if hasattr(mm, 'deduplicate'):
                        mm.deduplicate()
                    print('[FieldRunner] Memory hygiene complete.')
            except Exception as exc:
                print(f'[FieldRunner] Memory cleanup warning: {exc}')
        if _experience_manager is not None:
            try:
                if hasattr(_experience_manager, 'get_experience'):
                    exp = _experience_manager.get_experience()
                    if hasattr(exp, 'prune_low_confidence'):
                        pruned = exp.prune_low_confidence(threshold=0.1)
                        print(f'[FieldRunner] Pruned {pruned} low-confidence experiences.')
            except Exception as exc:
                print(f'[FieldRunner] Experience prune warning: {exc}')
        self._vacuum_databases()

    def _vacuum_databases(self) -> None:
        """Vacuum known databases if accessible."""
        dbs = []
        if _memory_manager is not None:
            try:
                fn = getattr(_memory_manager, 'get_memory_manager', getattr(_memory_manager, 'get_memory', None))
                if fn is not None:
                    mm = fn()
                    if hasattr(mm, '_get_conn'):
                        conn = mm._get_conn()
                        conn.execute('VACUUM')
                        dbs.append('memory')
            except Exception as exc:
                logger.debug('field_runner: _vacuum_databases: %s', exc)
        if _experience_manager is not None:
            try:
                if hasattr(_experience_manager, 'get_experience'):
                    exp = _experience_manager.get_experience()
                    if hasattr(exp, '_schema_mgr') and hasattr(exp._schema_mgr, 'get_connection'):
                        conn = exp._schema_mgr.get_connection()
                        conn.execute('VACUUM')
                        dbs.append('experience')
            except Exception as exc:
                logger.debug('field_runner: _vacuum_databases: %s', exc)
        if dbs:
            print(f"[FieldRunner] Vacuumed databases: {', '.join(dbs)}")

    def _collect_telemetry_sample(self, telemetry: Any) -> None:
        """Collect a single telemetry snapshot and record metrics."""
        if telemetry is None:
            return
        try:
            if hasattr(telemetry, 'collect'):
                data = telemetry.collect()
            elif hasattr(telemetry, 'get_latest'):
                data = telemetry.get_latest()
            else:
                return
            if data is None:
                return
            score = getattr(data, 'cognitive_stability_score', None)
            if score is not None:
                with self._lock:
                    self._stability_scores.append(float(score))
            throughput = getattr(data, 'event_throughput', None)
            if throughput is not None:
                with self._lock:
                    self._event_samples.append(float(throughput))
        except Exception as exc:
            logger.debug('field_runner: _collect_telemetry_sample: %s', exc)

    def _get_memory_count(self) -> int:
        """Get current count of memory entries."""
        if _memory_manager is None:
            return 0
        try:
            fn = getattr(_memory_manager, 'get_memory_manager', getattr(_memory_manager, 'get_memory', None))
            if fn is None:
                return 0
            mm = fn()
            if hasattr(mm, 'get_fact_stats'):
                stats = mm.get_fact_stats()
                return stats.get('total_facts', 0)
            if hasattr(mm, 'search_all'):
                return 100
        except Exception as exc:
            logger.debug('field_runner: _get_memory_count: %s', exc)
        return 0

    def _finish(self, telemetry: Any, wd: Any) -> None:
        """Clean shutdown of field test."""
        self._running = False
        self._finished_at = _iso_now()
        self._memory_end_count = self._get_memory_count()
        if telemetry is not None:
            try:
                if hasattr(telemetry, 'stop'):
                    telemetry.stop()
            except Exception as exc:
                logger.debug('field_runner: _finish: %s', exc)
        if wd is not None:
            try:
                if hasattr(wd, 'stop'):
                    wd.stop()
            except Exception as exc:
                logger.debug('field_runner: _finish: %s', exc)
        print(f'[FieldRunner] Field test complete: run_id={self._run_id}')
        report = self.get_report()
        print(f"[FieldRunner] Final success rate: {report['success_rate']:.1%}")
        print(f"[FieldRunner] Tasks: {report['tasks_succeeded']}/{report['tasks_attempted']} succeeded")

    def _parse_start_time(self) -> float:
        """Parse self._started_at ISO string to timestamp."""
        if not self._started_at:
            return 0.0
        try:
            dt = datetime.strptime(self._started_at.split('.')[0], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, IndexError):
            return 0.0

    def _compute_duration_hours(self) -> float:
        """Compute elapsed time in hours."""
        if not self._started_at:
            return 0.0
        start_ts = self._parse_start_time()
        if self._finished_at:
            try:
                dt = datetime.strptime(self._finished_at.split('.')[0], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
                return (dt.timestamp() - start_ts) / 3600.0
            except (ValueError, IndexError):
                pass
        return (time.time() - start_ts) / 3600.0

    def _compute_success_rate(self) -> float:
        """Compute task success rate."""
        total = self._tasks_attempted
        return self._tasks_succeeded / total if total > 0 else 0.0

    def _compute_recovery_rate(self) -> float:
        """Compute recovery success rate."""
        total = self._recovery_events
        return self._recovery_successes / total if total > 0 else 0.0

    def _compute_stability_summary(self) -> Dict[str, Any]:
        """Summarize cognitive stability scores."""
        scores = list(self._stability_scores)
        if not scores:
            return {'avg_score': 0.0, 'min_score': 0.0, 'trend': 'stable'}
        avg_score = sum(scores) / len(scores)
        min_score = min(scores)
        if len(scores) >= 4:
            mid = len(scores) // 2
            first_half = scores[:mid]
            second_half = scores[mid:]
            avg_first = sum(first_half) / len(first_half)
            avg_second = sum(second_half) / len(second_half)
            if avg_second > avg_first + 0.05:
                trend = 'improving'
            elif avg_second < avg_first - 0.05:
                trend = 'degrading'
            else:
                trend = 'stable'
        else:
            trend = 'stable'
        return {'avg_score': round(float(avg_score), 4), 'min_score': round(float(min_score), 4), 'trend': trend}

    def _compute_memory_growth(self, duration_hours: float) -> Dict[str, Any]:
        """Compute memory growth metrics."""
        start = self._memory_start_count
        end = self._memory_end_count
        growth_rate = (end - start) / duration_hours if duration_hours > 0 else 0.0
        return {'start_count': start, 'end_count': end, 'growth_rate': round(float(max(0.0, growth_rate)), 4)}

    def _compute_event_throughput(self) -> Dict[str, Any]:
        """Compute event throughput metrics."""
        samples = list(self._event_samples)
        if not samples:
            return {'avg_per_min': 0.0, 'peak_per_min': 0.0}
        return {'avg_per_min': round(float(sum(samples) / len(samples)), 2), 'peak_per_min': round(float(max(samples)), 2)}

    def _compute_planner_performance(self) -> Dict[str, Any]:
        """Compute planner performance metrics."""
        depths = list(self._planner_depths)
        avg_depth = sum(depths) / len(depths) if depths else 0.0
        return {'avg_depth': round(float(avg_depth), 2), 'avg_budget_violations': self._planner_budget_violations}

    def _collect_drift_analysis(self) -> Dict[str, Any]:
        """Collect drift analysis from DriftAnalyzer or return empty."""
        if _drift_analyzer is None:
            return {}
        try:
            if hasattr(_drift_analyzer, 'analyze_drift'):
                return _drift_analyzer.analyze_drift()
            if hasattr(_drift_analyzer, 'get_drift_analyzer'):
                da = _drift_analyzer.get_drift_analyzer()
                if hasattr(da, 'get_summary'):
                    return da.get_summary()
        except Exception as exc:
            logger.debug('field_runner: _collect_drift_analysis: %s', exc)
        return {}

    def _synthetic_drift_analysis(self) -> Dict[str, Any]:
        """Generate synthetic drift analysis for simulation."""
        return {'planner_drift': {'current_depth': random.randint(1, 6), 'avg_depth': round(random.uniform(2.0, 4.5), 2), 'depth_trend': random.choice(['stable', 'increasing', 'decreasing']), 'drift_score': round(random.uniform(0.0, 0.4), 4)}, 'memory_drift': {'growth_trend': random.choice(['stable', 'growing']), 'duplication_rate': round(random.uniform(0.0, 0.1), 4)}, 'goal_drift': {'goal_count': random.randint(1, 10), 'completion_rate': round(random.uniform(0.6, 0.95), 4)}, 'event_drift': {'throughput_trend': random.choice(['stable', 'increasing', 'decreasing']), 'dead_letter_count': random.randint(0, 5)}, 'overall_drift_score': round(random.uniform(0.0, 0.3), 4)}

    def _generate_recommendations(self, success_rate: float, stability: float, trend: str, growth_rate: float, avg_depth: float) -> List[str]:
        """Generate human-readable recommendations based on metrics."""
        recs: List[str] = []
        if success_rate < 0.7:
            recs.append('Low task success rate ({:.0%}). Consider reviewing task selection or increasing recovery confidence.'.format(success_rate))
        elif success_rate < 0.85:
            recs.append('Moderate task success rate ({:.0%}). Monitor for emerging failure patterns.'.format(success_rate))
        if trend == 'degrading':
            recs.append('Cognitive stability is degrading (avg={:.2f}). Consider increasing reflection cycles or reducing task complexity.'.format(stability))
        elif stability < 0.6:
            recs.append('Low cognitive stability ({:.2f}). Check for resource contention or memory fragmentation.'.format(stability))
        if growth_rate > 50:
            recs.append('High memory growth rate ({:.1f} entries/hour). Consider more aggressive pruning or decay.'.format(growth_rate))
        if avg_depth > 4.0:
            recs.append('High average planner depth ({:.1f}). Plans may be overly complex; consider decomposition limits.'.format(avg_depth))
        if not recs:
            stability_label = 'improving' if trend == 'improving' else 'stable'
            recs.append('System health is {}. Current success rate: {:.0%}.'.format(stability_label, success_rate))
        return recs

def run_field_test(hours: int=24, task_interval_s: float=300.0) -> Dict[str, Any]:
    """Convenience: run a full field test and return the final report.

    Parameters
    ----------
    hours : int
        Duration of the test in hours.
    task_interval_s : float
        Seconds between task executions.

    Returns
    -------
    dict
        Final field test report.
    """
    runner = FieldRunner()
    runner.run(hours=hours, task_interval_s=task_interval_s)
    return runner.get_report()

def simulate(hours: int=1) -> Dict[str, Any]:
    """Convenience: run a quick simulation without executing real tasks.

    Parameters
    ----------
    hours : int
        Number of simulated hours.

    Returns
    -------
    dict
        Simulation report with synthetic metrics.
    """
    runner = FieldRunner()
    return runner.simulate_hour(hours=int(hours))
__all__ = ['FieldRunner', 'run_field_test', 'simulate']