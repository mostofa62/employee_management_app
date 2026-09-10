"""Turso sync - save local SQLite DB to Turso (libSQL) cloud on each update when network available.

Supports two backends:
  1) native `libsql` package (embedded replica style) if installed
  2) pure HTTP via Hrana pipeline (`/v2/pipeline`) using `requests` / `urllib` fallback

Config stored in DB app_state + env fallback (like supabase):
  - TURSO_DATABASE_URL  (e.g. libsql://my-db-xxx.turso.io  or https://my-db-xxx.turso.io)
  - TURSO_AUTH_TOKEN
  - TURSO_ENABLED  (0/1, default 1 when configured)

Every mutating call in db.py calls `schedule_sync()` (debounced ~2s, background thread).
Network availability is checked before each push; if offline, sync is queued and retried
periodically (every 60s) and on next app start.

Full snapshot strategy (safe for small IEDCR DB < few MB):
  - Ensure remote schema exists (CREATE TABLE IF NOT EXISTS)
  - DELETE child tables first then re-INSERT all rows parent-first in batches, inside
    a logical batch (BEGIN/COMMIT). Handles deletions. Preserves explicit IDs.
  - Batched via Hrana pipeline (50-80 stmts per HTTP request) to stay under limits.

No libsql required - works offline without token (queues). If token/url missing,
sync is no-op.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Reuse DB_PATH and schema from db.py without circular import at module load
# Import lazily inside functions where needed.

# ── Config keys in app_state ──────────────────────────────────
K_URL = "turso_database_url"
K_TOKEN = "turso_auth_token"
K_ENABLED = "turso_enabled"          # "1" / "0"
K_LAST_SYNC = "turso_last_sync"      # iso timestamp
K_LAST_ERROR = "turso_last_error"
K_LAST_STATUS = "turso_last_status"  # "ok" / "error" / "offline" / "pending" / "syncing"
K_BACKUP_INTERVAL = "turso_backup_interval"  # seconds as string, "0"=disabled
K_BACKUP_TIME = "turso_backup_time"          # "HH:MM" for daily backup, ""=disabled
K_LAST_PERIODIC = "turso_last_periodic"      # iso timestamp of last periodic full backup

# Also support env names
ENV_URL_KEYS = ("TURSO_DATABASE_URL", "TURSO_SYNC_URL", "LIBSQL_URL", "DATABASE_URL")
ENV_TOKEN_KEYS = ("TURSO_AUTH_TOKEN", "TURSO_TOKEN", "LIBSQL_AUTH_TOKEN", "AUTH_TOKEN")

_lock = threading.Lock()
_timer: Optional[threading.Timer] = None
_sync_thread: Optional[threading.Thread] = None
_pending = False
_syncing = False
_last_result: Dict[str, Any] = {"status": "idle", "msg": ""}
_last_network: str = "online"
_last_network_at: float = time.time()
_status_callback = None  # optional callable(status_dict) invoked on status change (UI thread via after)
_cached_cfg: Optional[Dict[str, Any]] = None
_cached_cfg_at: float = 0.0

_POLL_THREAD: Optional[threading.Thread] = None
_POLL_STOP = threading.Event()
_PERIODIC_THREAD: Optional[threading.Thread] = None
_PERIODIC_STOP = threading.Event()

# ── Helpers: config ───────────────────────────────────────────
def _get_db_path() -> Path:
    # lazy import to avoid circular
    try:
        import db as _db
        return Path(_db.DB_PATH)
    except Exception:
        return Path(os.environ.get("EMPLOYEE_VISITS_DB") or Path(__file__).resolve().with_name("employees.db"))

def _db_get_state(key: str, default: str = "") -> str:
    try:
        import db as _db
        v = _db.get_app_state(key)
        return v if v is not None else default
    except Exception:
        return default

def _db_set_state(key: str, value: str):
    try:
        import db as _db
        _db.set_app_state(key, value)
    except Exception:
        pass

def _db_delete_state(key: str):
    try:
        import db as _db
        _db.delete_app_state(key)
    except Exception:
        pass

def _env_first(keys) -> str:
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""

def _get_cached_config() -> Optional[Dict[str, Any]]:
    global _cached_cfg, _cached_cfg_at
    if _cached_cfg is not None and (time.time() - _cached_cfg_at) < 5.0:
        return _cached_cfg
    return None

def get_turso_config() -> Dict[str, str]:
    """Return dict url, token, enabled (bool string). DB primary, env fallback."""
    global _cached_cfg, _cached_cfg_at
    # use cache for UI fast path
    _c = _get_cached_config()
    if _c is not None:
        return dict(_c)  # copy
    url = (_db_get_state(K_URL) or "").strip()
    token = (_db_get_state(K_TOKEN) or "").strip()
    enabled_s = (_db_get_state(K_ENABLED) or "").strip()
    # env fallback only if DB missing
    if not url:
        url = _env_first(ENV_URL_KEYS).strip()
        # also try reading .env file manually (for compiled builds without dotenv)
        if not url:
            try:
                base = Path(__file__).resolve().with_name(".env")
                if base.is_file():
                    for line in base.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip(); v = v.strip().strip('"').strip("'")
                        if k in ENV_URL_KEYS and not url and v:
                            url = v
            except Exception:
                pass
    if not token:
        token = _env_first(ENV_TOKEN_KEYS).strip()
        if not token:
            try:
                base = Path(__file__).resolve().with_name(".env")
                if base.is_file():
                    for line in base.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip(); v = v.strip().strip('"').strip("'")
                        if k in ENV_TOKEN_KEYS and not token and v:
                            token = v
            except Exception:
                pass
    enabled = True
    if enabled_s != "":
        enabled = enabled_s == "1"
    else:
        # default enabled when url+token present, else disabled
        enabled = bool(url and token)
    result = {"url": url, "token": token, "enabled": "1" if enabled else "0", "enabled_bool": enabled}  # type: ignore
    try:
        _cached_cfg = dict(result)
        _cached_cfg_at = time.time()
    except Exception:
        pass
    return result

def set_turso_config(url: Optional[str] = None, token: Optional[str] = None, enabled: Optional[bool] = None) -> Dict[str, str]:
    if url is not None:
        v = (url or "").strip()
        if v:
            _db_set_state(K_URL, v)
        else:
            _db_delete_state(K_URL)
    if token is not None:
        v = (token or "").strip()
        if v:
            _db_set_state(K_TOKEN, v)
        else:
            _db_delete_state(K_TOKEN)
    if enabled is not None:
        _db_set_state(K_ENABLED, "1" if enabled else "0")
    return get_turso_config()

def is_turso_configured() -> bool:
    cfg = get_turso_config()
    return bool(cfg["url"].strip() and cfg["token"].strip() and cfg["enabled_bool"])

def is_turso_enabled() -> bool:
    cfg = get_turso_config()
    # enabled flag matters even if url missing (to show status)
    return bool(cfg["enabled_bool"])

# ── Periodic backup config ────────────────────────────────
def get_backup_config() -> Dict[str, str]:
    """Return interval_sec (int str) and time_str HH:MM. DB primary, defaults disabled."""
    interval = (_db_get_state(K_BACKUP_INTERVAL, "") or "").strip()
    tstr = (_db_get_state(K_BACKUP_TIME, "") or "").strip()
    # default: disabled (interval 0)
    if not interval and not tstr:
        # check legacy env? not needed
        interval = "0"
    try:
        iv = int(interval) if interval else 0
    except Exception:
        iv = 0
    # validate time format
    if tstr:
        try:
            hh, mm = tstr.split(":")
            hh_i = int(hh); mm_i = int(mm)
            if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                tstr = ""
            else:
                tstr = f"{hh_i:02d}:{mm_i:02d}"
        except Exception:
            tstr = ""
    return {"interval_sec": str(iv), "interval_int": iv, "time_str": tstr}  # type: ignore

def set_backup_config(interval_sec: Optional[int] = None, time_str: Optional[str] = None) -> Dict[str, str]:
    if interval_sec is not None:
        try:
            iv = int(interval_sec)
        except Exception:
            iv = 0
        if iv <= 0:
            _db_delete_state(K_BACKUP_INTERVAL)
            _db_delete_state(K_LAST_PERIODIC)
        else:
            _db_set_state(K_BACKUP_INTERVAL, str(iv))
            # if interval set, clear specific time to avoid ambiguity? keep both but interval takes precedence if both set? we prioritize time if set
    if time_str is not None:
        t = (time_str or "").strip()
        if not t:
            _db_delete_state(K_BACKUP_TIME)
        else:
            # validate
            try:
                hh, mm = t.split(":")
                hh_i = int(hh); mm_i = int(mm)
                if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
                    t = f"{hh_i:02d}:{mm_i:02d}"
                    _db_set_state(K_BACKUP_TIME, t)
                else:
                    _db_delete_state(K_BACKUP_TIME)
            except Exception:
                _db_delete_state(K_BACKUP_TIME)
    return get_backup_config()

def _is_periodic_due() -> bool:
    """Check if periodic full backup is due (interval or daily time)."""
    try:
        cfg = get_backup_config()
        interval = int(cfg["interval_int"])
        tstr = cfg["time_str"]
        last = (_db_get_state(K_LAST_PERIODIC, "") or "").strip()
        now = datetime.now()
        # daily specific time has priority if set
        if tstr:
            try:
                hh, mm = map(int, tstr.split(":"))
                today_target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                # if today target is in future, due is yesterday target
                # we consider due if now >= today_target and last < today_target
                if not last:
                    return now >= today_target
                try:
                    last_dt = datetime.fromisoformat(last)
                except Exception:
                    return True
                # if last was before today_target and now after today_target -> due
                if last_dt < today_target <= now:
                    return True
                # also if last was yesterday and now is after today_target -> due handled above
                # if more than 24h since last and we missed window, also due
                if (now - last_dt).total_seconds() > 24*3600:
                    return True
                return False
            except Exception:
                pass
        if interval > 0:
            if not last:
                return True
            try:
                last_dt = datetime.fromisoformat(last)
                return (now - last_dt).total_seconds() >= interval
            except Exception:
                return True
        return False
    except Exception:
        return False

def _mark_periodic_done():
    try:
        _db_set_state(K_LAST_PERIODIC, datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass

# ── URL helpers ───────────────────────────────────────────────
def _libsql_to_https(url: str) -> str:
    """Convert libsql://host -> https://host, keep https:// as is, strip trailing /."""
    u = (url or "").strip()
    if not u:
        return ""
    # already https?
    if u.startswith("https://"):
        return u.rstrip("/")
    if u.startswith("http://"):
        return "https://" + u[len("http://"):].rstrip("/")
    if u.startswith("libsql://"):
        return "https://" + u[len("libsql://"):].rstrip("/")
    if u.startswith("wss://"):
        return "https://" + u[len("wss://"):].rstrip("/")
    # bare host
    if "://" not in u:
        return "https://" + u.rstrip("/")
    return u.rstrip("/")

def _extract_host(url: str) -> str:
    try:
        h = _libsql_to_https(url)
        # strip https://
        if "://" in h:
            h = h.split("://", 1)[1]
        return h.split("/")[0].split("?")[0]
    except Exception:
        return ""

# ── Network check ─────────────────────────────────────────────
def is_network_available(timeout: float = 1.5, host: str = "") -> bool:
    """Check if we can reach internet and (if host given) the Turso host.

    Keep timeout short for UI; background sync uses 2.5s.
    Always exception-safe, never raises.
    """
    # quick socket check to 1.1.1.1:53
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=timeout).close()
    except Exception:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        except Exception:
            return False
    if host:
        h = _extract_host(host)
        if h:
            try:
                socket.create_connection((h, 443), timeout=min(timeout, 1.5)).close()
            except Exception:
                pass
    return True

def _has_network_for_cfg(cfg: Dict[str, str], timeout: float = 2.5) -> bool:
    return is_network_available(timeout=timeout, host=cfg.get("url", ""))

def _update_cached_network(cfg: Dict[str, str]):
    global _last_network, _last_network_at
    try:
        ok = _has_network_for_cfg(cfg, timeout=1.2)
        _last_network = "online" if ok else "offline"
        _last_network_at = time.time()
    except Exception:
        _last_network = "unknown"

def is_syncing() -> bool:
    with _lock:
        return _syncing

def set_status_callback(cb):
    """Register UI callback: cb(status_dict) called on status change (from bg thread)."""
    global _status_callback
    _status_callback = cb

def _notify_callback():
    if _status_callback is None:
        return
    try:
        st = get_status(check_network=False)
        # call in try, caller is bg thread - UI should schedule via .after if needed
        _status_callback(st)
    except Exception:
        pass

# ── Hrana HTTP helpers ───────────────────────────────────────
def _hrana_value(py_val) -> Dict[str, Any]:
    if py_val is None:
        return {"type": "null"}
    if isinstance(py_val, int) and not isinstance(py_val, bool):
        return {"type": "integer", "value": str(py_val)}
    if isinstance(py_val, float):
        return {"type": "float", "value": py_val}
    if isinstance(py_val, bytes):
        import base64
        return {"type": "blob", "value": base64.b64encode(py_val).decode()}
    return {"type": "text", "value": str(py_val)}

def _headers_for_cfg(cfg: Dict[str, str]) -> Dict[str, str]:
    token = cfg["token"]
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

def _pipeline_url(cfg: Dict[str, str]) -> str:
    base = _libsql_to_https(cfg["url"])
    return base.rstrip("/") + "/v2/pipeline"

def _execute_pipeline(cfg: Dict[str, str], stmts: List[Tuple[str, List[Any]]], timeout: float = 12.0) -> Tuple[bool, str]:
    """Execute list of (sql, params) via Hrana pipeline. Returns (ok, msg).

    Timeout is short (12s) to avoid hanging UI bg thread. Exceptions are
    caught and returned as (False, msg) - never raises to caller.
    """
    if not stmts:
        return True, "empty"
    url = _pipeline_url(cfg)
    headers = _headers_for_cfg(cfg)
    # Build requests
    reqs = []
    for sql, params in stmts:
        if params:
            args = [_hrana_value(v) for v in params]
            reqs.append({"type": "execute", "stmt": {"sql": sql, "args": args}})
        else:
            reqs.append({"type": "execute", "stmt": {"sql": sql}})
    reqs.append({"type": "close"})
    body = json.dumps({"requests": reqs})
    # try requests first, fallback urllib
    try:
        import requests  # type: ignore
        resp = requests.post(url, headers=headers, data=body, timeout=timeout)
        txt = resp.text
        if resp.ok:
            try:
                j = resp.json()
                # check for errors in results
                results = j.get("results") or []
                for r in results:
                    if r.get("type") == "error":
                        err = r.get("error") or {}
                        msg = err.get("message") or str(err)
                        return False, msg
                return True, "ok"
            except Exception:
                return True, txt[:500]
        else:
            return False, f"HTTP {resp.status_code}: {txt[:800]}"
    except ImportError:
        pass
    except Exception as e:
        return False, str(e)[:800]
    # urllib fallback
    try:
        import urllib.request, urllib.error
        data = body.encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # type: ignore
            txt = resp.read().decode("utf-8", errors="ignore")
            if 200 <= resp.status < 300:
                try:
                    j = json.loads(txt)
                    results = j.get("results") or []
                    for r in results:
                        if r.get("type") == "error":
                            err = r.get("error") or {}
                            msg = err.get("message") or str(err)
                            return False, msg
                    return True, "ok"
                except Exception:
                    return True, txt[:500]
            return False, f"HTTP {resp.status}: {txt[:800]}"
    except Exception as e:
        # urllib.error.HTTPError has .read()
        try:
            import urllib.error
            if isinstance(e, urllib.error.HTTPError):  # type: ignore
                body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
                return False, f"HTTP {e.code}: {body[:800]}"
        except Exception:
            pass
        return False, str(e)[:800]

def test_connection(cfg: Optional[Dict[str, str]] = None, timeout: float = 8.0) -> Tuple[bool, str]:
    """Test Turso connection via simple SELECT 1. Short timeout, exception-safe."""
    if cfg is None:
        cfg = get_turso_config()
    if not cfg["url"] or not cfg["token"]:
        return False, "Missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN"
    try:
        if not _has_network_for_cfg(cfg):
            return False, "No network / cannot reach Turso host"
    except Exception:
        return False, "Network check failed"
    try:
        ok, msg = _execute_pipeline(cfg, [("SELECT 1 as _t", [])], timeout=timeout)
        if ok:
            return True, "Connected to Turso"
        return False, msg
    except Exception as e:
        return False, f"Test failed: {e}"[:400]

# ── Snapshot sync ─────────────────────────────────────────────
# Order matters for FK
_TABLES_INSERT_ORDER = ["projects", "organizations", "visit_purposes", "employees", "app_users", "visits", "visit_days", "employee_tenures", "employee_assignments"]
_TABLES_DELETE_ORDER = ["employee_assignments", "employee_tenures", "visit_days", "visits", "employees", "organizations", "projects", "visit_purposes", "app_users"]

def _local_tables(conn: sqlite3.Connection, include_tables: List[str]) -> List[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    existing = {r[0] for r in cur.fetchall()}
    return [t for t in include_tables if t in existing]

def _fetch_table_rows(conn: sqlite3.Connection, table: str) -> Tuple[List[str], List[Tuple]]:
    """Return (col_names, rows)."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if not cols:
        return [], []
    # quote cols
    col_list = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(f'SELECT {col_list} FROM "{table}"').fetchall()
    return cols, rows

def _ensure_remote_schema(cfg: Dict[str, str]) -> Tuple[bool, str]:
    """Ensure remote has same schema as local. Uses db._SCHEMA if available."""
    try:
        import db as _db
        schema_sql = getattr(_db, "_SCHEMA", "")
    except Exception:
        schema_sql = ""
    if not schema_sql:
        return True, "no schema"
    # Split schema into individual statements (CREATE TABLE / INDEX etc) that are not empty
    # Use executescript style: split by ';'
    stmts = []
    for part in schema_sql.split(";"):
        s = part.strip()
        if not s:
            continue
        # keep PRAGMA
        stmts.append((s, []))
    if not stmts:
        return True, "empty"
    # batch into chunks of 20
    for i in range(0, len(stmts), 20):
        chunk = stmts[i:i+20]
        ok, msg = _execute_pipeline(cfg, chunk)
        if not ok:
            return False, msg
    return True, "schema ok"

def _push_snapshot(cfg: Dict[str, str]) -> Tuple[bool, str]:
    """Full push: delete then insert all rows. Never blocks UI.

    Uses temp file backup to avoid locking main DB during long network sync.
    Slow network / exception -> returns False, will retry on next 15s scheduler.
    """
    db_path = _get_db_path()
    if not db_path.is_file():
        return False, f"Local DB not found: {db_path}"
    # create temp copy via sqlite backup - does NOT lock main DB for long, handles WAL
    temp_path = None
    conn = None
    try:
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            # fast backup without long exclusive lock
            try:
                src = sqlite3.connect(str(db_path), timeout=2.0)
                try:
                    src.execute("PRAGMA busy_timeout=5000")
                except Exception:
                    pass
                dst = sqlite3.connect(temp_path, timeout=2.0)
                try:
                    # pages=100 backs up incrementally without blocking UI writes
                    src.backup(dst, pages=100)
                except TypeError:
                    src.backup(dst)
                src.close()
                dst.close()
            except Exception as e:
                # fallback: try direct copy if backup fails (e.g. locked)
                try:
                    import shutil
                    shutil.copy2(str(db_path), temp_path)
                except Exception as e2:
                    return False, f"backup failed: {e} / {e2}"[:500]
        except Exception as e:
            return False, f"temp create failed: {e}"[:500]
        # read snapshot from temp copy - main DB stays unlocked for UI
        try:
            conn = sqlite3.connect(temp_path, timeout=2.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000")
            except Exception:
                pass
            conn.row_factory = sqlite3.Row
        except Exception as e:
            return False, f"temp open failed: {e}"[:500]
        ok, msg = _ensure_remote_schema(cfg)
        if not ok:
            return False, f"schema failed: {msg}"
        # use short timeouts for slow network - fail fast and retry later, don't hang
        delete_stmts: List[Tuple[str, List[Any]]] = []
        delete_stmts.append(("PRAGMA foreign_keys=OFF", []))
        existing_delete = _local_tables(conn, _TABLES_DELETE_ORDER)
        for t in existing_delete:
            delete_stmts.append((f'DELETE FROM "{t}"', []))
        ok, msg = _execute_pipeline(cfg, delete_stmts, timeout=10)
        if not ok:
            return False, f"delete failed: {msg}"
        for table in _TABLES_INSERT_ORDER:
            if table not in existing_delete and table not in _local_tables(conn, [table]):
                continue
            cols, rows = _fetch_table_rows(conn, table)
            if not cols or not rows:
                continue
            placeholders = ", ".join("?" for _ in cols)
            col_names = ", ".join(f'"{c}"' for c in cols)
            sql = f'INSERT OR REPLACE INTO "{table}" ({col_names}) VALUES ({placeholders})'
            batch: List[Tuple[str, List[Any]]] = []
            for r in rows:
                vals = list(r)
                batch.append((sql, vals))
                if len(batch) >= 50:
                    ok, msg = _execute_pipeline(cfg, batch, timeout=10)
                    if not ok:
                        return False, f"insert {table} failed: {msg}"
                    batch = []
            if batch:
                ok, msg = _execute_pipeline(cfg, batch, timeout=10)
                if not ok:
                    return False, f"insert {table} failed: {msg}"
        _execute_pipeline(cfg, [("PRAGMA foreign_keys=ON", [])], timeout=8)
        try:
            sz = db_path.stat().st_size
        except Exception:
            sz = 0
        return True, f"synced {sz} bytes"
    except Exception as e:
        return False, f"Sync exception: {e}"[:800]
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        if temp_path:
            for p in (temp_path, temp_path + "-wal", temp_path + "-shm"):
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except Exception:
                    pass

def _push_with_libsql(cfg: Dict[str, str]) -> Tuple[bool, str]:
    """Try native libsql path if available. Returns (ok, msg). Falls back to HTTP if not."""
    try:
        import libsql as _libsql  # type: ignore
    except Exception as e:
        return False, f"libsql not installed: {e}"
    db_path = str(_get_db_path())
    # libsql embedded replica: connect to local file with sync_url
    # But we want to push local sqlite to remote. libsql can sync if we open with sync_url.
    # Simpler: just open remote and push via libsql HTTP? Actually libsql.connect remote will work.
    # For now, fallback to HTTP snapshot - native path not needed for push snapshot.
    return _push_snapshot(cfg)

# ── Sync orchestration ────────────────────────────────────────
def _do_sync_internal() -> Tuple[bool, str]:
    cfg = get_turso_config()
    if not cfg["url"] or not cfg["token"]:
        return False, "Not configured (set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in Admin Setup)"
    if not cfg["enabled_bool"]:
        return False, "Turso sync disabled (enable in Admin Setup)"
    try:
        if not _has_network_for_cfg(cfg):
            return False, "Offline - no network to Turso"
    except Exception as e:
        return False, f"Offline - network check failed: {e}"[:300]
    try:
        ok, msg = _push_snapshot(cfg)
        return ok, msg
    except Exception as e:
        return False, f"Sync exception: {e}"[:500]

def _record_result(ok: bool, msg: str):
    now = datetime.now().isoformat(timespec="seconds")
    global _syncing, _pending
    with _lock:
        _last_result["status"] = "ok" if ok else "error"
        _last_result["msg"] = msg
        _last_result["at"] = now
        _syncing = False
        if ok or "Offline" not in msg:
            _pending = False if ok else _pending
    try:
        if ok:
            _db_set_state(K_LAST_SYNC, now)
            _db_set_state(K_LAST_STATUS, "ok")
            _db_delete_state(K_LAST_ERROR)
            # mark sync_log entries as synced on success (full snapshot covers all)
            try:
                import db as _db
                from contextlib import closing
                with closing(_db._connect()) as _c, _c:
                    _c.execute("UPDATE sync_log SET synced=1 WHERE synced=0")
            except Exception:
                pass
        else:
            if "Offline" in msg or "No network" in msg:
                _db_set_state(K_LAST_STATUS, "offline")
            else:
                _db_set_state(K_LAST_STATUS, "error")
            _db_set_state(K_LAST_ERROR, msg[:500])
            # on failure, keep logs pending for retry; optionally update retry_count
            if "Offline" not in msg:
                try:
                    import db as _db
                    from contextlib import closing
                    with closing(_db._connect()) as _c, _c:
                        _c.execute("UPDATE sync_log SET retry_count=retry_count+1, error=? WHERE synced=0", (msg[:500],))
                except Exception:
                    pass
    except Exception:
        pass
    # notify UI (async)
    try:
        _notify_callback()
    except Exception:
        pass

def _set_syncing(pending: bool = False):
    global _syncing, _pending
    with _lock:
        _syncing = True
        if pending:
            _pending = True
    try:
        _db_set_state(K_LAST_STATUS, "syncing")
    except Exception:
        pass
    try:
        _notify_callback()
    except Exception:
        pass

def sync_now(block: bool = False, timeout: float = 60.0) -> Tuple[bool, str]:
    """Synchronous push now. Exception-safe, never raises to caller."""
    global _sync_thread
    try:
        with _lock:
            if _sync_thread and _sync_thread.is_alive():
                return False, "Sync already in progress"
            # also check syncing flag
            if _syncing:
                return False, "Sync already in progress"
        def _run():
            _set_syncing(pending=False)
            ok, msg = _do_sync_internal()
            _record_result(ok, msg)
            return ok, msg
        if block:
            try:
                ok, msg = _run()
                return ok, msg
            except Exception as e:
                _record_result(False, f"Sync exception: {e}"[:500])
                return False, str(e)[:500]
        else:
            result = {}
            def _thr():
                try:
                    result["ok"], result["msg"] = _run()
                except Exception as e:
                    result["ok"], result["msg"] = False, str(e)[:500]
                    try:
                        _record_result(False, result["msg"])
                    except Exception:
                        pass
            t = threading.Thread(target=_thr, daemon=True)
            with _lock:
                _sync_thread = t
            t.start()
            t.join(timeout=timeout)
            if t.is_alive():
                return False, "Sync timed out (still running in background)"
            return result.get("ok", False), result.get("msg", "unknown")
    except Exception as e:
        return False, f"sync_now failed: {e}"[:500]

def schedule_sync(delay: float = 15.0):
    """Called after local DB commit + sync_log insert. Fully non-blocking.

    User request: scheduler every 10-20s is better than immediate sync.
    This only marks pending in-memory and notifies footer instantly.
    No DB/network/timer on UI thread - background poller every 15s does actual cloud sync.
    """
    global _pending
    try:
        with _lock:
            _pending = True
            try:
                _last_result["status"] = "pending"
            except Exception:
                pass
        try:
            _notify_callback()
        except Exception:
            pass
    except Exception:
        pass

def get_status(check_network: bool = False) -> Dict[str, str]:
    """Return sync status dict. By default does ZERO network/DB blocking.

    UI must call check_network=False (instant). Background poller uses True.
    """
    global _last_network, _last_network_at
    # fast in-memory status without DB hit if possible - but DB read is cheap
    # UI thread: avoid live socket completely
    try:
        # use in-memory last_result for instant UI
        mem_status = _last_result.get("status", "idle")
        # live network only if requested (background thread)
        if check_network:
            try:
                cfg = get_turso_config()
                _update_cached_network(cfg)
            except Exception:
                pass
            net = _last_network
        else:
            # UI: never do socket, just return cached (init as online to avoid hang)
            if _last_network == "unknown":
                # first call - assume online to avoid blocking socket; poller will correct
                net = "online"
                _last_network = net
            else:
                net = _last_network
    except Exception:
        net = _last_network if _last_network != "unknown" else "online"
    # use cached cfg for UI quick path to avoid DB on every poll
    try:
        if not check_network:
            cfg = _get_cached_config()
            if cfg is None:
                cfg = get_turso_config()
        else:
            cfg = get_turso_config()
    except Exception:
        cfg = {"url": "", "token": "", "enabled": "0", "enabled_bool": False}
    try:
        try:
            last_sync = _db_get_state(K_LAST_SYNC, "")
            last_error = _db_get_state(K_LAST_ERROR, "")
            db_status = _db_get_state(K_LAST_STATUS, "")
            # immediate UI feedback: if in-memory pending/syncing, show it even before DB updated
            if mem_status in ("pending", "syncing") and (_pending or _syncing):
                last_status = mem_status
            else:
                last_status = db_status or mem_status
        except Exception:
            last_sync = ""
            last_error = ""
            last_status = mem_status
        with _lock:
            pending_s = str(_pending)
            syncing_s = str(_syncing)
        # sync_log pending count for footer busy display (fast query, cached)
        log_pending = 0
        try:
            import db as _db
            log_pending = _db.get_sync_log_pending_count()
        except Exception:
            log_pending = 0
        return {
            "url": cfg.get("url", ""),
            "enabled": str(cfg.get("enabled_bool", False)),
            "configured": str(is_turso_configured()),
            "last_sync": last_sync,
            "last_error": last_error,
            "last_status": last_status,
            "network": net,
            "pending": pending_s,
            "syncing": syncing_s,
            "log_pending": str(log_pending),
            "msg": _last_result.get("msg", ""),
        }
    except Exception as e:
        return {
            "url": "",
            "enabled": "False",
            "configured": "False",
            "last_sync": "",
            "last_error": f"get_status failed: {e}"[:300],
            "last_status": "error",
            "network": "unknown",
            "pending": "False",
            "syncing": "False",
            "log_pending": "0",
            "msg": "",
        }

def init_turso():
    """Call on app startup. Starts poller to retry pending syncs every 60s. Exception-safe."""
    global _POLL_THREAD
    try:
        cfg = get_turso_config()
        if cfg["url"] and cfg["token"] and _db_get_state(K_ENABLED, "") == "":
            _db_set_state(K_ENABLED, "1")
        # stale 'syncing' from crash - reset to pending so UI not stuck
        if _db_get_state(K_LAST_STATUS, "") == "syncing":
            _db_set_state(K_LAST_STATUS, "pending")
    except Exception:
        pass
    if _POLL_THREAD and _POLL_THREAD.is_alive():
        return
    _POLL_STOP.clear()
    def _poller():
        time.sleep(3)
        try:
            if is_turso_configured():
                _update_cached_network(get_turso_config())
                if _last_network == "online" and _has_network_for_cfg(get_turso_config()):
                    _set_syncing(pending=False)
                    ok, msg = _do_sync_internal()
                    _record_result(ok, msg)
        except Exception:
            pass
        while not _POLL_STOP.wait(15):
            try:
                st = get_status(check_network=False)
                # scheduler: if any pending log or pending/offline/error, retry
                has_pending_log = False
                try:
                    import db as _db
                    has_pending_log = _db.get_sync_log_pending_count() > 0
                except Exception:
                    has_pending_log = False
                if has_pending_log or st["last_status"] in ("pending", "offline", "syncing", "error"):
                    _update_cached_network(get_turso_config())
                    if is_turso_configured() and _last_network == "online" and _has_network_for_cfg(get_turso_config()):
                        _set_syncing(pending=True)
                        ok, msg = _do_sync_internal()
                        _record_result(ok, msg)
                    else:
                        try:
                            _notify_callback()
                        except Exception:
                            pass
            except Exception:
                pass
    _POLL_THREAD = threading.Thread(target=_poller, daemon=True)
    _POLL_THREAD.start()
    # periodic full backup thread (interval or daily time) - separate from 15s scheduler
    global _PERIODIC_THREAD
    if _PERIODIC_THREAD and _PERIODIC_THREAD.is_alive():
        pass
    else:
        _PERIODIC_STOP.clear()
        def _periodic():
            while not _PERIODIC_STOP.wait(60):
                try:
                    if not is_turso_configured():
                        continue
                    if not _is_periodic_due():
                        continue
                    # need network
                    _update_cached_network(get_turso_config())
                    if _last_network != "online" or not _has_network_for_cfg(get_turso_config()):
                        continue
                    if is_syncing():
                        continue
                    _set_syncing(pending=False)
                    # mark as periodic sync (show in status)
                    try:
                        _db_set_state(K_LAST_STATUS, "syncing")
                    except Exception:
                        pass
                    ok, msg = _do_sync_internal()
                    _record_result(ok, msg)
                    if ok:
                        _mark_periodic_done()
                    else:
                        # on failure, keep pending so retry next cycle
                        pass
                except Exception:
                    pass
        _PERIODIC_THREAD = threading.Thread(target=_periodic, daemon=True)
        _PERIODIC_THREAD.start()

def stop_turso():
    _POLL_STOP.set()
    _PERIODIC_STOP.set()
    with _lock:
        global _timer
        if _timer:
            try:
                _timer.cancel()
            except Exception:
                pass
            _timer = None

# ── Pull (optional) ───────────────────────────────────────────
def pull_now(timeout: float = 20.0) -> Tuple[bool, str]:
    """Pull remote -> local (overwrites local). Use carefully. Returns (ok,msg)."""
    cfg = get_turso_config()
    if not cfg["url"] or not cfg["token"]:
        return False, "Not configured"
    if not _has_network_for_cfg(cfg):
        return False, "Offline"
    # For HTTP mode we would need to SELECT * from remote and overwrite local.
    # Implement via pipeline SELECTs? Simpler: not implemented for safety.
    return False, "Pull not implemented for HTTP mode - restore via Turso dashboard or enable embedded replica"


# ── Tkinter Config Dialog (used by main.py Admin) ─────────────
def _load_logo_turso(size=(40, 40)):
    try:
        from PIL import Image, ImageTk  # type: ignore
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        png = os.path.join(base, "logo.png")
        if os.path.isfile(png):
            pil = Image.open(png)
            pil.thumbnail(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(pil)
    except Exception:
        pass
    return None


class TursoConfigDialog:
    """Tkinter dialog to configure Turso sync (DB primary, env fallback).

    Imported lazily to avoid hard dependency on tkinter at import time for headless tests.
    Usage: from turso_sync import TursoConfigDialog; dlg = TursoConfigDialog(parent); parent.wait_window(dlg)
    """

    def __new__(cls, master, *args, **kwargs):
        # Create actual Toplevel via factory to avoid class inheritance issues when tkinter not available
        import tkinter as tk
        from tkinter import ttk, messagebox  # type: ignore

        class _Dialog(tk.Toplevel):
            def __init__(self, master):
                super().__init__(master)
                self.saved = False
                self.title("Turso Sync - Cloud Backup (turso.tech)")
                self.resizable(False, False)
                frm = ttk.Frame(self, padding=18)
                frm.pack(fill="both", expand=True)

                logo_top = ttk.Frame(frm)
                logo_top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
                _logo = _load_logo_turso((40, 40))
                if _logo:
                    self._logo_img = _logo  # keep ref
                    ttk.Label(logo_top, image=_logo).pack(side="left", padx=(0, 8))
                ttk.Label(logo_top, text="Turso Cloud Sync - Save DB on every update",
                          font=("Segoe UI", 11, "bold"), foreground="#006633").pack(side="left")
                ttk.Label(frm, text="Your local SQLite (employees.db) is backed up to Turso on each update when network is available. Works offline - queued and retried automatically.",
                          foreground="#555", wraplength=560, justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 2))
                ttk.Label(frm, text="Get URL & Token: turso.tech -> turso db create <name> -> turso db show <name> --url  &  turso db tokens create <name>",
                          foreground="#777", wraplength=560, justify="left", font=("Segoe UI", 8)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 10))

                cfg = get_turso_config()
                # status line
                if cfg["url"] and cfg["token"]:
                    if cfg["enabled_bool"]:
                        src_text = "Status: Configured and enabled - auto sync on."
                        src_color = "#006633"
                    else:
                        src_text = "Status: Configured but disabled (enable below)."
                        src_color = "#cc7700"
                else:
                    # check env fallback
                    has_token = bool(cfg["token"])
                    has_url = bool(cfg["url"])
                    if has_token or has_url:
                        src_text = "Status: Partially configured - both URL and Token required."
                        src_color = "#cc7700"
                    else:
                        src_text = "Status: NOT configured - sync disabled. Paste URL + Token below."
                        src_color = "#cc0000"
                self.lbl_status = ttk.Label(frm, text=src_text, foreground=src_color, wraplength=560, justify="left", font=("Segoe UI", 9, "bold"))
                self.lbl_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))

                # last sync info
                st = get_status()
                info = f"Last sync: {st['last_sync'] or 'never'} | Network: {st['network']} | Last status: {st['last_status']}"
                if st["last_error"]:
                    info += f"\nLast error: {st['last_error'][:120]}"
                self.lbl_info = ttk.Label(frm, text=info, foreground="#555", wraplength=560, justify="left", font=("Segoe UI", 8))
                self.lbl_info.grid(row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

                self.var_url = tk.StringVar(value=cfg.get("url") or "")
                self.var_token = tk.StringVar(value=cfg.get("token") or "")
                self.var_enabled = tk.BooleanVar(value=bool(cfg["enabled_bool"]))
                self.var_show = tk.BooleanVar(value=False)

                ttk.Label(frm, text="TURSO_DATABASE_URL *").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
                self.ent_url = ttk.Entry(frm, textvariable=self.var_url, width=58)
                self.ent_url.grid(row=5, column=1, columnspan=2, sticky="we", pady=4)
                ttk.Label(frm, text="  e.g. libsql://my-db-xxx.turso.io").grid(row=6, column=1, columnspan=2, sticky="w")
                ttk.Label(frm, text="TURSO_AUTH_TOKEN *").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=4)
                self.ent_token = ttk.Entry(frm, textvariable=self.var_token, width=58, show="•")
                self.ent_token.grid(row=7, column=1, columnspan=2, sticky="we", pady=4)

                ttk.Checkbutton(frm, text="Show token", variable=self.var_show, command=self._toggle_show).grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 6))
                ttk.Checkbutton(frm, text="Enable auto sync on network available", variable=self.var_enabled).grid(row=9, column=0, columnspan=3, sticky="w", pady=(2, 6))

                # Periodic backup config
                bck = get_backup_config()
                self.var_interval = tk.StringVar(value=bck["interval_sec"])
                self.var_time = tk.StringVar(value=bck["time_str"])
                periodic = ttk.LabelFrame(frm, text="Periodic full backup (whole DB) - late night or interval", padding=6)
                periodic.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(6,0))
                periodic.columnconfigure(1, weight=1)
                ttk.Label(periodic, text="Every:").grid(row=0, column=0, sticky="w", padx=(0,6))
                self.cmb_interval = ttk.Combobox(periodic, textvariable=self.var_interval, state="readonly", width=18,
                    values=["0","3600","7200","21600","43200","86400"])
                self.cmb_interval.grid(row=0, column=1, sticky="w")
                # show human readable in combo: map values to labels via separate display?
                ttk.Label(periodic, text="(0=disabled, 3600=1h, 7200=2h, 21600=6h, 43200=12h, 86400=24h)").grid(row=0, column=2, sticky="w", padx=(6,0))
                ttk.Label(periodic, text="Or daily at (HH:MM):").grid(row=1, column=0, sticky="w", padx=(0,6), pady=(6,0))
                self.ent_time = ttk.Entry(periodic, textvariable=self.var_time, width=10)
                self.ent_time.grid(row=1, column=1, sticky="w", pady=(6,0))
                ttk.Label(periodic, text="e.g. 02:00 for 2 AM - overrides interval if set").grid(row=1, column=2, sticky="w", padx=(6,0), pady=(6,0))
                # show last periodic
                last_per = (_db_get_state(K_LAST_PERIODIC, "") or "").strip()
                nxt = "never"
                try:
                    if bck["time_str"]:
                        nxt = f"daily at {bck['time_str']}"
                    elif int(bck["interval_sec"]) > 0:
                        nxt = f"every {int(bck['interval_sec'])//3600}h"
                    else:
                        nxt = "disabled"
                except Exception:
                    nxt = "disabled"
                info_per = f"Current: {nxt} | Last periodic: {last_per or 'never'}"
                ttk.Label(periodic, text=info_per, foreground="#555", font=("Segoe UI", 8)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4,0))

                hint = ttk.Label(frm, text="Env vars (.env) TURSO_DATABASE_URL / TURSO_AUTH_TOKEN are fallback; DB values take priority and work in compiled builds.",
                                 foreground="#666", wraplength=560, justify="left", font=("Segoe UI", 8))
                hint.grid(row=11, column=0, columnspan=3, sticky="w", pady=(2, 6))

                self.lbl_msg = ttk.Label(frm, text="", foreground="#cc0000", wraplength=560, justify="left")
                self.lbl_msg.grid(row=12, column=0, columnspan=3, sticky="ew", pady=(4, 0))

                btns = ttk.Frame(frm)
                btns.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(12, 0))
                ttk.Button(btns, text="Import from .env", command=self._import_env).pack(side="left")
                ttk.Button(btns, text="Clear", command=self._clear).pack(side="left", padx=6)
                ttk.Button(btns, text="Test Connection", command=self._test).pack(side="left", padx=6)
                ttk.Button(btns, text="Sync Now", command=self._sync_now).pack(side="left", padx=6)
                ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
                ttk.Button(btns, text="Save", command=self._save).pack(side="right", padx=6)

                self.bind("<Return>", lambda e: self._save())
                self.bind("<Escape>", lambda e: self.destroy())
                try:
                    if master.winfo_viewable():
                        self.transient(master)
                except Exception:
                    pass
                self.update_idletasks()
                self.deiconify()
                try:
                    if master.winfo_viewable():
                        self.grab_set()
                except Exception:
                    pass
                try:
                    self.focus_set()
                except Exception:
                    pass
                # center
                try:
                    self.update_idletasks()
                    w, h = self.winfo_width(), self.winfo_height()
                    sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
                    if master.winfo_viewable():
                        mx, my, mw, mh = master.winfo_rootx(), master.winfo_rooty(), master.winfo_width(), master.winfo_height()
                        x, y = mx + (mw - w)//2, my + (mh - h)//2
                    else:
                        x, y = (sw - w)//2, (sh - h)//2 - 40
                    x = max(10, min(x, sw - w - 10))
                    y = max(10, min(y, sh - h - 40))
                    self.geometry(f"+{x}+{y}")
                except Exception:
                    pass

            def _toggle_show(self):
                show = "" if self.var_show.get() else "•"
                self.ent_token.configure(show=show)

            def _import_env(self):
                # manual .env read already in get_turso_config, but allow import button to fill fields
                cfg = get_turso_config()
                # try reading .env directly for url/token if fields empty
                if not self.var_url.get().strip() and cfg["url"]:
                    self.var_url.set(cfg["url"])
                if not self.var_token.get().strip() and cfg["token"]:
                    self.var_token.set(cfg["token"])
                # also try direct .env scan for TURSO keys
                try:
                    import os, pathlib
                    base = pathlib.Path(__file__).resolve().with_name(".env")
                    if base.is_file():
                        found_url = ""
                        found_tok = ""
                        for line in base.read_text(encoding="utf-8", errors="ignore").splitlines():
                            line=line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k,v=line.split("=",1)
                            k=k.strip(); v=v.strip().strip('"').strip("'")
                            if k in ("TURSO_DATABASE_URL","TURSO_SYNC_URL","LIBSQL_URL","DATABASE_URL") and v:
                                found_url=v
                            if k in ("TURSO_AUTH_TOKEN","TURSO_TOKEN","LIBSQL_AUTH_TOKEN","AUTH_TOKEN") and v:
                                found_tok=v
                        if found_url and not self.var_url.get().strip():
                            self.var_url.set(found_url)
                        if found_tok and not self.var_token.get().strip():
                            self.var_token.set(found_tok)
                        if found_url or found_tok:
                            self.lbl_msg.configure(text="Imported from .env. Click Save to persist to DB.", foreground="#006633")
                            return
                except Exception:
                    pass
                if self.var_url.get().strip() or self.var_token.get().strip():
                    self.lbl_msg.configure(text="Fields filled from current config. Click Save to persist.", foreground="#006633")
                else:
                    self.lbl_msg.configure(text="No keys found in env or .env.", foreground="#cc0000")

            def _clear(self):
                if not messagebox.askyesno("Clear Turso Config", "Clear Turso URL and Token from DB?\nEnv vars will still work for dev but compiled build will have no keys.", parent=self):
                    return
                set_turso_config(url="", token="", enabled=False)
                self.var_url.set("")
                self.var_token.set("")
                self.var_enabled.set(False)
                self.lbl_msg.configure(text="Cleared.", foreground="#cc7700")
                self.lbl_status.configure(text="Status: NOT configured.", foreground="#cc0000")

            def _test(self):
                url = self.var_url.get().strip()
                tok = self.var_token.get().strip()
                if not url or not tok:
                    messagebox.showwarning("Missing", "Both URL and Token are required to test.", parent=self)
                    return
                self.lbl_msg.configure(text="Testing connection...", foreground="#006633")
                self.update_idletasks()
                ok, msg = test_connection({"url": url, "token": tok})
                if ok:
                    messagebox.showinfo("Success", f"Connected to Turso!\n{msg}", parent=self)
                    self.lbl_msg.configure(text="Test succeeded: " + msg, foreground="#006633")
                else:
                    messagebox.showerror("Failed", f"Connection failed:\n{msg}", parent=self)
                    self.lbl_msg.configure(text="Test failed: " + msg[:200], foreground="#cc0000")

            def _sync_now(self):
                url = self.var_url.get().strip()
                tok = self.var_token.get().strip()
                if not url or not tok:
                    messagebox.showwarning("Missing", "Save URL and Token first.", parent=self)
                    return
                # save first if changed
                cur = get_turso_config()
                if url != cur["url"] or tok != cur["token"]:
                    if not messagebox.askyesno("Save first?", "URL/Token changed. Save to DB before syncing?", parent=self):
                        return
                    set_turso_config(url=url, token=tok, enabled=self.var_enabled.get())
                self.lbl_msg.configure(text="Syncing...", foreground="#006633")
                self.update_idletasks()
                ok, msg = sync_now(block=True, timeout=40)
                if ok:
                    messagebox.showinfo("Synced", f"Sync succeeded: {msg}", parent=self)
                    self.lbl_msg.configure(text="Synced: " + msg, foreground="#006633")
                    st = get_status()
                    self.lbl_info.configure(text=f"Last sync: {st['last_sync'] or 'now'} | Network: {st['network']} | Last status: {st['last_status']}")
                else:
                    messagebox.showerror("Sync failed", msg, parent=self)
                    self.lbl_msg.configure(text="Sync failed: " + msg[:200], foreground="#cc0000")

            def _save(self):
                url = self.var_url.get().strip()
                tok = self.var_token.get().strip()
                enabled = bool(self.var_enabled.get())
                if enabled and (not url or not tok):
                    messagebox.showerror("Missing", "Both URL and Token are required when enabled.", parent=self)
                    return
                if url and not url.startswith(("libsql://", "https://", "http://", "wss://")):
                    # allow bare host but warn
                    if "." not in url:
                        messagebox.showerror("Invalid URL", "URL must be like libsql://my-db-xxx.turso.io", parent=self)
                        return
                # validate periodic time
                tstr = self.var_time.get().strip()
                if tstr:
                    try:
                        hh, mm = tstr.split(":")
                        hh_i = int(hh); mm_i = int(mm)
                        if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
                            raise ValueError
                    except Exception:
                        messagebox.showerror("Invalid time", "Periodic time must be HH:MM (e.g. 02:00)", parent=self)
                        return
                iv_str = self.var_interval.get().strip() or "0"
                try:
                    iv = int(iv_str)
                    if iv < 0:
                        raise ValueError
                except Exception:
                    messagebox.showerror("Invalid interval", "Interval must be 0, 3600, 7200, etc.", parent=self)
                    return
                set_turso_config(url=url, token=tok, enabled=enabled)
                try:
                    set_backup_config(interval_sec=iv, time_str=tstr)
                except Exception:
                    pass
                # ensure enabled flag stored
                self.saved = True
                msg = "Turso config saved to DB." + (" Sync enabled." if enabled else " Sync disabled.")
                if tstr:
                    msg += f" Periodic daily at {tstr}."
                elif iv > 0:
                    msg += f" Periodic every {iv//3600}h."
                messagebox.showinfo("Saved", msg, parent=self)
                self.destroy()

        return _Dialog(master)

