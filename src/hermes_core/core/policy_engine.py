"""

import logging

logger = logging.getLogger(__name__)

policy_engine.py — Hermes Core Policy Engine.

Enforces safety, resource, and alignment policies for all tool/action
invocations.  Checks forbidden actions, risk thresholds, tool-specific
rules, domain blocks, and resource limits against a configurable policy
file (``~/.hermes/core/config/policy.yaml``).

Typical usage::

    from hermes.core.policy_engine import check_action, get_policy_engine

    engine = get_policy_engine()
    result = check_action("terminal_exec", {"command": "ls -la"})
    if not result["allowed"]:
        print(f"Blocked: {result['reason']}")
"""
from __future__ import annotations
import fnmatch
import json
import os
import pathlib
import threading
from typing import Any, Dict, List, Optional
try:
    from .exceptions import PolicyViolation, ResourceLimitExceeded
    from .event_logger import get_logger
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from exceptions import PolicyViolation, ResourceLimitExceeded
    from event_logger import get_logger
_CONFIG_DIR = pathlib.Path.home() / '.hermes' / 'core' / 'config'
_CONFIG_PATH = _CONFIG_DIR / 'policy.yaml'
_CONFIG_PATH_JSON = _CONFIG_DIR / 'policy.json'
_RISK_ORDER: dict[str, int] = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}
PolicyResult = Dict[str, Any]
'\nA dictionary with the following keys:\n    allowed      — bool; ``True`` if the action is permitted\n    reason       — str; human-readable explanation\n    severity     — str; one of ``"allow"``, ``"prompt"``, ``"deny"``\n    suggestions  — list[str]; optional remediation suggestions\n'

def _policy_result(allowed: bool, reason: str='', severity: str='allow', suggestions: Optional[List[str]]=None) -> PolicyResult:
    """Build a standardised PolicyResult dict."""
    return {'allowed': allowed, 'reason': reason, 'severity': severity, 'suggestions': suggestions or []}
_DEFAULT_CONFIG: Dict[str, Any] = {'version': 1, 'forbidden_actions': ['captcha_bypass', 'destructive_shell', 'infinite_loop', 'credential_harvest', 'unauthorized_port_scan'], 'limits': {'max_runtime_minutes': 20, 'max_requests_per_domain': 30, 'max_parallel_browsers': 3, 'max_retry_per_step': 3, 'max_concurrent_tasks': 2, 'max_memory_percent': 85, 'max_disk_percent': 90}, 'default_risk_threshold': 'medium', 'require_confirmation': [{'risk': 'high'}, {'type': 'destructive_shell'}, {'type': 'captcha_bypass'}], 'tool_specific': {'terminal_exec': {'max_timeout': 300, 'blocked_commands': ['rm -rf /', 'mkfs', 'dd if=']}, 'browser_interact': {'max_pages': 10, 'block_domains': ['*paypal*', '*bank*']}, 'code_exec': {'max_timeout': 600, 'blocked_modules': ['subprocess', 'os.system']}}}

def _risk_int(level: str) -> int:
    """Map a risk string to an integer (unknown -> high)."""
    return _RISK_ORDER.get(level.strip().lower(), 3)

def _domain_matches(pattern: str, domain: str) -> bool:
    """Return True if *domain* matches the glob *pattern*."""
    return fnmatch.fnmatch(domain.lower(), pattern.lower())
_instances: Dict[str, 'PolicyEngine'] = {}
_instances_lock = threading.Lock()

class PolicyEngine:
    """Central policy enforcement engine (singleton).

    Loads policy from ``~/.hermes/core/config/policy.yaml`` (or ``.json``
    fallback), validates it, and provides ``check_action()`` for every
    tool/action invocation in the Hermes runtime.
    """

    def __new__(cls, config_path: Optional[str]=None) -> 'PolicyEngine':
        key = config_path or 'default'
        with _instances_lock:
            if key not in _instances:
                obj = super().__new__(cls)
                obj._initialized = False
                _instances[key] = obj
            return _instances[key]

    def __init__(self, config_path: Optional[str]=None) -> None:
        """Initialise the policy engine.

        Parameters
        ----------
        config_path : str or None
            Explicit path to a policy config file.  If ``None``, the default
            location ``~/.hermes/core/config/policy.yaml`` is used.
        """
        if getattr(self, '_initialized', False):
            return

        self._lock = threading.Lock()
        self._custom_path: Optional[pathlib.Path] = pathlib.Path(config_path).expanduser().resolve() if config_path else None
        self._config: Dict[str, Any] = {}
        self._config_path: Optional[pathlib.Path] = None
        self._tool_registry = None
        self._load_or_create()
        self._initialized = True

    def get_config_path(self) -> pathlib.Path:
        """Return the path to the active config file.

        Creates a default config at the canonical location if none exists.
        """
        p = self._resolve_config_path()
        if not p.exists():
            self._create_config_at(p)
        return p

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instances_lock:
            _instances.clear()

    def create_default_config(self) -> pathlib.Path:
        """Write the default policy config to disk and return its path.

        Uses the canonical location (``~/.hermes/core/config/policy.yaml``)
        unless a custom path was provided at construction time.
        """
        p = self._resolve_config_path()
        self._create_config_at(p)
        return p

    def load_config(self) -> Dict[str, Any]:
        """Parse the config file and return the validated dictionary.

        Raises
        ------
        ValueError
            If the config structure is invalid (missing required keys).
        OSError
            If the file cannot be read and no fallback is available.
        """
        p = self._resolve_config_path()
        config = self._parse_file(p)
        self._validate_config(config)
        return config

    def reload_config(self) -> None:
        """Re-read the config file from disk and apply the updated rules.

        This is safe to call at runtime — the engine uses a read/write lock
        so that concurrent ``check_action()`` calls see a consistent snapshot.
        """
        with self._lock:
            self._config = self.load_config()
        logger = get_logger()
        logger.log('policy.reload', {'config_path': str(self._config_path)}, severity='info')

    def check_action(self, action_type: str, context: Optional[Dict[str, Any]]=None) -> PolicyResult:
        """Evaluate whether *action_type* is permitted in the given *context*.

        The checks run in order:
        1. Is the action in ``forbidden_actions``?               → DENY
        2. Is the tool's risk level above the threshold?         → PROMPT or DENY
        3. Are any tool-specific rules violated?                 → DENY
        4. Domain-based rules (browser_interact only)?           → DENY or PROMPT
        5. Are global resource limits reached?                   → DENY

        Parameters
        ----------
        action_type : str
            The capability / action name, e.g. ``"terminal_exec"``,
            ``"web_search"``, ``"code_exec"``.
        context : dict or None
            Optional contextual data.  Supported keys:

            * ``command`` (str) — the shell command being run
            * ``domain`` (str) — URL domain for browser actions
            * ``url`` (str) — full URL for browser actions
            * ``code`` (str) — code snippet for code_exec
            * ``modules`` (list[str]) — imported modules for code_exec
            * ``timeout`` (int) — requested timeout in seconds
            * ``pages`` (int) — number of pages to interact with
            * ``parallel_browsers`` (int) — number of parallel browser instances
            * ``requests_per_domain`` (int) — requests count for this domain

        Returns
        -------
        PolicyResult
            ``{"allowed": bool, "reason": str, "severity": str, "suggestions": list}``
        """
        ctx = context or {}
        action = action_type.strip().lower()
        with self._lock:
            config = dict(self._config)
        forbidden = config.get('forbidden_actions', [])
        if action in [f.strip().lower() for f in forbidden]:
            return _policy_result(allowed=False, reason=f"Action '{action_type}' is unconditionally forbidden by policy.", severity='deny', suggestions=[f'Remove {action_type} from your plan or use an alternative approach.'])
        risk = self._get_tool_risk(action)
        threshold = config.get('default_risk_threshold', 'medium')
        if _risk_int(risk) > _risk_int(threshold):
            return _policy_result(allowed=False, reason=f"Action '{action_type}' has risk level '{risk}' which exceeds the configured threshold '{threshold}'.  User confirmation required.", severity='prompt', suggestions=[f'Set default_risk_threshold higher in policy config, or request user approval for this action.'])
        confirm_rules = config.get('require_confirmation', [])
        for rule in confirm_rules:
            if not isinstance(rule, dict):
                continue
            rule_risk = rule.get('risk', '').strip().lower()
            if rule_risk and risk == rule_risk:
                return _policy_result(allowed=True, reason=f"Action '{action_type}' at risk level '{risk}' requires user confirmation.", severity='prompt', suggestions=['Await user approval before proceeding.'])
            rule_type = rule.get('type', '').strip().lower()
            if rule_type and action == rule_type:
                return _policy_result(allowed=True, reason=f"Action type '{action_type}' requires user confirmation per policy.", severity='prompt', suggestions=['Await user approval before proceeding.'])
        tool_rules = config.get('tool_specific', {}).get(action, {})
        if tool_rules:
            result = self._check_tool_specific(action, tool_rules, ctx)
            if result is not None:
                return result
        domain = ctx.get('domain', '') or self._extract_domain(ctx)
        if domain:
            domain_rules = config.get('tool_specific', {}).get(action, {}).get('block_domains', [])
            for pattern in domain_rules:
                if _domain_matches(pattern, domain):
                    return _policy_result(allowed=False, reason=f"Domain '{domain}' matches blocked pattern '{pattern}' for action '{action_type}'.", severity='deny', suggestions=[f'Remove access to blocked domains or use a different tool.'])
        limits = config.get('limits', {})
        limit_result = self._check_resource_limits(action, limits, ctx)
        if limit_result is not None:
            return limit_result
        return _policy_result(allowed=True, reason='Action permitted by policy.', severity='allow')

    def check_action_batch(self, actions: List[tuple]) -> List[PolicyResult]:
        """Check multiple actions in a batch.

        Parameters
        ----------
        actions : list of (action_type, context) tuples
            Each tuple is ``(action_type: str, context: dict | None)``.

        Returns
        -------
        list of PolicyResult
            One result per input tuple, in the same order.
        """
        return [self.check_action(at, ctx) for at, ctx in actions]

    def get_summary(self) -> Dict[str, Any]:
        """Return a snapshot of the current policy configuration.

        Returns
        -------
        dict
            Keys: ``version``, ``forbidden_actions_count``, ``limits``,
            ``default_risk_threshold``, ``require_confirmation``,
            ``tool_specific_keys``, ``config_path``.
        """
        with self._lock:
            cfg = self._config
            return {'version': cfg.get('version', 'unknown'), 'forbidden_actions_count': len(cfg.get('forbidden_actions', [])), 'limits': dict(cfg.get('limits', {})), 'default_risk_threshold': cfg.get('default_risk_threshold', 'medium'), 'require_confirmation': list(cfg.get('require_confirmation', [])), 'tool_specific_keys': list(cfg.get('tool_specific', {}).keys()), 'config_path': str(self._config_path) if self._config_path else None}

    # ------------------------------------------------------------------
    # Runtime policy adjustment — called by DriftAnalyzer / ActiveDefense
    # ------------------------------------------------------------------

    def update_policy(self, action: str, tool_name: str = "",
                      adjust: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Adjust policy rules at runtime (thread-safe).

        Parameters
        ----------
        action : str
            One of:
            - ``"block_tool"`` — add tool to tool-specific ``blocked_commands``
            - ``"unblock_tool"`` — remove tool from blocked list
            - ``"raise_threshold"`` — increase risk threshold for a domain/tool
            - ``"lower_threshold"`` — decrease risk threshold
            - ``"add_forbidden"`` — add an action to ``forbidden_actions``
            - ``"remove_forbidden"`` — remove from ``forbidden_actions``
            - ``"tune_limit"`` — adjust a numeric resource limit
        tool_name : str
            Target tool (e.g. ``"terminal_exec"``).  May be empty for
            global actions.
        adjust : dict or None
            Action-specific parameters (see examples).

        Examples
        --------
        >>> engine.update_policy("block_tool", "terminal_exec",
        ...                      {"blocked_command": "rm -rf"})
        >>> engine.update_policy("raise_threshold",
        ...                      {"new_risk": "high"})
        >>> engine.update_policy("add_forbidden",
        ...                      {"action": "web_search"})
        >>> engine.update_policy("tune_limit",
        ...                      {"limit": "max_runtime_minutes",
        ...                       "value": 10})

        Returns
        -------
        dict
            ``{"success": bool, "message": str, "changed": dict}``
        """
        adjust = adjust or {}
        changed: Dict[str, Any] = {}

        with self._lock:
            cfg = self._config

            if action == "block_tool" and tool_name:
                tool_specific = cfg.setdefault("tool_specific", {})
                rules = tool_specific.setdefault(tool_name, {})
                blocked = rules.setdefault("blocked_commands", [])
                cmd = adjust.get("blocked_command", "")
                if cmd and cmd not in blocked:
                    blocked.append(cmd)
                    changed["blocked_commands"] = list(blocked)
                return {"success": True,
                        "message": f"Blocked '{cmd}' for {tool_name}",
                        "changed": changed}

            elif action == "unblock_tool" and tool_name:
                tool_specific = cfg.get("tool_specific", {})
                rules = tool_specific.get(tool_name, {})
                blocked = rules.get("blocked_commands", [])
                cmd = adjust.get("blocked_command", "")
                if cmd and cmd in blocked:
                    blocked.remove(cmd)
                    changed["blocked_commands"] = list(blocked)
                return {"success": True,
                        "message": f"Unblocked '{cmd}' for {tool_name}",
                        "changed": changed}

            elif action == "raise_threshold":
                old = cfg.get("default_risk_threshold", "medium")
                new = adjust.get("new_risk", "high")
                if new in _RISK_ORDER and _RISK_ORDER[new] > _RISK_ORDER.get(old, 0):
                    cfg["default_risk_threshold"] = new
                    changed["default_risk_threshold"] = new
                return {"success": True,
                        "message": f"Risk threshold {old} → {new}",
                        "changed": changed}

            elif action == "lower_threshold":
                old = cfg.get("default_risk_threshold", "medium")
                new = adjust.get("new_risk", "low")
                if new in _RISK_ORDER and _RISK_ORDER[new] < _RISK_ORDER.get(old, 3):
                    cfg["default_risk_threshold"] = new
                    changed["default_risk_threshold"] = new
                return {"success": True,
                        "message": f"Risk threshold {old} → {new}",
                        "changed": changed}

            elif action == "add_forbidden":
                name = adjust.get("action", "")
                forbidden = cfg.setdefault("forbidden_actions", [])
                if name and name not in forbidden:
                    forbidden.append(name)
                    changed["forbidden_actions"] = list(forbidden)
                return {"success": True,
                        "message": f"Added '{name}' to forbidden_actions",
                        "changed": changed}

            elif action == "remove_forbidden":
                name = adjust.get("action", "")
                forbidden = cfg.get("forbidden_actions", [])
                if name and name in forbidden:
                    forbidden.remove(name)
                    changed["forbidden_actions"] = list(forbidden)
                return {"success": True,
                        "message": f"Removed '{name}' from forbidden_actions",
                        "changed": changed}

            elif action == "tune_limit":
                limit_name = adjust.get("limit", "")
                value = adjust.get("value")
                limits = cfg.setdefault("limits", {})
                if limit_name and value is not None:
                    old_val = limits.get(limit_name)
                    limits[limit_name] = int(value)
                    changed["limits"] = {limit_name: {"old": old_val, "new": int(value)}}
                return {"success": True,
                        "message": f"Limit '{limit_name}' → {value}",
                        "changed": changed}

            else:
                return {"success": False,
                        "message": f"Unknown policy action: '{action}'",
                        "changed": changed}

    def adjust_thresholds(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        """Apply multiple threshold adjustments atomically (thread-safe).

        Parameters
        ----------
        changes : dict
            Mapping of config key → new value.  Supported keys match the
            top-level policy config keys (``default_risk_threshold``,
            ``limits.*``, etc.).

        Returns
        -------
        dict
            ``{"success": bool, "applied": list[str], "errors": list[str]}``
        """
        applied: List[str] = []
        errors: List[str] = []

        with self._lock:
            cfg = self._config

            for key, value in changes.items():
                try:
                    if key == "default_risk_threshold":
                        if value in _RISK_ORDER:
                            cfg[key] = value
                            applied.append(f"{key}={value}")
                        else:
                            errors.append(f"Invalid risk '{value}'")
                    elif key.startswith("limits."):
                        sub_key = key[len("limits."):]
                        limits = cfg.setdefault("limits", {})
                        limits[sub_key] = int(value)
                        applied.append(f"limits.{sub_key}={value}")
                    elif key == "forbidden_actions":
                        if isinstance(value, list):
                            cfg[key] = value
                            applied.append(f"forbidden_actions ({len(value)} items)")
                        else:
                            errors.append("forbidden_actions must be a list")
                    else:
                        errors.append(f"Unknown key '{key}'")
                except Exception as exc:
                    errors.append(f"{key}: {exc}")

        return {"success": len(errors) == 0,
                "applied": applied,
                "errors": errors}

    def _check_tool_specific(self, action: str, rules: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[PolicyResult]:
        """Apply tool-specific rules and return a PolicyResult or ``None``."""
        if action == 'terminal_exec':
            command = (ctx.get('command') or '').strip()
            blocked = rules.get('blocked_commands', [])
            for pattern in blocked:
                pattern_stripped = pattern.strip()
                if pattern_stripped and pattern_stripped in command:
                    return _policy_result(allowed=False, reason=f"Command contains blocked pattern '{pattern_stripped}'.", severity='deny', suggestions=['Remove the flagged command fragment or use a safer approach.'])
            max_to = rules.get('max_timeout')
            req_to = ctx.get('timeout')
            if max_to is not None and req_to is not None and (req_to > max_to):
                return _policy_result(allowed=False, reason=f"Requested timeout {req_to}s exceeds the maximum allowed timeout of {max_to}s for '{action}'.", severity='deny', suggestions=[f'Reduce timeout to ≤ {max_to}s.'])
        if action == 'browser_interact':
            max_pages = rules.get('max_pages')
            req_pages = ctx.get('pages')
            if max_pages is not None and req_pages is not None and (req_pages > max_pages):
                return _policy_result(allowed=False, reason=f"Requested {req_pages} pages exceeds the maximum allowed {max_pages} pages for '{action}'.", severity='deny', suggestions=[f'Reduce the number of pages to ≤ {max_pages}.'])
        if action == 'code_exec':
            blocked_modules = rules.get('blocked_modules', [])
            modules = ctx.get('modules', []) or []
            code = ctx.get('code', '') or ''
            for blocked_mod in blocked_modules:
                mod_name = blocked_mod.strip()
                if not mod_name:
                    continue
                if any((mod_name in m for m in modules)):
                    return _policy_result(allowed=False, reason=f"Code uses blocked module '{mod_name}'.", severity='deny', suggestions=[f"Replace '{mod_name}' with a safer alternative."])
                for keyword in (f'import {mod_name}', f'from {mod_name}'):
                    if keyword in code:
                        return _policy_result(allowed=False, reason=f"Code contains blocked import '{keyword}'.", severity='deny', suggestions=[f"Remove 'import {mod_name}' from the code."])
            max_to = rules.get('max_timeout')
            req_to = ctx.get('timeout')
            if max_to is not None and req_to is not None and (req_to > max_to):
                return _policy_result(allowed=False, reason=f"Requested timeout {req_to}s exceeds the maximum allowed timeout of {max_to}s for '{action}'.", severity='deny', suggestions=[f'Reduce code execution timeout to ≤ {max_to}s.'])
        return None

    def _check_resource_limits(self, action: str, limits: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[PolicyResult]:
        """Check global resource limits and return a PolicyResult or ``None``."""
        max_browsers = limits.get('max_parallel_browsers')
        req_browsers = ctx.get('parallel_browsers')
        if max_browsers is not None and req_browsers is not None and (req_browsers > max_browsers):
            return _policy_result(allowed=False, reason=f'Requested {req_browsers} parallel browser instances exceeds the limit of {max_browsers}.', severity='deny', suggestions=[f'Reduce parallel browsers to ≤ {max_browsers}.'])
        max_req = limits.get('max_requests_per_domain')
        req_count = ctx.get('requests_per_domain')
        if max_req is not None and req_count is not None and (req_count > max_req):
            return _policy_result(allowed=False, reason=f'Request count {req_count} for this domain exceeds the limit of {max_req}.', severity='deny', suggestions=[f'Reduce requests or wait for the rate limit to reset.'])
        max_tasks = limits.get('max_concurrent_tasks')
        req_tasks = ctx.get('concurrent_tasks')
        if max_tasks is not None and req_tasks is not None and (req_tasks > max_tasks):
            return _policy_result(allowed=False, reason=f'Requested {req_tasks} concurrent tasks exceeds the limit of {max_tasks}.', severity='deny', suggestions=[f'Reduce concurrency to ≤ {max_tasks} tasks.'])
        max_runtime = limits.get('max_runtime_minutes')
        elapsed = ctx.get('runtime_minutes')
        if max_runtime is not None and elapsed is not None and (elapsed > max_runtime):
            return _policy_result(allowed=False, reason=f'Elapsed runtime {elapsed:.1f} minutes exceeds the limit of {max_runtime} minutes.', severity='deny', suggestions=[f'Wrap up the current task or request a runtime extension.'])
        max_retry = limits.get('max_retry_per_step')
        retries = ctx.get('retry_count')
        if max_retry is not None and retries is not None and (retries > max_retry):
            return _policy_result(allowed=False, reason=f'Retry count {retries} exceeds the limit of {max_retry} per step.', severity='deny', suggestions=[f'Try an alternative approach instead of retrying.'])
        max_mem = limits.get('max_memory_percent')
        mem_pct = ctx.get('memory_percent') or self._get_system_memory_percent()
        if max_mem is not None and mem_pct is not None and (mem_pct > max_mem):
            return _policy_result(allowed=False, reason=f'System memory usage {mem_pct:.0f}% exceeds the limit of {max_mem}%.', severity='deny', suggestions=[f'Free up memory or close other applications.'])
        max_disk = limits.get('max_disk_percent')
        disk_pct = ctx.get('disk_percent') or self._get_system_disk_percent()
        if max_disk is not None and disk_pct is not None and (disk_pct > max_disk):
            return _policy_result(allowed=False, reason=f'System disk usage {disk_pct:.0f}% exceeds the limit of {max_disk}%.', severity='deny', suggestions=[f'Free up disk space before proceeding.'])
        return None

    def _get_tool_risk(self, action_type: str) -> str:
        """Look up the registered risk level for *action_type*.

        Falls back to ``"medium"`` if the ToolRegistry is not initialised
        or the capability is not registered.
        """
        try:
            if self._tool_registry is None:
                try:
                    from .tool_registry import ToolRegistry
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    from tool_registry import ToolRegistry
                self._tool_registry = ToolRegistry()
            cap = self._tool_registry.get(action_type)
            if cap is not None:
                return cap.risk
        except Exception as exc:
            logger.debug('policy_engine: _get_tool_risk: %s', exc)
        return 'medium'

    @staticmethod
    def _get_system_memory_percent() -> Optional[float]:
        """Return current system memory usage as a percentage (0–100).

        Uses ``/proc/meminfo`` on Linux.  Returns ``None`` if unavailable.
        """
        try:
            with open('/proc/meminfo') as f:
                lines = f.readlines()
            mem_total = None
            mem_available = None
            for line in lines:
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1])
                if mem_total is not None and mem_available is not None:
                    break
            if mem_total and mem_available:
                return (mem_total - mem_available) / mem_total * 100.0
        except (OSError, IndexError, ValueError):
            pass
        return None

    @staticmethod
    def _get_system_disk_percent() -> Optional[float]:
        """Return disk usage percentage for the root filesystem.

        Returns ``None`` if ``os.statvfs`` is not available.
        """
        try:
            s = os.statvfs('/')
            total = s.f_frsize * s.f_blocks
            free = s.f_frsize * s.f_bfree
            if total > 0:
                return (total - free) / total * 100.0
        except (AttributeError, OSError):
            pass
        return None

    def _resolve_config_path(self) -> pathlib.Path:
        """Return the config path to use (custom or default)."""
        if self._custom_path:
            return self._custom_path
        return _CONFIG_PATH

    def _load_or_create(self) -> None:
        """Try to load config; create default if missing."""
        p = self._resolve_config_path()
        if p.exists():
            try:
                self._config = self._parse_file(p)
                self._validate_config(self._config)
                self._config_path = p
                return
            except (ValueError, OSError):
                pass
        json_path = _CONFIG_PATH_JSON
        if json_path.exists():
            try:
                self._config = self._parse_file(json_path)
                self._validate_config(self._config)
                self._config_path = json_path
                return
            except (ValueError, OSError):
                pass
        p_dest = p
        self._create_config_at(p_dest)
        self._config = dict(_DEFAULT_CONFIG)
        self._config_path = p_dest

    def _create_config_at(self, path: pathlib.Path) -> None:
        """Write the default config to *path* (YAML preferred, JSON fallback)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yaml as _yaml
            with open(path, 'w', encoding='utf-8') as f:
                _yaml.dump(_DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except ImportError:
            json_path = path.with_suffix('.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(_DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
            path = json_path
        logger = get_logger()
        logger.log('policy.config_created', {'path': str(path)}, severity='info')

    @staticmethod
    def _parse_file(path: pathlib.Path) -> Dict[str, Any]:
        """Parse a YAML (or JSON) config file and return the dict."""
        if not path.exists():
            raise FileNotFoundError(f'Policy config not found: {path}')
        suffix = path.suffix.lower()
        with open(path, 'r', encoding='utf-8') as f:
            if suffix in ('.yaml', '.yml'):
                try:
                    import yaml as _yaml
                    data = _yaml.safe_load(f)
                except ImportError:
                    f.seek(0)
                    data = json.load(f)
            elif suffix == '.json':
                data = json.load(f)
            else:
                try:
                    import yaml as _yaml
                    data = _yaml.safe_load(f)
                except (ImportError, Exception):
                    f.seek(0)
                    data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f'Policy config must be a top-level dict, got {type(data).__name__}')
        return data

    @staticmethod
    def _validate_config(config: Dict[str, Any]) -> None:
        """Validate the structure of a parsed policy config.

        Raises
        ------
        ValueError
            If required keys are missing or types are wrong.
        """
        if 'version' not in config:
            raise ValueError("Policy config missing required field: 'version'")
        if 'forbidden_actions' not in config:
            raise ValueError("Policy config missing required field: 'forbidden_actions'")
        if not isinstance(config['forbidden_actions'], list):
            raise ValueError("'forbidden_actions' must be a list")
        if 'limits' not in config:
            raise ValueError("Policy config missing required field: 'limits'")
        if not isinstance(config['limits'], dict):
            raise ValueError("'limits' must be a dict")
        threshold = config.get('default_risk_threshold', 'medium')
        if threshold not in _RISK_ORDER:
            raise ValueError(f"Invalid default_risk_threshold '{threshold}'. Must be one of {list(_RISK_ORDER.keys())}")

    @staticmethod
    def _extract_domain(ctx: Dict[str, Any]) -> str:
        """Extract a clean domain string from context dict.

        Tries ``domain``, then parses ``url``.
        """
        domain = ctx.get('domain', '')
        if domain:
            return domain.strip().lower()
        url = ctx.get('url', '')
        if url:
            url = url.strip().lower()
            for prefix in ('https://', 'http://', 'ftp://'):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            for sep in ('/', '?', '#', ':'):
                idx = url.find(sep)
                if idx >= 0:
                    url = url[:idx]
            return url
        return ''
_default_engine: Optional[PolicyEngine] = None
_default_engine_lock = threading.Lock()

def get_policy_engine(config_path: Optional[str]=None) -> PolicyEngine:
    """Return the application-wide PolicyEngine singleton.

    Parameters
    ----------
    config_path : str or None
        Optional explicit path to a policy config file.

    Returns
    -------
    PolicyEngine
        The singleton policy engine instance.
    """
    global _default_engine
    if config_path is not None:
        return PolicyEngine(config_path)
    with _default_engine_lock:
        if _default_engine is None:
            _default_engine = PolicyEngine()
        return _default_engine

def check_action(action_type: str, context: Optional[Dict[str, Any]]=None) -> PolicyResult:
    """Convenience: check an action against the default policy engine.

    Equivalent to ``get_policy_engine().check_action(action_type, context)``.

    Parameters
    ----------
    action_type : str
        The capability / action name to check.
    context : dict or None
        Optional contextual data (see ``PolicyEngine.check_action``).

    Returns
    -------
    PolicyResult
        The policy decision.
    """
    return get_policy_engine().check_action(action_type, context)