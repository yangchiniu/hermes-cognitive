"""
world_model.py — World Model system for Hermes Core.

Manages a snapshot of the environment state including CPU, memory, disk,
network, browser processes, and active tasks.  Reads live system state
and persists it to ``world_state.db`` via ``SchemaManager``.

Standard library only: os, subprocess, json, datetime, pathlib, socket, uuid,
stat, time, sqlite3 (via db_schema).
"""

from __future__ import annotations

import os
import re
import json
import uuid
import socket
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from .db_schema import SchemaManager, DATA_DIR
    from .event_logger import EventLogger, get_logger
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from db_schema import SchemaManager, DATA_DIR
    from event_logger import EventLogger, get_logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR = DATA_DIR
_PROXY_VAR_NAMES = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
_BROWSER_KEYWORDS = ("chrom", "chrome", "firefox")
_PING_TARGET = "8.8.8.8"
_PING_TIMEOUT_SEC = 3
_VERIFY_NETWORK_CACHE_SEC = 60
_VERIFY_CACHE_MAX_AGE_S = 3600  # 1 hour
_COOKIE_FILE_PATTERNS = ("cnki_cookies.json", "cookies.json", "cookies.txt")

# ---------------------------------------------------------------------------
# WorldModel
# ---------------------------------------------------------------------------


class WorldModel:
    """Snapshot and persist environment state.

    Usage
    -----
    >>> wm = WorldModel()
    >>> state = wm.get_world_state(refresh=True)
    >>> task_id = wm.record_task("code_gen", "Generate parser")
    >>> wm.update_task(task_id, status="completed", result_summary="Done")
    >>> wm.get_summary()
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._data_dir = Path(data_dir).expanduser().resolve() if data_dir else _DEFAULT_DATA_DIR
        self._mgr = SchemaManager(self._data_dir)
        self._mgr.initialize("world_state")
        self._logger: Optional[EventLogger] = None

    # -- public API ---------------------------------------------------------

    def snapshot(self, active_task_id: Optional[str] = None) -> Dict[str, Any]:
        """Read live system state, write to DB, and return the snapshot dict."""
        cpu = self._read_cpu_load()
        memory = self._read_memory()
        disk = self._read_disk()
        network = self._check_network()
        browsers = self._count_browsers()
        now = self._timestamp()
        snapshot_at = now

        # Fall back to DB if caller didn't supply an active task id.
        if active_task_id is None:
            active_task_id = self._last_active_task_id()

        # Persist to DB
        conn = self._mgr.get_connection("world_state")
        conn.execute(
            """INSERT INTO system_state
               (snapshot_at, cpu_pct, ram_total_mb, ram_used_mb, ram_avail_mb,
                disk_total_gb, disk_used_gb, load_1m, network_status,
                browser_count, active_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_at,
                cpu.get("load_1m", 0.0) * 100.0,  # rough cpu_pct from load
                memory["total_mb"],
                memory["used_mb"],
                memory["available_mb"],
                disk["total_gb"],
                disk["used_gb"],
                cpu.get("load_1m", 0.0),
                network["status"],
                browsers["count"],
                active_task_id,
            ),
        )
        conn.commit()

        # Build the full output dict
        result: Dict[str, Any] = {
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
            "network": network,
            "browsers": browsers,
            "active_task": active_task_id,
            "snapshot_at": snapshot_at,
        }
        return result

    def get_world_state(self, refresh: bool = False) -> Dict[str, Any]:
        """Return the current world state.

        If *refresh* is ``True`` a new live snapshot is taken first.
        Otherwise the latest entry from ``system_state`` is returned.

        If no entries exist in the DB, a minimal default state is returned.
        """
        if refresh:
            return self.snapshot()

        conn = self._mgr.get_connection("world_state")
        cursor = conn.execute(
            "SELECT * FROM system_state ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            return self._default_state()

        return self._row_to_state_dict(row)

    def record_task(
        self,
        task_type: str,
        description: str,
        status: str = "started",
    ) -> str:
        """Insert a new task into ``task_history`` and log via EventLogger.

        Returns the newly assigned task id (auto-increment id as a string).
        """
        now = self._timestamp()

        conn = self._mgr.get_connection("world_state")
        cursor = conn.execute(
            """INSERT INTO task_history
               (session_id, task_type, description, status, started_at)
               VALUES (?, ?, ?, ?, ?)""",
            (self._session_id(), task_type, description, status, now),
        )
        conn.commit()
        task_id = str(cursor.lastrowid)

        self._log_event("task.started", {
            "task_id": task_id,
            "task_type": task_type,
            "description": description,
            "status": status,
        })

        return task_id

    def update_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        resource_peak_ram_mb: Optional[float] = None,
    ) -> None:
        """Update an existing task in ``task_history`` by its auto-increment id.

        Sets ``completed_at`` and auto-calculates ``duration_seconds``
        when *status* is ``"completed"`` or ``"failed"``.
        """
        fields: Dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if result_summary is not None:
            fields["result_summary"] = result_summary
        if error_message is not None:
            fields["error_message"] = error_message
        if resource_peak_ram_mb is not None:
            fields["resource_peak_ram_mb"] = resource_peak_ram_mb

        is_terminal = status in ("completed", "failed")
        if is_terminal:
            fields["completed_at"] = self._timestamp()

        if not fields:
            return  # nothing to update

        conn = self._mgr.get_connection("world_state")

        # Calculate duration if completing/failing the task
        if is_terminal:
            cursor = conn.execute(
                "SELECT started_at FROM task_history WHERE id = ?",
                (int(task_id),),
            )
            row = cursor.fetchone()
            if row and row["started_at"]:
                try:
                    started = datetime.fromisoformat(row["started_at"])
                    completed = datetime.fromisoformat(fields["completed_at"])
                    duration = (completed - started).total_seconds()
                    fields["duration_seconds"] = round(duration, 2)
                except (ValueError, TypeError):
                    pass

        set_clauses = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(int(task_id))

        conn.execute(
            f"UPDATE task_history SET {set_clauses} WHERE id = ?",
            values,
        )
        conn.commit()

        self._log_event("task.updated", {
            "task_id": task_id,
            "status": status,
            "result_summary": result_summary,
        })

    def record_website_result(
        self,
        domain: str,
        success: bool,
        risk_level: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Update the ``website_risk`` table for *domain*.

        Increments ``success_count`` or ``fail_count`` accordingly.
        """
        now = self._timestamp()
        conn = self._mgr.get_connection("world_state")

        # Check if domain already exists
        cursor = conn.execute(
            "SELECT * FROM website_risk WHERE domain = ?", (domain,)
        )
        existing = cursor.fetchone()

        if existing is None:
            # Insert new row
            conn.execute(
                """INSERT INTO website_risk
                   (domain, risk_level, last_tested_at, success_count,
                    fail_count, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    domain,
                    risk_level or 0,
                    now,
                    1 if success else 0,
                    0 if success else 1,
                    notes or "",
                ),
            )
        else:
            # Update existing row
            if success:
                conn.execute(
                    "UPDATE website_risk SET success_count = success_count + 1, "
                    "last_tested_at = ? WHERE domain = ?",
                    (now, domain),
                )
            else:
                conn.execute(
                    "UPDATE website_risk SET fail_count = fail_count + 1, "
                    "last_tested_at = ? WHERE domain = ?",
                    (now, domain),
                )
            if risk_level is not None:
                conn.execute(
                    "UPDATE website_risk SET risk_level = ? WHERE domain = ?",
                    (risk_level, domain),
                )
            if notes is not None:
                conn.execute(
                    "UPDATE website_risk SET notes = ? WHERE domain = ?",
                    (notes, domain),
                )

        conn.commit()

    def get_website_risk(self, domain: str) -> Optional[Dict[str, Any]]:
        """Return risk info for *domain*, or ``None`` if not found."""
        conn = self._mgr.get_connection("world_state")
        cursor = conn.execute(
            "SELECT * FROM website_risk WHERE domain = ?", (domain,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_summary(self) -> Dict[str, Any]:
        """Return a high-level summary of the current world state."""
        state = self.get_world_state(refresh=False)
        conn = self._mgr.get_connection("world_state")

        # Count active (incomplete) tasks
        cursor = conn.execute(
            "SELECT COUNT(*) AS cnt FROM task_history "
            "WHERE status NOT IN ('completed', 'failed') OR status IS NULL"
        )
        active_tasks = cursor.fetchone()["cnt"]

        # Total website domains tracked
        cursor = conn.execute("SELECT COUNT(*) AS cnt FROM website_risk")
        known_domains = cursor.fetchone()["cnt"]

        # Browser risk domains
        cursor = conn.execute(
            "SELECT COUNT(*) AS cnt FROM website_risk WHERE risk_level >= 3"
        )
        risky_domains = cursor.fetchone()["cnt"]

        summary = {
            "cpu": state.get("cpu", {}),
            "memory": state.get("memory", {}),
            "disk": state.get("disk", {}),
            "network": state.get("network", {}),
            "browsers": state.get("browsers", {}),
            "active_tasks": active_tasks,
            "known_domains": known_domains,
            "risky_domains": risky_domains,
            "snapshot_at": state.get("snapshot_at"),
        }
        return summary

    def add_system_event(self, event_type: str, data: Dict[str, Any]) -> str:
        """Convenience wrapper for ``EventLogger.log()``.

        Returns the event id (UUID string).
        """
        return self._log_event(event_type, data)

    # -- helpers ------------------------------------------------------------

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> str:
        """Write a structured event to the NDJSON log."""
        logger = self._get_logger()
        return logger.log(event_type, data)

    def _get_logger(self) -> EventLogger:
        """Return (and lazily create) the EventLogger singleton."""
        if self._logger is None:
            self._logger = get_logger(self._data_dir)
        return self._logger

    @staticmethod
    def _session_id() -> str:
        """Return the current session id from env or a fresh UUID."""
        sid = os.environ.get("HERMES_SESSION_ID")
        if sid is None:
            sid = str(uuid.uuid4())
            os.environ["HERMES_SESSION_ID"] = sid
        return sid

    @staticmethod
    def _timestamp() -> str:
        """Return ISO-8601 UTC timestamp string."""
        return datetime.now(timezone.utc).isoformat()

    # -- system state readers -----------------------------------------------

    @staticmethod
    def _read_cpu_load() -> Dict[str, float]:
        """Read CPU load averages from ``/proc/loadavg``."""
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().strip().split()
            return {
                "load_1m": float(parts[0]),
                "load_5m": float(parts[1]),
                "load_15m": float(parts[2]),
            }
        except (OSError, IndexError, ValueError):
            return {"load_1m": 0.0, "load_5m": 0.0, "load_15m": 0.0}

    @staticmethod
    def _read_memory() -> Dict[str, float]:
        """Read memory stats from ``/proc/meminfo``.

        Returns dict with keys: total_mb, available_mb, free_mb, used_mb, percent.
        """
        meminfo: Dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip()
                    value_str = parts[1].strip().split()[0]  # value in kB
                    try:
                        meminfo[key] = int(value_str)
                    except (ValueError, IndexError):
                        pass
        except OSError:
            pass

        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        free_kb = meminfo.get("MemFree", 0)

        total_mb = total_kb / 1024.0
        available_mb = available_kb / 1024.0
        free_mb = free_kb / 1024.0
        used_mb = total_mb - available_mb
        percent = (used_mb / total_mb * 100.0) if total_mb > 0 else 0.0

        return {
            "total_mb": round(total_mb, 1),
            "available_mb": round(available_mb, 1),
            "free_mb": round(free_mb, 1),
            "used_mb": round(used_mb, 1),
            "percent": round(percent, 1),
        }

    @staticmethod
    def _read_disk() -> Dict[str, float]:
        """Read disk usage for ``/`` with ``os.statvfs``."""
        try:
            st = os.statvfs("/")
            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bfree
            used = total - free
            total_gb = total / (1024**3)
            used_gb = used / (1024**3)
            free_gb = free / (1024**3)
            percent = (used / total * 100.0) if total > 0 else 0.0
        except OSError:
            total_gb = used_gb = free_gb = percent = 0.0

        return {
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "free_gb": round(free_gb, 1),
            "percent": round(percent, 1),
        }

    @staticmethod
    def _check_network() -> Dict[str, Any]:
        """Check network connectivity and proxy settings.

        Returns ``{"status": "online"|"offline", "proxy": str|None}``.
        """
        # Check proxy first
        proxy = None
        for var in _PROXY_VAR_NAMES:
            val = os.environ.get(var)
            if val:
                proxy = val
                break

        # Ping test
        online = False
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(_PING_TIMEOUT_SEC), _PING_TARGET],
                capture_output=True,
                timeout=_PING_TIMEOUT_SEC + 1,
            )
            online = result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass

        return {
            "status": "online" if online else "offline",
            "proxy": proxy,
        }

    @staticmethod
    def _count_browsers() -> Dict[str, int]:
        """Count running browser processes via ``ps aux``."""
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            count = 0
            for line in result.stdout.splitlines():
                lower = line.lower()
                for kw in _BROWSER_KEYWORDS:
                    if kw in lower:
                        count += 1
                        break
            return {"count": count}
        except (subprocess.SubprocessError, OSError):
            return {"count": 0}

    def _last_active_task_id(self) -> Optional[str]:
        """Return the most recent uncompleted task id from DB."""
        conn = self._mgr.get_connection("world_state")
        cursor = conn.execute(
            "SELECT id, description FROM task_history "
            "WHERE status NOT IN ('completed', 'failed') OR status IS NULL "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return str(row["id"])

    # -- verification methods ------------------------------------------------

    @staticmethod
    def verify_browser_sessions() -> Dict[str, Any]:
        """Check if any browser sessions tracked in system_state are still alive.

        Uses ``ps aux`` to find browser processes and checks ``/proc/[pid]/status``
        for zombie state.

        Returns
        -------
        dict
            {alive: int, dead: int, sessions: [{pid, alive, name}]}
        """
        sessions: list[Dict[str, Any]] = []
        alive = 0
        dead = 0
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                lower = line.lower()
                is_browser = False
                for kw in _BROWSER_KEYWORDS:
                    if kw in lower:
                        is_browser = True
                        break
                if not is_browser:
                    continue
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[1])
                except (ValueError, IndexError):
                    continue
                proc_name = parts[-1] if parts else "?"
                # Check /proc/[pid] to see if process is still alive
                proc_alive = os.path.isdir(f"/proc/{pid}")
                if proc_alive:
                    # Check for zombie state
                    try:
                        with open(f"/proc/{pid}/status") as f:
                            status_content = f.read()
                        if "State:\\tZ" in status_content:
                            dead += 1
                            proc_alive = False
                        else:
                            alive += 1
                    except OSError:
                        alive += 1
                else:
                    dead += 1
                sessions.append({
                    "pid": pid,
                    "alive": proc_alive,
                    "name": proc_name,
                })
        except (subprocess.SubprocessError, OSError):
            pass
        return {"alive": alive, "dead": dead, "sessions": sessions}

    @staticmethod
    def get_cookie_freshness() -> Dict[str, Any]:
        """Check cookie files in ``~/.hermes/`` for freshness.

        Returns
        -------
        dict
            {files: [{path, exists, age_days, size}], notes: [str]}
        """
        hermes_dir = Path.home() / ".hermes"
        notes: list[str] = []
        files_info: list[Dict[str, Any]] = []
        now = datetime.now(timezone.utc)

        for pattern in _COOKIE_FILE_PATTERNS:
            # Check in ~/.hermes/
            for p in [hermes_dir / pattern] + list(hermes_dir.glob(f"*{pattern.rsplit('.', 1)[0]}*.json")):
                if not p.exists():
                    continue
                # Avoid duplicates
                if any(f["path"] == str(p) for f in files_info):
                    continue
                try:
                    st = p.stat()
                    age = now - datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                    age_days = round(age.total_seconds() / 86400.0, 1)
                    files_info.append({
                        "path": str(p),
                        "exists": True,
                        "age_days": age_days,
                        "size": st.st_size,
                    })
                    if age_days > 7:
                        notes.append(f"Cookie file {p.name} is {age_days} days old")
                    if st.st_size == 0:
                        notes.append(f"Cookie file {p.name} is empty")
                except OSError:
                    files_info.append({
                        "path": str(p),
                        "exists": True,
                        "age_days": -1,
                        "size": 0,
                    })
                    notes.append(f"Could not stat cookie file {p.name}")

        return {"files": files_info, "notes": notes}

    def verify_databases(self) -> Dict[str, Any]:
        """Run ``PRAGMA integrity_check`` on all known databases.

        Returns
        -------
        dict
            {dbs: [{name: str, ok: bool, error: str}], all_ok: bool}
        """
        try:
            from .db_schema import DATABASE_SCHEMAS as _schemas
        except ImportError:
            _schemas = {"world_state": {}, "experience": {}, "reflection": {}}
        dbs: list[Dict[str, Any]] = []
        all_ok = True
        for db_name in _schemas:
            try:
                conn = self._mgr.get_connection(db_name)
                cursor = conn.execute("PRAGMA integrity_check;")
                result = cursor.fetchone()
                ok = result is not None and result[0] == "ok"
                error = "" if ok else (str(result[0]) if result else "unknown")
                dbs.append({"name": db_name, "ok": ok, "error": error})
                if not ok:
                    all_ok = False
            except Exception as exc:
                dbs.append({"name": db_name, "ok": False, "error": str(exc)})
                all_ok = False
        return {"dbs": dbs, "all_ok": all_ok}

    def verify_network(self, force: bool = False) -> Dict[str, Any]:
        """Check network connectivity, proxy, DNS, and latency.

        Results are cached for ``_VERIFY_NETWORK_CACHE_SEC`` seconds
        (default 60) unless *force* is ``True``.

        Returns
        -------
        dict
            {online: bool, proxy_working: bool, dns_ok: bool, latency_ms: float}
        """
        now = time.time()
        if not force and hasattr(self, "_network_cache"):
            cache_time, cache_result = self._network_cache
            if now - cache_time < _VERIFY_NETWORK_CACHE_SEC:
                return cache_result

        online = False
        proxy_working = False
        dns_ok = False
        latency_ms = -1.0

        # 1. DNS resolution
        try:
            socket.getaddrinfo("google.com", 80, socket.AF_INET)
            dns_ok = True
        except OSError:
            pass

        # 2. Ping test
        try:
            start = time.time()
            result = subprocess.run(
                ["ping", "-c", "1", "-W", str(_PING_TIMEOUT_SEC), _PING_TARGET],
                capture_output=True,
                timeout=_PING_TIMEOUT_SEC + 1,
            )
            elapsed = (time.time() - start) * 1000.0  # ms
            latency_ms = round(elapsed, 1)
            online = result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass

        # 3. Proxy check
        proxy_url = None
        for var in _PROXY_VAR_NAMES:
            val = os.environ.get(var)
            if val:
                proxy_url = val
                break
        if proxy_url:
            # Try a basic TCP connection to the proxy host
            try:
                proxy_str = proxy_url.replace("http://", "").replace("https://", "").replace("socks5://", "").replace("socks4://", "")
                if ":" in proxy_str:
                    host, port_str = proxy_str.split(":", 1)
                    port = int(port_str.split("/")[0].strip())
                else:
                    host = proxy_str.split("/")[0].strip()
                    port = 80
                sock = socket.create_connection((host, port), timeout=3)
                sock.close()
                proxy_working = True
            except (OSError, ValueError, IndexError):
                proxy_working = False
        else:
            proxy_working = True  # no proxy configured, nothing to be wrong

        result = {
            "online": online,
            "proxy_working": proxy_working,
            "dns_ok": dns_ok,
            "latency_ms": latency_ms,
        }
        self._network_cache = (now, result)
        return result

    @staticmethod
    def verify_cache() -> Dict[str, Any]:
        """Check the scraping cache directory for stale files.

        Cache directory is ``~/.hermes/core/data/`` (``DATA_DIR``).
        Files older than 1 hour are considered stale.

        Returns
        -------
        dict
            {total_cache_files: int, stale: int, size_bytes: int}
        """
        cache_dir = _DEFAULT_DATA_DIR
        total = 0
        stale = 0
        size_bytes = 0
        now = time.time()
        try:
            for entry in cache_dir.iterdir():
                if not entry.is_file():
                    continue
                total += 1
                try:
                    st = entry.stat()
                    size_bytes += st.st_size
                    age = now - st.st_mtime
                    if age > _VERIFY_CACHE_MAX_AGE_S:
                        stale += 1
                except OSError:
                    pass
        except OSError:
            pass
        return {
            "total_cache_files": total,
            "stale": stale,
            "size_bytes": size_bytes,
        }

    def verify_all(self) -> Dict[str, Any]:
        """Run all verification checks and return a combined report.

        Returns
        -------
        dict
            {
                browser_sessions: {...},
                cookie_freshness: {...},
                databases: {...},
                network: {...},
                cache: {...},
                health_score: int,
                timestamp: str,
            }
        """
        results: Dict[str, Any] = {
            "browser_sessions": self.verify_browser_sessions(),
            "cookie_freshness": self.get_cookie_freshness(),
            "databases": self.verify_databases(),
            "network": self.verify_network(),
            "cache": self.verify_cache(),
        }

        # Compute a health score (0-100)
        score = 100
        browser = results["browser_sessions"]
        if browser["dead"] > 0:
            score -= min(browser["dead"] * 10, 30)
        cookie = results["cookie_freshness"]
        if cookie["notes"]:
            score -= min(len(cookie["notes"]) * 10, 20)
        db = results["databases"]
        if not db["all_ok"]:
            score -= 30
        net = results["network"]
        if not net["online"]:
            score -= 20
        if not net["dns_ok"]:
            score -= 10
        if not net["proxy_working"]:
            score -= 10
        cache = results["cache"]
        if cache["stale"] > 0:
            score -= min(cache["stale"] * 5, 20)

        results["health_score"] = max(score, 0)
        results["timestamp"] = self._timestamp()
        return results

    def is_world_stale(self, max_age_s: float = 60.0) -> bool:
        """Check if the last snapshot is older than *max_age_s* seconds.

        Parameters
        ----------
        max_age_s : float
            Maximum allowed age of the last snapshot in seconds (default 60.0).

        Returns
        -------
        bool
            ``True`` if the most recent snapshot is older than *max_age_s*
            or if no snapshot exists.
        """
        conn = self._mgr.get_connection("world_state")
        cursor = conn.execute(
            "SELECT snapshot_at FROM system_state ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row is None or row["snapshot_at"] is None:
            return True
        try:
            snapshot_time = datetime.fromisoformat(row["snapshot_at"])
            now = datetime.now(timezone.utc)
            age = (now - snapshot_time).total_seconds()
            return age > max_age_s
        except (ValueError, TypeError):
            return True

    @staticmethod
    def heartbeat() -> str:
        """Simple health check: returns ``"alive"`` + ISO-8601 timestamp.

        Used by Watchdog to verify the WorldModel is responsive.

        Returns
        -------
        str
            e.g. ``"alive@2026-05-13T04:20:00.000000+00:00"``
        """
        return f"alive@{datetime.now(timezone.utc).isoformat()}"

    # -- state construction -------------------------------------------------

    @staticmethod
    def _default_state() -> Dict[str, Any]:
        """Return a minimal default state dict when DB is empty."""
        return {
            "cpu": {"load_1m": 0.0, "load_5m": 0.0, "load_15m": 0.0},
            "memory": {"total_mb": 0.0, "available_mb": 0.0, "free_mb": 0.0, "used_mb": 0.0, "percent": 0.0},
            "disk": {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0},
            "network": {"status": "unknown", "proxy": None},
            "browsers": {"count": 0},
            "active_task": None,
            "snapshot_at": None,
        }

    @staticmethod
    def _row_to_state_dict(row) -> Dict[str, Any]:
        """Convert a ``system_state`` DB row to the standard output dict."""
        return {
            "cpu": {
                "load_1m": row["load_1m"] if row["load_1m"] is not None else 0.0,
                "load_5m": 0.0,  # not stored in DB — only load_1m is persisted
                "load_15m": 0.0,
            },
            "memory": {
                "total_mb": row["ram_total_mb"] or 0.0,
                "available_mb": row["ram_avail_mb"] or 0.0,
                "free_mb": round(
                    (row["ram_total_mb"] or 0.0) - (row["ram_avail_mb"] or 0.0), 1
                ),
                "used_mb": row["ram_used_mb"] or 0.0,
                "percent": (
                    round(
                        (row["ram_used_mb"] / row["ram_total_mb"]) * 100.0, 1
                    )
                    if row["ram_total_mb"] and row["ram_total_mb"] > 0
                    else 0.0
                ),
            },
            "disk": {
                "total_gb": row["disk_total_gb"] or 0.0,
                "used_gb": row["disk_used_gb"] or 0.0,
                "free_gb": round(
                    (row["disk_total_gb"] or 0.0) - (row["disk_used_gb"] or 0.0), 1
                ),
                "percent": (
                    round(
                        (row["disk_used_gb"] / row["disk_total_gb"]) * 100.0, 1
                    )
                    if row["disk_total_gb"] and row["disk_total_gb"] > 0
                    else 0.0
                ),
            },
            "network": {
                "status": row["network_status"] or "unknown",
                "proxy": None,
            },
            "browsers": {
                "count": row["browser_count"] or 0,
            },
            "active_task": row["active_task_id"],
            "snapshot_at": row["snapshot_at"],
        }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_world_model: Optional[WorldModel] = None


def get_world_model(data_dir: Optional[Path] = None) -> WorldModel:
    """Return the module-level ``WorldModel`` singleton."""
    global _default_world_model
    if _default_world_model is None:
        _default_world_model = WorldModel(data_dir)
    return _default_world_model


def verify_world(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Shorthand for ``WorldModel(data_dir).verify_all()``.

    Parameters
    ----------
    data_dir : Path or None
        Optional data directory override.

    Returns
    -------
    dict
        Combined verification report from ``verify_all()``.
    """
    wm = get_world_model(data_dir)
    return wm.verify_all()
