"""
planner.py — Goal decomposition and planning engine for Hermes Core.

Decomposes high-level goals into actionable plans with tool selection,
constraint checking, risk assessment, cost estimation, and fallback
generation.  Integrates with the full Hermes Core stack: WorldModel,
ToolRegistry, PolicyEngine, ExperienceManager, MemoryManager, and
EventLogger.

Standard library only + existing core modules.
"""
from __future__ import annotations
import copy
import json
import os
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
try:
    import yaml as _yaml
except ImportError:
    _yaml = None
try:
    from .world_model import get_world_model, WorldModel
    from .tool_registry import get_registry as _get_tool_registry, ToolRegistry, ToolCapability
    from .policy_engine import get_policy_engine, PolicyEngine
    from .experience_manager import get_experience, ExperienceManager
    from .memory_manager import get_memory_manager as _get_memory_manager
    from .event_logger import get_logger, EventLogger
    from .exceptions import HermesCoreError, PolicyViolation, ToolNotFoundError, ResourceLimitExceeded
except ImportError:
    import sys as _sys
    import os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from world_model import get_world_model, WorldModel
    from tool_registry import get_registry as _get_tool_registry, ToolRegistry, ToolCapability
    from policy_engine import get_policy_engine, PolicyEngine
    from experience_manager import get_experience, ExperienceManager
    from memory_manager import get_memory_manager as _get_memory_manager
    from event_logger import get_logger, EventLogger
    from exceptions import HermesCoreError, PolicyViolation, ToolNotFoundError, ResourceLimitExceeded
import logging

logger = logging.getLogger(__name__)
_RISK_ORDER: dict[str, int] = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}
_COST_ORDER: dict[str, int] = {'free': 0, 'low': 1, 'medium': 2, 'high': 3}
_DEFAULT_DECOMPOSITION_CACHE_SIZE = 50
PLANNING_BUDGET: dict[str, int | bool] = {'max_plan_depth': 5, 'max_reasoning_steps': 20, 'max_tool_calls': 30, 'max_fallbacks': 3, 'max_runtime_minutes': 15, 'max_parallel_nodes': 5, 'budget_check_enabled': True}

# ---------------------------------------------------------------------------
# LLM config helper — reads Hermes config.yaml for model endpoint
# ---------------------------------------------------------------------------

_HERMES_CONFIG_PATH = os.path.expanduser("~/.hermes/config.yaml")
_HERMES_ENV_PATH = os.path.expanduser("~/.hermes/.env")
_env_loaded = False

def _ensure_hermes_env() -> None:
    """Load env vars from ~/.hermes/.env if not already present."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if not os.path.isfile(_HERMES_ENV_PATH):
        return
    try:
        with open(_HERMES_ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

def _read_hermes_llm_config() -> dict:
    """Read LLM config from Hermes config.yaml.

    Returns a dict with keys ``base_url``, ``model``, ``provider``.
    Falls back to environment variables, then hard-coded defaults.
    """
    _ensure_hermes_env()
    cfg: dict[str, str] = {
        "base_url": os.environ.get("HERMES_LLM_BASE_URL", ""),
        "model": os.environ.get("HERMES_LLM_MODEL", ""),
        "provider": os.environ.get("HERMES_LLM_PROVIDER", ""),
    }

    if _yaml is None or not os.path.isfile(_HERMES_CONFIG_PATH):
        return cfg

    try:
        with open(_HERMES_CONFIG_PATH) as _f:
            data = _yaml.safe_load(_f) or {}
        m = data.get("model", {})
        if isinstance(m, dict):
            # base_url from config takes priority over env
            bu = m.get("base_url")
            if bu:
                cfg["base_url"] = bu.rstrip("/")
            # model name: config.default > aliases lookup > env fallback
            mdl = m.get("default") or cfg["model"]
            cfg["model"] = mdl
            prv = m.get("provider") or cfg["provider"]
            cfg["provider"] = prv
        logger.debug("planner: LLM config from yaml — base_url=%s model=%s provider=%s",
                     cfg["base_url"], cfg["model"], cfg["provider"])
    except Exception as exc:
        logger.debug("planner: failed to read %s: %s", _HERMES_CONFIG_PATH, exc)

    # Resolve provider-specific base_url if not set
    if not cfg["base_url"] and cfg["provider"]:
        provider_upper = cfg["provider"].upper()
        cfg["base_url"] = os.environ.get(f"{provider_upper}_BASE_URL", "")

    # Final fallback defaults
    if not cfg["base_url"]:
        cfg["base_url"] = "https://api.deepseek.com/v1"
    if not cfg["model"]:
        cfg["model"] = "deepseek-chat"

    return cfg

@dataclass
class PlanStep:
    """A single atomic step within a plan.

    Attributes
    ----------
    id : str
        Unique step identifier (e.g. ``"step_001"``).
    action : str
        Tool capability name, e.g. ``"web_search"``, ``"file_read"``.
    params : dict
        Parameters to pass to the tool when executing this step.
    depends_on : list[str]
        Step IDs that must complete before this step can run.
    timeout : int
        Maximum execution time in seconds (default 300).
    retry_policy : dict or None
        Retry configuration::

            {"max_retries": 3, "backoff": "exponential"}

    fallback : str or None
        Step ID of an alternative step to try if this one fails.
    validation : str or None
        Optional validation expression or function name.
    estimated_cost : str
        One of ``"low"``, ``"medium"``, ``"high"``.
    risk : str
        One of ``"none"``, ``"low"``, ``"medium"``, ``"high"``.
    """
    id: str
    action: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    timeout: int = 300
    retry_policy: dict | None = None
    fallback: str | None = None
    validation: str | None = None
    estimated_cost: str = 'low'
    risk: str = 'low'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'PlanStep':
        return cls(**data)

@dataclass
class Plan:
    """A complete execution plan composed of ordered steps.

    Attributes
    ----------
    plan_id : str
        UUID identifying this plan.
    goal : str
        The original high-level goal being planned for.
    steps : list[PlanStep]
        Ordered list of steps to execute.
    fallbacks : dict
        Mapping of ``{step_id: alternative_step_id}``.
    constraints : dict
        Policy and resource constraints that apply to this plan, e.g.::

            {"max_runtime": 1200, "max_requests_per_domain": 30}

    estimated_cost : dict
        Cost estimate breakdown, e.g.::

            {"total": "medium", "api_calls": 3, "browser_sessions": 0,
             "estimated_seconds": 120}

    risk_assessment : dict
        Risk assessment, e.g.::

            {"overall": "medium", "high_risk_steps": 1,
             "network_dependent": 3, "domain_risks": {...}}

    created_at : str
        ISO-8601 UTC timestamp of plan creation.
    """
    plan_id: str
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    fallbacks: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    estimated_cost: dict = field(default_factory=dict)
    risk_assessment: dict = field(default_factory=dict)
    created_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {'plan_id': self.plan_id, 'goal': self.goal, 'steps': [s.to_dict() for s in self.steps], 'fallbacks': dict(self.fallbacks), 'constraints': dict(self.constraints), 'estimated_cost': dict(self.estimated_cost), 'risk_assessment': dict(self.risk_assessment), 'created_at': self.created_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Plan':
        steps = [PlanStep.from_dict(s) for s in data.get('steps', [])]
        return cls(plan_id=data.get('plan_id', ''), goal=data.get('goal', ''), steps=steps, fallbacks=data.get('fallbacks', {}), constraints=data.get('constraints', {}), estimated_cost=data.get('estimated_cost', {}), risk_assessment=data.get('risk_assessment', {}), created_at=data.get('created_at', ''))

def _compute_max_dependency_depth(steps: list[PlanStep]) -> int:
    """Compute the longest dependency chain depth in a list of steps."""
    depth_map: dict[str, int] = {}
    step_dict = {s.id: s for s in steps}

    def _depth_of(step_id: str, visited: set[str] | None=None) -> int:
        if visited is None:
            visited = set()
        if step_id in visited:
            return 0
        if step_id in depth_map:
            return depth_map[step_id]
        step = step_dict.get(step_id)
        if not step or not step.depends_on:
            depth_map[step_id] = 1
            return 1
        visited.add(step_id)
        max_dep = max((_depth_of(d, visited) for d in step.depends_on), default=0)
        visited.discard(step_id)
        depth_map[step_id] = max_dep + 1
        return depth_map[step_id]
    for s in steps:
        _depth_of(s.id)
    return max(depth_map.values()) if depth_map else 0

def _count_fallback_paths(fallbacks: dict[str, str]) -> int:
    """Count the number of distinct fallback chains."""
    if not fallbacks:
        return 0
    reverse: dict[str, str] = {}
    for primary, alt in fallbacks.items():
        reverse[alt] = primary
    chains: set[str] = set()
    for alt_id in reverse:
        chain_root = alt_id
        while chain_root in reverse:
            chain_root = reverse[chain_root]
        chains.add(chain_root)
    return len(chains)

def validate_plan_budget(plan: Plan, budget: dict | None=None) -> list[str]:
    """Check *plan* against budget limits.

    Parameters
    ----------
    plan : Plan
        The plan to validate.
    budget : dict or None
        Budget overrides.  If ``None``, uses the global ``PLANNING_BUDGET``.

    Returns
    -------
    list[str]
        List of budget violation descriptions.  Empty list = within budget.
    """
    if budget is None:
        budget = dict(PLANNING_BUDGET)
    if not budget.get('budget_check_enabled', True):
        return []
    violations: list[str] = []
    steps: list[PlanStep] = plan.steps
    fallbacks: dict[str, str] = plan.fallbacks
    max_depth = int(budget.get('max_plan_depth', 5))
    actual_depth = _compute_max_dependency_depth(steps)
    if actual_depth > max_depth:
        violations.append(f'Plan depth {actual_depth} exceeds max_plan_depth {max_depth}')
    max_steps = int(budget.get('max_reasoning_steps', 20))
    if len(steps) > max_steps:
        violations.append(f'Step count {len(steps)} exceeds max_reasoning_steps {max_steps}')
    max_fallbacks_count = int(budget.get('max_fallbacks', 3))
    actual_fallbacks = _count_fallback_paths(fallbacks)
    if actual_fallbacks > max_fallbacks_count:
        violations.append(f'Fallback path count {actual_fallbacks} exceeds max_fallbacks {max_fallbacks_count}')
    max_runtime_s = int(budget.get('max_runtime_minutes', 15)) * 60
    total_timeout_s = sum((s.timeout for s in steps))
    if total_timeout_s > max_runtime_s:
        violations.append(f'Estimated runtime {total_timeout_s}s exceeds max_runtime {max_runtime_s}s ({max_runtime_s // 60} min)')
    return violations
_instance: Optional['Planner'] = None
_instance_lock = threading.Lock()

def get_planner() -> 'Planner':
    """Return the application-wide Planner singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Planner()
        return _instance


def reset_planner_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None
def plan(goal: str, context: dict | None=None) -> dict:
    """Convenience: decompose *goal* into a plan and return it as a dict.

    Parameters
    ----------
    goal : str
        The high-level goal to plan for.
    context : dict or None
        Optional context (world_state, task constraints, etc.).

    Returns
    -------
    dict
        The generated Plan serialised to a dictionary.
    """
    planner = get_planner()
    p = planner.plan(goal, context=context)
    return p.to_dict()

class Planner:
    """Goal decomposition and planning engine (singleton).

    Decomposes high-level goals into actionable, constraint-checked plans
    with fallbacks, risk assessment, and cost estimation.

    Usage
    -----
    >>> p = Planner()
    >>> result = p.plan("collect rental data near 斗南")
    >>> result.steps
    [PlanStep(id='step_001', action='web_search', ...), ...]
    """

    def __init__(self) -> None:
        if getattr(self, '_initialized', False):
            return
        self._lock = threading.Lock()
        self._logger: Optional[EventLogger] = None
        self._plans: dict[str, Plan] = {}
        self._decomposition_cache: dict[str, list[dict]] = {}
        self._strategy_preferences: list[str] = []
        self._tool_preferences: dict[str, float] = {}
        self._domain_experience_count: int = 0
        self._initialized = True

    def plan(self, goal: str, world_state: dict | None=None, context: dict | None=None) -> Plan:
        """THE main method — decompose a *goal* into a full Plan.

        Steps
        -----
        a) Get world state (or use provided)
        b) Decompose goal into sub-goals
        c) For each sub-goal, select the best tool from ToolRegistry
        d) Check constraints (policy engine, resource limits)
        e) Look up similar past plans from ExperienceManager
        f) Generate PlanSteps with dependencies, fallbacks, retry policies
        g) Assess risk and cost
        h) Return Plan

        Parameters
        ----------
        goal : str
            The high-level goal to achieve (e.g. ``"collect rental data"``).
        world_state : dict or None
            Pre-fetched world state.  If ``None``, a fresh snapshot is taken.
        context : dict or None
            Optional contextual data such as task constraints, preferences,
            or environmental hints.

        Returns
        -------
        Plan
            A fully constructed Plan object.
        """
        ctx = context or {}
        plan_id = self._new_id('plan')
        if world_state is None:
            try:
                wm = get_world_model()
                world_state = wm.get_world_state(refresh=True)
            except Exception:
                world_state = {}
        self._log('plan.start', {'plan_id': plan_id, 'goal': goal, 'has_world_state': bool(world_state)})
        sub_goals = self._decompose_goal(goal, ctx)
        steps: list[PlanStep] = []
        fallbacks: dict[str, str] = {}
        for i, sg in enumerate(sub_goals):
            step_id = f'step_{i + 1:03d}'
            tool_name = self._select_tool(sg, ctx)
            cap = self._get_tool_capability(tool_name)
            risk = cap.risk if cap else 'medium'
            cost = cap.cost if cap else 'medium'
            timeout = cap.timeout_s if cap else 300
            depends = [f'step_{j + 1:03d}' for j in range(i)] if i > 0 else []
            params = self._build_params(sg, ctx, tool_name)
            retry_policy = {'max_retries': 3, 'backoff': 'exponential'}
            step = PlanStep(id=step_id, action=tool_name, params=params, depends_on=depends, timeout=timeout, retry_policy=retry_policy, fallback=None, validation=None, estimated_cost=cost, risk=risk)
            steps.append(step)
        similar_plans = self._find_similar_plans(goal, sub_goals)
        if similar_plans:
            steps = self._apply_experience(steps, similar_plans)
        steps = self._apply_strategy_preferences(goal, sub_goals, steps)
        fallbacks = self._generate_fallbacks(goal, steps)
        risk_assessment = self._assess_risk(steps)
        cost_estimate = self._estimate_cost(steps)
        constraints = self._build_constraints(world_state)
        plan_obj = Plan(plan_id=plan_id, goal=goal, steps=steps, fallbacks=fallbacks, constraints=constraints, estimated_cost=cost_estimate, risk_assessment=risk_assessment, created_at=self._timestamp())
        plan_obj = self._apply_constraints(plan_obj)
        context_budget = ctx.get('budget', None)
        if context_budget is not None:
            active_budget = dict(PLANNING_BUDGET)
            active_budget.update(context_budget)
        else:
            active_budget = None
        violations = validate_plan_budget(plan_obj, active_budget)
        if violations:
            self._log('budget.violations', {'plan_id': plan_id, 'violations': violations})
            plan_obj = self._trim_overbudget_plan(plan_obj, active_budget)
            remaining = validate_plan_budget(plan_obj, active_budget)
            if remaining:
                self._log('budget.violations_remaining', {'plan_id': plan_id, 'remaining_violations': remaining})
        with self._lock:
            self._plans[plan_id] = plan_obj
        self._log('plan.complete', {'plan_id': plan_id, 'goal': goal, 'step_count': len(steps), 'overall_risk': risk_assessment.get('overall', 'unknown'), 'estimated_cost_total': cost_estimate.get('total', 'unknown')})
        return plan_obj

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Retrieve a cached plan by its ID."""
        with self._lock:
            return self._plans.get(plan_id)

    def refine_plan(self, plan_id: str, feedback: dict) -> Plan:
        """Accept feedback from execution and refine the plan.

        Used by the OODA loop to adjust plans based on runtime outcomes.

        Parameters
        ----------
        plan_id : str
            The ID of the plan to refine.
        feedback : dict
            Feedback data.  Supported keys:

            * ``failed_step`` (str) — step ID that failed
            * ``error`` (str) — error description
            * ``suggestion`` (str) — alternative approach
            * ``adjusted_params`` (dict) — param overrides
            * ``skip_steps`` (list[str]) — steps to remove

        Returns
        -------
        Plan
            The refined Plan (the original is updated in-place).
        """
        with self._lock:
            plan_obj = self._plans.get(plan_id)
            if plan_obj is None:
                raise HermesCoreError(f"Plan '{plan_id}' not found in cache")
            failed_step_id = feedback.get('failed_step')
            suggestion = feedback.get('suggestion', '')
            adjusted_params = feedback.get('adjusted_params', {})
            skip_steps = feedback.get('skip_steps', [])
            if skip_steps:
                plan_obj.steps = [s for s in plan_obj.steps if s.id not in skip_steps]
            for step in plan_obj.steps:
                if step.id == failed_step_id:
                    if suggestion and suggestion != step.action:
                        alt_tool = self._select_tool({'description': suggestion, 'original_action': step.action}, {})
                        if alt_tool and alt_tool != step.action:
                            step.action = alt_tool
                            cap = self._get_tool_capability(alt_tool)
                            if cap:
                                step.risk = cap.risk
                                step.estimated_cost = cap.cost
                                step.timeout = cap.timeout_s
                    if adjusted_params:
                        step.params.update(adjusted_params)
                    if step.fallback and step.fallback in [s.id for s in plan_obj.steps]:
                        fallback_step = next((s for s in plan_obj.steps if s.id == step.fallback))
                        step.action = fallback_step.action
                        step.params = dict(fallback_step.params)
                        step.risk = fallback_step.risk
                        step.estimated_cost = fallback_step.estimated_cost
            plan_obj.risk_assessment = self._assess_risk(plan_obj.steps)
            plan_obj.estimated_cost = self._estimate_cost(plan_obj.steps)
            self._log('plan.refined', {'plan_id': plan_id, 'failed_step': failed_step_id, 'suggestion': suggestion, 'steps_after': len(plan_obj.steps)})
            return plan_obj

    def list_plans(self) -> list[dict]:
        """Return a summary of all cached plans."""
        with self._lock:
            return [{'plan_id': pid, 'goal': p.goal, 'step_count': len(p.steps), 'created_at': p.created_at} for pid, p in self._plans.items()]

    def set_budget(self, key: str, value: int | bool) -> None:
        """Override a planning budget parameter.

        Parameters
        ----------
        key : str
            Budget key (e.g. ``"max_plan_depth"``, ``"budget_check_enabled"``).
        value : int or bool
            New value for the budget parameter.
        """
        if key in PLANNING_BUDGET:
            PLANNING_BUDGET[key] = value
            self._log('budget.set', {'key': key, 'value': value})
        else:
            raise HermesCoreError(f"Unknown budget key '{key}'. Valid keys: {list(PLANNING_BUDGET.keys())}")

    def get_budget(self) -> dict:
        """Return the current planning budget configuration.

        Returns
        -------
        dict
            A copy of the global ``PLANNING_BUDGET``.
        """
        return dict(PLANNING_BUDGET)

    def get_adaptation_stats(self) -> dict:
        """Return current adaptation statistics.

        Provides insight into how experience is influencing planning
        decisions, including strategy preferences, tool preference scores,
        and the number of domain experiences consulted.

        Returns
        -------
        dict
            ``{
                'strategy_preferences': [str],   # strategies influencing plans
                'tool_preferences': {tool: score},  # current tool scores
                'domain_experience_count': int
            }``
        """
        with self._lock:
            return {'strategy_preferences': list(self._strategy_preferences), 'tool_preferences': dict(self._tool_preferences), 'domain_experience_count': self._domain_experience_count}

    def _trim_overbudget_plan(self, plan: Plan, budget: dict | None=None) -> Plan:
        """Trim a plan that exceeds the budget to fit within limits.

        Strategies (applied in order):
          1. Merge consecutive low-risk file_read / terminal_exec steps
          2. Flatten dependency chains where possible
          3. Keep only the first fallback for each risky step
          4. Trim excess steps if still over max_reasoning_steps

        Parameters
        ----------
        plan : Plan
            The plan to trim (modified in-place).
        budget : dict or None
            Budget to trim against.  If ``None`` uses the global ``PLANNING_BUDGET``.

        Returns
        -------
        Plan
            The trimmed plan.
        """
        if budget is None:
            budget = dict(PLANNING_BUDGET)
        trimmed: list[str] = []
        mergeable_actions = {'file_read', 'terminal_exec'}
        merged: list[PlanStep] = []
        i = 0
        while i < len(plan.steps):
            current = plan.steps[i]
            if i + 1 < len(plan.steps) and current.action in mergeable_actions and (plan.steps[i + 1].action in mergeable_actions) and (current.action == plan.steps[i + 1].action):
                next_step = plan.steps[i + 1]
                merged_params = dict(current.params)
                merged_params.update(next_step.params)
                merged_step = PlanStep(id=current.id, action=current.action, params=merged_params, depends_on=current.depends_on, timeout=current.timeout + next_step.timeout, retry_policy=current.retry_policy, fallback=current.fallback, estimated_cost=current.estimated_cost, risk=current.risk)
                merged.append(merged_step)
                trimmed.append(f"Merged '{current.id}' + '{next_step.id}' ({current.action})")
                i += 2
            else:
                merged.append(current)
                i += 1
        if trimmed:
            plan.steps = merged
        max_depth = int(budget.get('max_plan_depth', 5))
        actual_depth = _compute_max_dependency_depth(plan.steps)
        if actual_depth > max_depth:
            step_map = {s.id: s for s in plan.steps}
            for s in plan.steps:
                if len(s.depends_on) > 1:
                    old_deps = list(s.depends_on)
                    s.depends_on = [old_deps[-1]]
                    trimmed.append(f"Flattened deps for '{s.id}': [{','.join(old_deps)}] -> [{s.depends_on[0]}]")
            if _compute_max_dependency_depth(plan.steps) > max_depth:
                for idx, s in enumerate(plan.steps):
                    if idx > 0:
                        old_deps = list(s.depends_on)
                        s.depends_on = [plan.steps[idx - 1].id]
                        if old_deps != s.depends_on:
                            trimmed.append(f"Aggressively flattened deps for '{s.id}'")
        max_fallbacks_count = int(budget.get('max_fallbacks', 3))
        if len(plan.fallbacks) > max_fallbacks_count:
            ordered = list(plan.fallbacks.items())
            plan.fallbacks = dict(ordered[:max_fallbacks_count])
            trimmed.append(f'Trimmed fallbacks from {len(ordered)} to {max_fallbacks_count}')
            active_fallback_ids = set(plan.fallbacks.values())
            plan.steps = [s for s in plan.steps if not (s.id.endswith('_fallback') and s.id not in active_fallback_ids)]
        max_steps = int(budget.get('max_reasoning_steps', 20))
        if len(plan.steps) > max_steps:
            removed = len(plan.steps) - max_steps
            plan.steps = plan.steps[:max_steps]
            trimmed.append(f'Trimmed {removed} steps to fit max_reasoning_steps {max_steps}')
        if trimmed:
            self._log('budget.trimmed', {'plan_id': plan.plan_id, 'actions': trimmed})
        return plan

    def _decompose_goal(self, goal: str, context: dict) -> list[dict]:
        """Break down high-level goal into primitive sub-goals.

        Uses LLM-driven decomposition as the primary strategy, with
        hardcoded pattern matching and generic fallback as backups.

        Results are cached for repeated goals.

        Parameters
        ----------
        goal : str
            The user's goal (e.g. "collect rental data near 斗南").
        context : dict
            Optional context that may influence decomposition.

        Returns
        -------
        list[dict]
            Each dict has keys ``description``, ``domain``, and optionally
            ``target_url``, ``keywords``, or other hints.
        """
        cache_key = goal.strip().lower()
        with self._lock:
            cached = self._decomposition_cache.get(cache_key)
            if cached is not None:
                return [dict(c) for c in cached]

        sub_goals = self._llm_decompose_goal(goal, context)
        if not sub_goals:
            # Fallback: hardcoded pattern matching (covers rental/search/code/file/browse)
            sub_goals = self._match_decomposition_patterns(goal.strip().lower(), context)
        if not sub_goals:
            # Last resort: generic template
            sub_goals = self._generic_decomposition(goal, context)
        experience_informed = self._enrich_with_experience(sub_goals, context)
        with self._lock:
            if len(self._decomposition_cache) >= _DEFAULT_DECOMPOSITION_CACHE_SIZE:
                oldest = next(iter(self._decomposition_cache))
                del self._decomposition_cache[oldest]
            self._decomposition_cache[cache_key] = [dict(sg) for sg in experience_informed]
        return experience_informed

    def _llm_decompose_goal(self, goal: str, context: dict) -> list[dict]:
        """Use an LLM to decompose a goal into structured sub-goals.

        Constructs a prompt asking the LLM to return a JSON array of steps.
        Reads model endpoint from Hermes config.yaml (with env var fallback).
        Respects HTTP_PROXY/HTTPS_PROXY for corporate / Clash proxy setups.
        Retries up to 3 times on transient failures.
        Falls back gracefully (empty list) on any persistent error.

        Returns
        -------
        list[dict]
            Each dict has keys ``description``, ``domain``, and optionally
            ``target_url``, ``keywords``, or other hints.
            Empty list on failure.
        """
        api_key = (
            os.environ.get("HERMES_LLM_API_KEY")
            or os.environ.get("XIAOMI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        )
        if not api_key:
            logger.debug("planner: LLM decomposition skipped — no API key")
            return []
        logger.debug("planner: using API key prefix=%s...", api_key[:10])

        # Read endpoint from config.yaml with env/static fallback
        llm_cfg = _read_hermes_llm_config()
        base_url = llm_cfg["base_url"]
        model = llm_cfg["model"]

        context_str = json.dumps(context, ensure_ascii=False) if context else "{}"

        prompt = (
            'You are a task decomposition engine. Break the following goal into a '
            'sequence of 2-8 actionable sub-goals. Each sub-goal must be a concrete, '
            'executable step that an AI agent with terminal, file, web, browser, and '
            'code execution capabilities can perform.\n\n'
            f'Goal: {goal}\n'
            f'Context: {context_str}\n\n'
            'Return ONLY a JSON array of objects. Each object must have:\n'
            '  - "description": clear action description (str)\n'
            '  - "domain": one of "web", "browser", "code", "file", "data", "analysis", "general"\n'
            '  - "keywords": list of search keywords or relevant terms (optional)\n'
            '  - "target_sites": list of target website names (optional, only for web domain)\n\n'
            'Example:\n'
            '[\n'
            '  {"description": "Search the web for relevant information", '
            '"domain": "web", "keywords": ["topic"], "target_sites": ["google"]},\n'
            '  {"description": "Extract and compile findings", '
            '"domain": "data"}\n'
            ']\n\n'
            'Return ONLY the JSON array, no other text, no markdown.'
        )

        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a precise task decomposition engine."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        }).encode("utf-8")

        # Build request with proxy support
        endpoint = f"{base_url}/chat/completions"
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        # Enable proxy: respect HTTP_PROXY/HTTPS_PROXY env vars (Clash, corporate)
        proxy_support = urllib.request.ProxyHandler()
        opener = urllib.request.build_opener(proxy_support)

        # Retry loop — 3 attempts with exponential backoff
        last_exc = None
        body = None
        for attempt in range(1, 4):
            try:
                with opener.open(req, timeout=90) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                last_exc = None
                break
            except (urllib.error.URLError, urllib.error.HTTPError,
                    json.JSONDecodeError, OSError, TimeoutError) as exc:
                last_exc = exc
                if attempt < 3:
                    wait = 2 ** (attempt - 1)  # 1s, 2s
                    logger.warning("planner: LLM API attempt %d/3 failed: %s — retrying in %ds",
                                   attempt, exc, wait)
                    time.sleep(wait)
                else:
                    logger.warning("planner: LLM decomposition failed after 3 attempts: %s", exc)

        if body is None:
            logger.debug("planner: LLM decomposition empty after %d attempts", attempt)
            return []

        try:
            msg = body["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            logger.debug("planner: LLM decomposition parse error: %s", exc)
            return []

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            # Remove ```json ... ``` or ``` ... ```
            lines = content.split("\n")
            content = "\n".join(
                line for line in lines
                if not line.startswith("```")
            ).strip()

        try:
            steps = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.debug("planner: LLM decomposition JSON parse error: %s", exc)
            return []

        if not isinstance(steps, list):
            logger.debug("planner: LLM decomposition returned non-list")
            return []

        # Validate and normalize
        valid_domains = {"web", "browser", "code", "file", "data", "analysis", "general"}
        validated = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            desc = step.get("description", "").strip()
            if not desc:
                continue
            domain = step.get("domain", "general")
            if domain not in valid_domains:
                domain = "general"
            entry = {"description": desc, "domain": domain}
            if step.get("keywords"):
                entry["keywords"] = step["keywords"]
            if step.get("target_sites"):
                entry["target_sites"] = step["target_sites"]
            validated.append(entry)

        if not validated:
            logger.debug("planner: LLM decomposition returned zero valid steps")
            return []

        logger.info("planner: LLM decomposed '%s' into %d steps", goal[:60], len(validated))
        return validated

    def _match_decomposition_patterns(self, goal_lower: str, context: dict) -> list[dict]:
        """Match goal text against known domain patterns."""
        sub_goals: list[dict] = []
        if any((kw in goal_lower for kw in ('rental', 'rent', '租房', '出租', '斗南', 'listing', '房源'))):
            target_sites = context.get('target_sites', [])
            if 'douban' in goal_lower or '豆瓣' in goal_lower:
                target_sites.append('douban')
            if 'beike' in goal_lower or '贝壳' in goal_lower or '链家' in goal_lower:
                target_sites.append('beike')
            if '58' in goal_lower or '同城' in goal_lower:
                target_sites.append('58city')
            if 'anjuke' in goal_lower or '安居客' in goal_lower:
                target_sites.append('anjuke')
            target_sites = list(dict.fromkeys(target_sites))
            if not target_sites:
                target_sites = ['web_search']
            sub_goals.append({'description': f"Search target sites: {', '.join(target_sites)}", 'domain': 'web', 'target_sites': target_sites})
            sub_goals.append({'description': 'Extract and parse listing data from pages', 'domain': 'web'})
            sub_goals.append({'description': 'Deduplicate and normalise listings', 'domain': 'data'})
            sub_goals.append({'description': 'Summarise results', 'domain': 'data'})
            return sub_goals
        if any((kw in goal_lower for kw in ('search', 'find', 'look up', 'research', 'investigate', '查询', '搜索', '查找'))):
            sub_goals.append({'description': 'Search the web for relevant information', 'domain': 'web'})
            sub_goals.append({'description': 'Extract and compile findings', 'domain': 'web'})
            sub_goals.append({'description': 'Summarise results', 'domain': 'data'})
            return sub_goals
        if any((kw in goal_lower for kw in ('code', 'script', 'run', 'execute', 'python', 'bash', '代码', '运行', '执行'))):
            sub_goals.append({'description': 'Write or load the code/script', 'domain': 'code'})
            sub_goals.append({'description': 'Execute the code/script', 'domain': 'code'})
            sub_goals.append({'description': 'Capture and return output', 'domain': 'code'})
            return sub_goals
        if any((kw in goal_lower for kw in ('file', 'read', 'write', 'edit', 'create', '文件', '读取', '写入'))):
            sub_goals.append({'description': 'Locate the target file', 'domain': 'file'})
            sub_goals.append({'description': 'Read or modify file content', 'domain': 'file'})
            return sub_goals
        if any((kw in goal_lower for kw in ('browse', 'navigate', 'click', 'form', 'login', '浏览器', '点击', '表单'))):
            sub_goals.append({'description': 'Navigate to the target page', 'domain': 'browser'})
            sub_goals.append({'description': 'Interact with page elements', 'domain': 'browser'})
            sub_goals.append({'description': 'Extract resulting data', 'domain': 'browser'})
            return sub_goals
        return sub_goals

    def _generic_decomposition(self, goal: str, context: dict) -> list[dict]:
        """Fallback: produce a sensible generic decomposition.

        .. deprecated::
            This is a hardcoded 4-step template that does not use real
            experience or world-model context.  Phase 3 should replace
            with a call to the Planner's experience-informed decomposition
            or PolicyEngine fallback.  See :mod:`plan_executor`.
        """
        logger.warning(
            "DEPRECATED: _generic_decomposition() — hardcoded 4-step template "
            "used as decomposition fallback.  Phase 3: replace with "
            "experience-informed or PolicyEngine-driven decomposition."
        )
        return [{'description': f'Analyse goal: {goal}', 'domain': 'analysis'}, {'description': 'Gather required information', 'domain': 'general'}, {'description': 'Execute primary action', 'domain': 'general'}, {'description': 'Verify and summarise outcome', 'domain': 'general'}]

    def _enrich_with_experience(self, sub_goals: list[dict], context: dict) -> list[dict]:
        """Enrich sub-goals with hints from past successful patterns."""
        try:
            exp = get_experience()
        except Exception:
            return sub_goals
        enriched = []
        for sg in sub_goals:
            domain = sg.get('domain', '')
            if domain:
                try:
                    strategies = exp.get_strategies(domain=domain, min_success_rate=0.3)
                    if strategies:
                        best = strategies[0]
                        sg['experience_hint'] = {'pattern_name': best.get('pattern_name'), 'action_sequence': best.get('action_sequence'), 'success_count': best.get('success_count', 0)}
                except Exception as exc:
                    logger.debug('planner: _enrich_with_experience: %s', exc)
            enriched.append(sg)
        return enriched

    def _select_tool(self, step: dict, context: dict) -> str:
        """Find the best tool capability for a given sub-goal step.

        Considers: success_rate (from experience), cost, risk, auth
        requirements, and network requirements.

        Parameters
        ----------
        step : dict
            Sub-goal descriptor with at least ``description`` and ``domain``.
        context : dict
            Additional constraints (e.g. ``max_cost``, ``max_risk``).

        Returns
        -------
        str
            The selected capability name.
        """
        description = step.get('description', '')
        domain = step.get('domain', 'general')
        target_sites = step.get('target_sites', [])
        filters: dict = {}
        max_cost = context.get('max_cost')
        max_risk = context.get('max_risk')
        if max_cost:
            filters['max_cost'] = max_cost
        if max_risk:
            filters['max_risk'] = max_risk
        keyword = self._domain_to_keyword(domain, description)
        candidates: list[ToolCapability] = []
        try:
            registry = _get_tool_registry()
            if registry is not None:
                candidates = registry.find(keyword, filters=filters)
        except Exception as exc:
            logger.debug('planner: _select_tool: %s', exc)
        if not candidates:
            try:
                registry = _get_tool_registry()
                if registry is not None and len(registry) == 0:
                    registry.register_defaults()
                    candidates = registry.find(keyword, filters=filters)
            except Exception as exc:
                logger.debug('planner: _select_tool: %s', exc)
        if candidates:
            experience_rates: dict[str, float] = {}
            try:
                exp = get_experience()
                for cap in candidates:
                    stats = exp.get_tool_stats(tool_name=cap.name, domain=domain)
                    if stats and 'success_rate' in stats:
                        experience_rates[cap.name] = float(stats['success_rate'])
            except Exception as exc:
                logger.debug('planner: _select_tool: %s', exc)
            scored: list[tuple[float, str]] = []
            for cap in candidates:
                base_rate = getattr(cap, 'success_rate', 0.5)
                historical_rate = experience_rates.get(cap.name, base_rate)
                risk_int = _RISK_ORDER.get(cap.risk, 2)
                max_risk_int = 3
                risk_score = 1.0 - risk_int / max_risk_int
                cost_int = _COST_ORDER.get(cap.cost, 2)
                max_cost_int = 3
                cost_score = 1.0 - cost_int / max_cost_int
                score_value = 0.5 * historical_rate + 0.3 * risk_score + 0.2 * cost_score
                if historical_rate < 0.3:
                    score_value *= 0.5
                scored.append((score_value, cap.name))
            self._tool_preferences = {name: round(s, 4) for s, name in scored}
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]
        return self._domain_fallback_tool(domain)

    def _score_capability(self, cap: ToolCapability, step: dict, context: dict) -> float:
        """Return a numeric score (higher = better) for a capability.

        Factors (weighted):
          - success_rate (0.4)
          - cost (0.2) — lower is better
          - risk (0.2) — lower is better
          - description match bonus (0.1)
          - experience hint alignment (0.1)
        """
        score = 0.0
        score += cap.success_rate * 0.4
        cost_int = _COST_ORDER.get(cap.cost, 2)
        max_cost_int = 3
        score += (1.0 - cost_int / max_cost_int) * 0.2
        risk_int = _RISK_ORDER.get(cap.risk, 2)
        max_risk_int = 3
        score += (1.0 - risk_int / max_risk_int) * 0.2
        step_desc = step.get('description', '').lower()
        if step_desc and (cap.name.lower() in step_desc or any((t in step_desc for t in cap.tools))):
            score += 0.1
        hint = step.get('experience_hint', {})
        if hint:
            action_seq = hint.get('action_sequence', [])
            if isinstance(action_seq, list) and cap.name in action_seq:
                score += 0.1
        return score

    @staticmethod
    def _domain_to_keyword(domain: str, description: str) -> str:
        """Map a domain/capability code to a search keyword."""
        domain_map = {'web': 'web', 'browser': 'browser', 'data': 'db_query' if 'data' else 'web', 'file': 'file', 'code': 'code', 'analysis': 'code_exec', 'general': 'terminal'}
        base = domain_map.get(domain, 'terminal')
        desc_lower = description.lower()
        for kw in ('search', 'scrape', 'extract'):
            if kw in desc_lower:
                return f'web_{kw}' if kw == 'search' else f'web_{kw}'
        if 'execute' in desc_lower or 'run' in desc_lower:
            return 'terminal_exec'
        return base

    @staticmethod
    def _domain_fallback_tool(domain: str) -> str:
        """Fallback tool name when registry lookup fails."""
        fallback_map = {'web': 'web_search', 'browser': 'browser_interact', 'data': 'db_query', 'file': 'file_read', 'code': 'code_exec', 'analysis': 'code_exec', 'general': 'terminal_exec'}
        return fallback_map.get(domain, 'terminal_exec')

    @staticmethod
    def _get_tool_capability(name: str) -> ToolCapability | None:
        """Look up a ToolCapability by name from the registry."""
        try:
            registry = _get_tool_registry()
            if registry is not None:
                return registry.get(name)
        except Exception as exc:
            logger.debug('planner: _get_tool_capability: %s', exc)
        return None

    @staticmethod
    def _build_params(sub_goal: dict, context: dict, tool_name: str) -> dict:
        """Build the parameter dict for a tool from the sub-goal and context."""
        params: dict = {}
        description = sub_goal.get('description', '')
        domain = sub_goal.get('domain', '')
        params['query'] = description
        params['task'] = description
        if domain == 'web' or tool_name in ('web_search', 'web_scrape'):
            params['query'] = description
            target_sites = sub_goal.get('target_sites', [])
            if target_sites:
                params['target_sites'] = target_sites
        elif domain == 'file':
            params['path'] = context.get('file_path', context.get('path', ''))
        elif domain == 'code':
            params['code'] = context.get('code', '')
            params['language'] = context.get('language', 'python')
        extra = context.get('params', {})
        if extra:
            params.update(extra)
        return params

    def _apply_constraints(self, plan: Plan) -> Plan:
        """Check each step against PolicyEngine and apply resource constraints.

        - Checks each step action against policy
        - Adds timeouts based on tool registry
        - Ensures max_runtime, max_requests_per_domain are respected
        - Breaks plan into parallelizable batches where possible

        Parameters
        ----------
        plan : Plan
            The plan to constrain.

        Returns
        -------
        Plan
            The constrained plan (modified in-place).
        """
        try:
            engine = get_policy_engine()
        except Exception:
            engine = None
        limits = {}
        if engine is not None:
            try:
                summary = engine.get_summary()
                limits = summary.get('limits', {})
            except Exception as exc:
                logger.debug('planner: _apply_constraints: %s', exc)
        constrained_steps: list[PlanStep] = []
        for step in plan.steps:
            if engine is not None:
                try:
                    policy_ctx = {'timeout': step.timeout, 'domain': step.params.get('domain', step.params.get('query', ''))}
                    result = engine.check_action(step.action, policy_ctx)
                    if not result.get('allowed', True):
                        if result.get('severity') == 'deny':
                            raise PolicyViolation(f"Step '{step.id}' action '{step.action}' denied by policy: {result.get('reason', 'unknown')}")
                        if step.risk != 'high':
                            step.risk = 'medium'
                except PolicyViolation:
                    raise
                except Exception as exc:
                    logger.debug('planner: _apply_constraints: %s', exc)
            max_runtime = limits.get('max_runtime_minutes', 20) * 60
            if step.timeout > max_runtime:
                step.timeout = max_runtime
            max_retry = limits.get('max_retry_per_step', 3)
            if step.retry_policy:
                step.retry_policy['max_retries'] = min(step.retry_policy.get('max_retries', 3), max_retry)
            constrained_steps.append(step)
        plan.steps = constrained_steps
        plan.constraints = {'max_runtime_seconds': limits.get('max_runtime_minutes', 20) * 60, 'max_requests_per_domain': limits.get('max_requests_per_domain', 30), 'max_retry_per_step': limits.get('max_retry_per_step', 3), 'max_concurrent_tasks': limits.get('max_concurrent_tasks', 2), 'default_risk_threshold': limits.get('default_risk_threshold', 'medium')}
        plan = self._batch_parallel_steps(plan)
        return plan

    @staticmethod
    def _batch_parallel_steps(plan: Plan) -> Plan:
        """Identify steps that can run in parallel and adjust dependencies.

        Two steps can be parallelised if they have no transitive dependency
        relationship and don't conflict on shared resources (network, browser).
        """
        step_map = {s.id: s for s in plan.steps}
        deps_of: dict[str, set[str]] = {}
        for s in plan.steps:
            deps_of[s.id] = set(s.depends_on)
        network_steps: set[str] = set()
        browser_steps: set[str] = set()
        for s in plan.steps:
            cap = Planner._get_tool_capability(s.action)
            if cap:
                if cap.requires_network:
                    network_steps.add(s.id)
                if cap.requires_browser:
                    browser_steps.add(s.id)
        changed = True
        while changed:
            changed = False
            for s in plan.steps:
                if not s.depends_on:
                    continue
                can_relax = True
                for dep_id in list(s.depends_on):
                    dep = step_map.get(dep_id)
                    if dep is None:
                        continue
                    if s.id in network_steps and dep.id in network_steps or (s.id in browser_steps and dep.id in browser_steps):
                        can_relax = False
                        break
                if can_relax and len(s.depends_on) > 1:
                    s.depends_on = [s.depends_on[-1]]
                    changed = True
        return plan

    def _generate_fallbacks(self, goal: str, steps: list[PlanStep]) -> dict[str, str]:
        """For each risky step, generate an alternative approach.

        Fallback strategy hierarchy:
            1. If scrape/web fails -> try cached version -> try search -> manual takeover
            2. If network-dependent tool fails -> try local variant
            3. If high-risk step fails -> try lower-risk alternative

        Adaptive ordering: failures patterns from ExperienceManager are used
        to order fallbacks so that historically successful ones come first.

        Parameters
        ----------
        goal : str
            The original goal (used for context).
        steps : list[PlanStep]
            The plan steps.

        Returns
        -------
        dict
            Mapping of ``{step_id: alternative_step_id}``.  Alternative steps
            are appended to *steps* if they don't already exist.
        """
        fallbacks: dict[str, str] = {}
        steps_snapshot = list(steps)
        step_ids = {s.id for s in steps}
        fallback_success_rates: dict[str, float] = {}
        domain_failure_count: dict[str, int] = {}
        try:
            exp = get_experience()
            for step in steps_snapshot:
                domain = step.action
                try:
                    failures = exp.get_known_failures(domain=domain)
                    if failures:
                        domain_failure_count[domain] = len(failures)
                    for alt_action_name in ('web_search', 'terminal_exec', 'file_read', 'db_query'):
                        stats = exp.get_tool_stats(tool_name=alt_action_name, domain=domain)
                        if stats and 'success_rate' in stats:
                            fallback_success_rates[alt_action_name] = float(stats['success_rate'])
                except Exception as exc:
                    logger.debug('planner: _generate_fallbacks: %s', exc)
        except Exception as exc:
            logger.debug('planner: _generate_fallbacks: %s', exc)
        candidate_fallbacks: list[tuple[str, str, float]] = []
        for step in steps_snapshot:
            risk_int = _RISK_ORDER.get(step.risk, 0)
            if risk_int >= 2 or step.action in ('web_scrape', 'browser_interact'):
                alt_action = self._fallback_for(step.action)
                if alt_action and alt_action != step.action:
                    success_rate = fallback_success_rates.get(alt_action, 0.5)
                    domain_failures = domain_failure_count.get(step.action, 0)
                    failure_boost = min(float(domain_failures) * 0.05, 0.2)
                    priority = success_rate + failure_boost
                    candidate_fallbacks.append((step.id, alt_action, priority))
        candidate_fallbacks.sort(key=lambda x: x[2], reverse=True)
        for step_id, alt_action, _priority in candidate_fallbacks:
            alt_id = f'{step_id}_fallback'
            if alt_id not in step_ids:
                original = next((s for s in steps_snapshot if s.id == step_id), None)
                if original is None:
                    continue
                alt_step = PlanStep(id=alt_id, action=alt_action, params=dict(original.params), depends_on=list(original.depends_on), timeout=min(original.timeout * 2, 600), retry_policy={'max_retries': 1, 'backoff': 'linear'}, estimated_cost=original.estimated_cost, risk=original.risk)
                steps.append(alt_step)
                step_ids.add(alt_id)
            fallbacks[step_id] = alt_id
        return fallbacks

    @staticmethod
    def _fallback_for(action: str) -> str:
        """Map a primary action to its fallback alternative."""
        fallback_map: dict[str, str] = {'web_scrape': 'web_search', 'web_search': 'terminal_exec', 'browser_interact': 'web_search', 'code_exec': 'terminal_exec', 'vision_analyze': 'file_read', 'db_query': 'terminal_exec'}
        return fallback_map.get(action, '')

    def _assess_risk(self, steps: list[PlanStep]) -> dict:
        """Assess overall plan risk based on individual step risks.

        Factors:
        a) Number of high-risk steps
        b) Network dependencies
        c) Website risk levels (from WorldModel)
        d) Success rates of selected tools

        Parameters
        ----------
        steps : list[PlanStep]
            The steps to assess.

        Returns
        -------
        dict
            Assessment with keys: ``overall``, ``high_risk_steps``,
            ``medium_risk_steps``, ``low_risk_steps``, ``network_dependent``,
            ``domain_risks``, ``average_success_rate``.
        """
        high_count = 0
        medium_count = 0
        low_count = 0
        network_deps = 0
        total_success_rate = 0.0
        success_counted = 0
        domain_risks: dict[str, str] = {}
        for step in steps:
            risk_int = _RISK_ORDER.get(step.risk, 0)
            if risk_int >= 3:
                high_count += 1
            elif risk_int >= 2:
                medium_count += 1
            else:
                low_count += 1
            cap = self._get_tool_capability(step.action)
            if cap and cap.requires_network:
                network_deps += 1
            if cap and cap.call_count > 0:
                total_success_rate += cap.success_rate
                success_counted += 1
            domain = step.params.get('domain', '') or step.params.get('query', '')
            if domain:
                try:
                    wm = get_world_model()
                    risk_info = wm.get_website_risk(domain)
                    if risk_info:
                        domain_risks[domain] = risk_info.get('risk_level', 'unknown')
                except Exception as exc:
                    logger.debug('planner: _assess_risk: %s', exc)
        avg_success = round(total_success_rate / success_counted, 4) if success_counted > 0 else 0.0
        if high_count > 0:
            overall = 'high'
        elif medium_count >= len(steps) / 2 or network_deps >= len(steps):
            overall = 'medium'
        else:
            overall = 'low'
        return {'overall': overall, 'high_risk_steps': high_count, 'medium_risk_steps': medium_count, 'low_risk_steps': low_count, 'network_dependent': network_deps, 'domain_risks': domain_risks, 'average_success_rate': avg_success, 'total_steps': len(steps)}

    def _estimate_cost(self, steps: list[PlanStep]) -> dict:
        """Estimate total cost of executing all steps.

        Factors considered:
        - API calls (network tools)
        - Browser sessions
        - Estimated wall-clock time
        - Tool-specific cost levels

        Parameters
        ----------
        steps : list[PlanStep]
            The plan steps.

        Returns
        -------
        dict
            Estimate with keys: ``total``, ``api_calls``, ``browser_sessions``,
            ``estimated_seconds``, ``cost_breakdown``.
        """
        api_calls = 0
        browser_sessions = 0
        total_seconds = 0
        cost_levels: list[int] = []
        for step in steps:
            total_seconds += step.timeout
            cap = self._get_tool_capability(step.action)
            if cap:
                cost_levels.append(_COST_ORDER.get(cap.cost, 1))
                if cap.requires_network:
                    api_calls += 1
                if cap.requires_browser:
                    browser_sessions += 1
            else:
                cost_levels.append(1)
        if cost_levels:
            avg_cost_int = sum(cost_levels) / len(cost_levels)
        else:
            avg_cost_int = 1.0
        if avg_cost_int <= 0.5:
            total = 'free'
        elif avg_cost_int <= 1.5:
            total = 'low'
        elif avg_cost_int <= 2.5:
            total = 'medium'
        else:
            total = 'high'
        cost_breakdown: dict[str, str] = {}
        for step in steps:
            cap = self._get_tool_capability(step.action)
            cost_breakdown[step.id] = cap.cost if cap else 'medium'
        return {'total': total, 'api_calls': api_calls, 'browser_sessions': browser_sessions, 'estimated_seconds': total_seconds, 'cost_breakdown': cost_breakdown}

    def _find_similar_plans(self, goal: str, sub_goals: list[dict]) -> list[dict]:
        """Query ExperienceManager for patterns similar to the current goal.

        Returns
        -------
        list[dict]
            Similar successful patterns ordered by relevance.
        """
        similar: list[dict] = []
        try:
            exp = get_experience()
        except Exception:
            return similar
        domains = list(dict.fromkeys((sg.get('domain', '') for sg in sub_goals if sg.get('domain'))))
        for domain in domains:
            try:
                strategies = exp.get_strategies(domain=domain, min_success_rate=0.5)
                similar.extend(strategies)
            except Exception as exc:
                logger.debug('planner: _find_similar_plans: %s', exc)
        seen: set[str] = set()
        unique: list[dict] = []
        for s in similar:
            pname = s.get('pattern_name', '')
            if pname and pname not in seen:
                seen.add(pname)
                unique.append(s)
        return unique[:10]

    def _apply_experience(self, steps: list[PlanStep], similar_plans: list[dict]) -> list[PlanStep]:
        """Adjust steps based on experience from similar past plans.

        - If experience shows a different tool works better for a domain, prefer it.
        - If experience shows known failure patterns, adjust params to mitigate.
        """
        if not similar_plans:
            return steps
        tool_success: dict[str, float] = {}
        known_failures: dict[str, list[str]] = {}
        try:
            exp = get_experience()
            for sp in similar_plans:
                action_seq = sp.get('action_sequence', [])
                if isinstance(action_seq, list):
                    for tool_name in action_seq:
                        stats = exp.get_tool_stats(tool_name=tool_name)
                        if stats and 'success_rate' in stats:
                            tool_success[tool_name] = stats['success_rate']
            for step in steps:
                try:
                    failures = exp.get_known_failures(domain=step.action)
                    if failures:
                        known_failures[step.id] = [f.get('error_type', '') for f in failures[:3]]
                except Exception as exc:
                    logger.debug('planner: _apply_experience: %s', exc)
        except Exception:
            return steps
        for step in steps:
            if step.action in tool_success and tool_success[step.action] < 0.5:
                for other_tool, rate in tool_success.items():
                    if rate > tool_success[step.action] and rate >= 0.7:
                        step.action = other_tool
                        cap = self._get_tool_capability(other_tool)
                        if cap:
                            step.risk = cap.risk
                            step.estimated_cost = cap.cost
                            step.timeout = cap.timeout_s
                        break
            if step.id in known_failures:
                failures = known_failures[step.id]
                if 'timeout' in ' '.join(failures).lower():
                    step.timeout = min(int(step.timeout * 1.5), 600)
                if 'auth' in ' '.join(failures).lower():
                    step.retry_policy = {'max_retries': 1, 'backoff': 'linear'}
                    step.params['force_no_auth'] = True
        return steps

    def _apply_strategy_preferences(self, goal: str, sub_goals: list[dict], steps: list[PlanStep]) -> list[PlanStep]:
        """Check ExperienceManager for high-confidence strategies that match
        the current goal, and reorder steps to match their action sequence.

        Parameters
        ----------
        goal : str
            The original high-level goal.
        sub_goals : list[dict]
            The decomposed sub-goals for this plan.
        steps : list[PlanStep]
            The current ordered step list.

        Returns
        -------
        list[PlanStep]
            Steps potentially reordered to match a preferred strategy.
        """
        try:
            exp = get_experience()
        except Exception:
            return steps
        domains = list(dict.fromkeys((sg.get('domain', '') for sg in sub_goals if sg.get('domain'))))
        self._strategy_preferences = []
        self._domain_experience_count = 0
        best_strategy: dict | None = None
        best_priority: float = 0.0
        for domain in domains:
            try:
                strategies = exp.get_strategies(domain=domain, min_success_rate=0.7)
                self._domain_experience_count += len(strategies)
                for strategy in strategies:
                    priority = float(strategy.get('success_rate', 0)) * float(strategy.get('success_count', 0))
                    action_seq = strategy.get('action_sequence', [])
                    if isinstance(action_seq, list) and len(action_seq) > 1:
                        strategy_domain = strategy.get('domain', '')
                        if strategy_domain == domain and priority > best_priority:
                            best_strategy = strategy
                            best_priority = priority
            except Exception:
                continue
        if best_strategy is not None:
            action_seq: list[str] = best_strategy.get('action_sequence', [])
            seq_name = best_strategy.get('pattern_name', 'unknown')
            self._strategy_preferences.append(seq_name)
            self._log('strategy.preference_applied', {'pattern_name': seq_name, 'domain': best_strategy.get('domain', ''), 'priority': best_priority, 'action_sequence': action_seq})
            reordered: list[PlanStep] = []
            matched_ids: set[str] = set()
            for preferred_action in action_seq:
                for step in steps:
                    if step.id not in matched_ids and step.action == preferred_action:
                        reordered.append(step)
                        matched_ids.add(step.id)
            for step in steps:
                if step.id not in matched_ids:
                    reordered.append(step)
            if len(reordered) == len(steps):
                return reordered
        return steps

    def _build_constraints(self, world_state: dict) -> dict:
        """Build a constraints dict from world state and defaults."""
        constraints: dict = {'max_runtime_seconds': 1200, 'max_requests_per_domain': 30, 'max_retry_per_step': 3, 'max_concurrent_tasks': 2}
        if world_state:
            memory = world_state.get('memory', {})
            mem_percent = memory.get('percent', 0)
            if mem_percent and mem_percent > 80:
                constraints['max_concurrent_tasks'] = 1
            disk = world_state.get('disk', {})
            disk_percent = disk.get('percent', 0)
            if disk_percent and disk_percent > 85:
                constraints['max_concurrent_tasks'] = 1
        try:
            engine = get_policy_engine()
            summary = engine.get_summary()
            limits = summary.get('limits', {})
            if limits:
                constraints['max_runtime_seconds'] = limits.get('max_runtime_minutes', 20) * 60
                constraints['max_requests_per_domain'] = limits.get('max_requests_per_domain', 30)
                constraints['default_risk_threshold'] = limits.get('default_risk_threshold', 'medium')
        except Exception as exc:
            logger.debug('planner: _build_constraints: %s', exc)
        return constraints

    def _log(self, event_type: str, data: dict) -> None:
        """Write a structured event to the NDJSON log."""
        try:
            logger = get_logger()
            logger.log(f'planner.{event_type}', data)
        except Exception as exc:
            logger.debug('planner: _log: %s', exc)

    @staticmethod
    def _new_id(prefix: str='plan') -> str:
        """Generate a unique ID string."""
        return f'{prefix}_{uuid.uuid4().hex[:12]}'

    @staticmethod
    def _timestamp() -> str:
        """Return ISO-8601 UTC timestamp string."""
        return datetime.now(timezone.utc).isoformat()
__all__ = ['Planner', 'Plan', 'PlanStep', 'get_planner', 'plan']