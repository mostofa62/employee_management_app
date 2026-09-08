import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(
    os.environ.get("EMPLOYEE_VISITS_DB")
    or Path(__file__).resolve().with_name("employees.db")
)

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS organizations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS employees (
    emp_id          TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    designation     TEXT NOT NULL,
    phone_primary   TEXT NOT NULL DEFAULT '',
    phone_secondary TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL DEFAULT '',
    max_visits      INTEGER NOT NULL DEFAULT 2 CHECK (max_visits >= 0),
    emp_type        TEXT NOT NULL DEFAULT 'Government'
                    CHECK (emp_type IN ('Government', 'Non-Government')),
    project_id      INTEGER REFERENCES projects (id)
);

CREATE TABLE IF NOT EXISTS visits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id         TEXT NOT NULL REFERENCES employees (emp_id) ON DELETE CASCADE,
    country        TEXT NOT NULL,
    purpose_title  TEXT NOT NULL,
    purpose_detail TEXT NOT NULL DEFAULT '',
    visit_date     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visits_emp_date ON visits (emp_id, visit_date);

CREATE TABLE IF NOT EXISTS employee_tenures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id          TEXT NOT NULL REFERENCES employees (emp_id) ON DELETE CASCADE,
    join_date       TEXT NOT NULL,
    release_date    TEXT,
    tenure_type     TEXT NOT NULL DEFAULT 'Government'
                    CHECK (tenure_type IN ('Government', 'Project', 'Contract', 'Deputation', 'Other')),
    project_id      INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    organization_id INTEGER REFERENCES organizations (id) ON DELETE SET NULL,
    role            TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    CHECK (release_date IS NULL OR release_date >= join_date)
);

CREATE INDEX IF NOT EXISTS idx_tenures_emp_join ON employee_tenures (emp_id, join_date);
CREATE INDEX IF NOT EXISTS idx_tenures_emp_release ON employee_tenures (emp_id, release_date);

CREATE TABLE IF NOT EXISTS employee_assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id          TEXT NOT NULL REFERENCES employees (emp_id) ON DELETE CASCADE,
    tenure_id       INTEGER REFERENCES employee_tenures (id) ON DELETE SET NULL,
    project_id      INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    organization_id INTEGER REFERENCES organizations (id) ON DELETE SET NULL,
    role            TEXT NOT NULL DEFAULT '',
    start_date      TEXT NOT NULL,
    end_date        TEXT,
    notes           TEXT NOT NULL DEFAULT '',
    CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_assign_emp_start ON employee_assignments (emp_id, start_date);

CREATE TABLE IF NOT EXISTS app_users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    phone          TEXT NOT NULL UNIQUE,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin','user')),
    is_active      INTEGER NOT NULL DEFAULT 1,
    is_logged_in   INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    last_login     TEXT
);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    operation  TEXT NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
    payload    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    synced     INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sync_log_synced ON sync_log (synced, created_at);
"""


def _migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(employees)")}
    if "emp_type" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN emp_type TEXT NOT NULL DEFAULT 'Government'")
    if "project_id" not in cols:
        conn.execute(
            "ALTER TABLE employees ADD COLUMN project_id INTEGER REFERENCES projects (id)"
        )
    # Create new tables for existing DBs (IF NOT EXISTS covers it, but ensure indexes)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS employee_tenures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id          TEXT NOT NULL REFERENCES employees (emp_id) ON DELETE CASCADE,
            join_date       TEXT NOT NULL,
            release_date    TEXT,
            tenure_type     TEXT NOT NULL DEFAULT 'Government'
                            CHECK (tenure_type IN ('Government', 'Project', 'Contract', 'Deputation', 'Other')),
            project_id      INTEGER REFERENCES projects (id) ON DELETE SET NULL,
            organization_id INTEGER REFERENCES organizations (id) ON DELETE SET NULL,
            role            TEXT NOT NULL DEFAULT '',
            notes           TEXT NOT NULL DEFAULT '',
            CHECK (release_date IS NULL OR release_date >= join_date)
        );
        CREATE INDEX IF NOT EXISTS idx_tenures_emp_join ON employee_tenures (emp_id, join_date);
        CREATE INDEX IF NOT EXISTS idx_tenures_emp_release ON employee_tenures (emp_id, release_date);
        CREATE TABLE IF NOT EXISTS employee_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id          TEXT NOT NULL REFERENCES employees (emp_id) ON DELETE CASCADE,
            tenure_id       INTEGER REFERENCES employee_tenures (id) ON DELETE SET NULL,
            project_id      INTEGER REFERENCES projects (id) ON DELETE SET NULL,
            organization_id INTEGER REFERENCES organizations (id) ON DELETE SET NULL,
            role            TEXT NOT NULL DEFAULT '',
            start_date      TEXT NOT NULL,
            end_date        TEXT,
            notes           TEXT NOT NULL DEFAULT '',
            CHECK (end_date IS NULL OR end_date >= start_date)
        );
        CREATE INDEX IF NOT EXISTS idx_assign_emp_start ON employee_assignments (emp_id, start_date);
        CREATE TABLE IF NOT EXISTS app_users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            phone          TEXT NOT NULL UNIQUE,
            email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash  TEXT NOT NULL,
            password_salt  TEXT NOT NULL,
            role           TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin','user')),
            is_active      INTEGER NOT NULL DEFAULT 1,
            is_logged_in   INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL DEFAULT (datetime('now')),
            last_login     TEXT
        );
        CREATE TABLE IF NOT EXISTS app_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id  TEXT NOT NULL,
            operation  TEXT NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
            payload    TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            synced     INTEGER NOT NULL DEFAULT 0,
            error      TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sync_log_synced ON sync_log (synced, created_at);
    """)
    # ensure app_users columns for old installs
    try:
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(app_users)")}
        if "is_logged_in" not in ucols:
            conn.execute("ALTER TABLE app_users ADD COLUMN is_logged_in INTEGER NOT NULL DEFAULT 0")
        if "role" not in ucols:
            conn.execute("ALTER TABLE app_users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        if "is_active" not in ucols:
            conn.execute("ALTER TABLE app_users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass


def _connect():
    # WAL + busy_timeout + synchronous NORMAL keeps UI responsive when background sync runs
    # check_same_thread False allows use from any thread (Tk + sync worker)
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0, check_same_thread=False, isolation_level=None)
    except TypeError:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    try:
        conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        # First-time create: ensure WAL mode and checkpoint so file is self-contained
        # (required for Turso file upload / embedded replica and safe offline backup)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    # Auto-seed Supabase keys from env/.env into DB config table if DB is empty
    # (helps first-run and avoids manual re-entry; compiled builds then have keys)
    try:
        cfg = get_supabase_config()
        if not (cfg.get("api_key") or "").strip() and not (cfg.get("secret_key") or "").strip():
            env_api = (os.environ.get("SUPABASE_API_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
            env_sec = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
            # also try .env file directly (covers compiled build where python-dotenv may not have loaded yet)
            if not env_api or not env_sec:
                try:
                    base = Path(__file__).resolve().with_name(".env")
                    if base.is_file():
                        for line in base.read_text(encoding="utf-8", errors="ignore").splitlines():
                            line=line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k,v=line.split("=",1)
                            k=k.strip(); v=v.strip().strip('"').strip("'")
                            if k=="SUPABASE_API_KEY" and not env_api and v:
                                env_api=v
                            elif k=="SUPABASE_SECRET_KEY" and not env_sec and v:
                                env_sec=v
                except Exception:
                    pass
            if env_api or env_sec:
                if env_api:
                    set_app_state("supabase_api_key", env_api)
                if env_sec:
                    set_app_state("supabase_secret_key", env_sec)
    except Exception:
        pass


EMPLOYEE_TYPES = ("Government", "Non-Government")


def ensure_project(name, _do_sync=True):
    """Ensure project exists, return id. Sync only on final save when _do_sync True."""
    text = " ".join((name or "").split())
    if not text:
        return None
    with closing(_connect()) as conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (text,)).fetchone()
        if row:
            return row["id"]
    inserted = False
    with closing(_connect()) as conn, conn:
        try:
            cur = conn.execute("INSERT INTO projects (name) VALUES (?)", (text,))
            row_id = cur.lastrowid
            inserted = True
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM projects WHERE name = ?", (text,)).fetchone()
            if not row:
                raise
            row_id = row["id"]
            inserted = False
    if inserted and _do_sync:
        _log_sync('projects', row_id, 'INSERT', {'name': text})
    return row_id


def list_projects():
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT p.id, p.name, COUNT(e.emp_id) AS employees_used
               FROM projects p
               LEFT JOIN employees e ON e.project_id = p.id
               GROUP BY p.id
               ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()


def delete_project(project_id):
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not exists:
            raise ValueError("This project no longer exists.")
        conn.execute("UPDATE employees SET project_id = NULL WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    _log_sync('projects', project_id, 'DELETE')


def _clean_employee(emp_id, name, designation, phone_primary, phone_secondary, email, max_visits, emp_type):
    emp_id = (emp_id or "").strip()
    name = (name or "").strip()
    designation = (designation or "").strip()
    phone_primary = (phone_primary or "").strip()
    phone_secondary = (phone_secondary or "").strip()
    email = (email or "").strip()
    if not emp_id:
        raise ValueError("Employee ID is required.")
    if not name:
        raise ValueError("Name is required.")
    if not designation:
        raise ValueError("Designation is required.")
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        raise ValueError("Please enter a valid email address.")
    try:
        max_visits = int(max_visits)
    except (TypeError, ValueError):
        raise ValueError("Yearly visit limit must be a whole number.")
    if max_visits < 0:
        raise ValueError("Yearly visit limit cannot be negative. Use 0 for unlimited.")
    emp_type = (emp_type or "").strip() or "Government"
    if emp_type not in EMPLOYEE_TYPES:
        raise ValueError(f"Employee type must be one of: {', '.join(EMPLOYEE_TYPES)}.")
    return {
        "emp_id": emp_id,
        "name": name,
        "designation": designation,
        "phone_primary": phone_primary,
        "phone_secondary": phone_secondary,
        "email": email,
        "max_visits": max_visits,
        "emp_type": emp_type,
    }


def add_employee(emp_id, name, designation, phone_primary="", phone_secondary="", email="", max_visits=2,
                 emp_type="Government", project=None):
    data = _clean_employee(emp_id, name, designation, phone_primary, phone_secondary, email, max_visits, emp_type)
    project_id = ensure_project(project, _do_sync=False)
    with closing(_connect()) as conn, conn:
        try:
            conn.execute(
                """INSERT INTO employees
                   (emp_id, name, designation, phone_primary, phone_secondary, email, max_visits,
                    emp_type, project_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(data.values()) + (project_id,),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Employee ID '{data['emp_id']}' already exists.")
    _log_sync('employees', data['emp_id'], 'INSERT', data)


def update_employee(original_emp_id, emp_id, name, designation, phone_primary="", phone_secondary="",
                    email="", max_visits=2, emp_type="Government", project=None):
    data = _clean_employee(emp_id, name, designation, phone_primary, phone_secondary, email, max_visits, emp_type)
    project_id = ensure_project(project, _do_sync=False)
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (original_emp_id,)).fetchone()
        if not exists:
            raise ValueError(f"Employee '{original_emp_id}' no longer exists.")
        clash = conn.execute(
            "SELECT 1 FROM employees WHERE emp_id = ? AND emp_id <> ?",
            (data["emp_id"], original_emp_id),
        ).fetchone()
        if clash:
            raise ValueError(f"Employee ID '{data['emp_id']}' is already used by another employee.")
        conn.execute(
            """UPDATE employees
               SET emp_id = ?, name = ?, designation = ?, phone_primary = ?,
                   phone_secondary = ?, email = ?, max_visits = ?, emp_type = ?, project_id = ?
               WHERE emp_id = ?""",
            tuple(data.values()) + (project_id, original_emp_id),
        )
    _log_sync('employees', data['emp_id'], 'UPDATE', data)


def delete_employee(emp_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employees WHERE emp_id = ?", (emp_id,))
    _log_sync('employees', emp_id, 'DELETE')


def get_employees(search=""):
    query = """SELECT e.*, p.name AS project_name
               FROM employees e LEFT JOIN projects p ON p.id = e.project_id"""
    params = []
    term = (search or "").strip()
    if term:
        query += " WHERE e.emp_id LIKE ? OR e.name LIKE ? OR e.designation LIKE ? OR e.email LIKE ? OR p.name LIKE ?"
        like = f"%{term}%"
        params = [like, like, like, like, like]
    query += " ORDER BY e.name COLLATE NOCASE"
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchall()


def get_employee(emp_id):
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT e.*, p.name AS project_name
               FROM employees e LEFT JOIN projects p ON p.id = e.project_id
               WHERE e.emp_id = ?""",
            (emp_id,),
        ).fetchone()


def normalize_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = (value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError("Date of visit must be in YYYY-MM-DD format.")


def yearly_usage(emp_id, year):
    year = str(year)
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE emp_id = ? AND substr(visit_date, 1, 4) = ?",
            (emp_id, year),
        ).fetchone()
        return row[0]


def _assert_visit_allowed(conn, emp_id, visit_date, exclude_id=None):
    emp = conn.execute("SELECT name, max_visits FROM employees WHERE emp_id = ?", (emp_id,)).fetchone()
    if emp is None:
        raise ValueError("Please select a valid employee.")
    limit = emp["max_visits"]
    if limit <= 0:
        return
    year = visit_date[:4]
    sql = "SELECT COUNT(*) FROM visits WHERE emp_id = ? AND substr(visit_date, 1, 4) = ?"
    params = [emp_id, year]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    used = conn.execute(sql, params).fetchone()[0]
    if used >= limit:
        name = emp["name"]
        raise ValueError(
            f"BLOCKED: \"{name}\" ({emp_id}) has already used {used} of {limit} abroad "
            f"visits in {year}.\nRule: an employee may visit abroad at most {limit} time(s) "
            f"within one calendar year.\nThis entry cannot be saved."
        )


def _clean_visit(emp_id, country, purpose_title, purpose_detail, visit_date):
    emp_id = (emp_id or "").strip()
    country = (country or "").strip()
    purpose_title = (purpose_title or "").strip()
    purpose_detail = (purpose_detail or "").strip()
    visit_date = normalize_date(visit_date)
    if not emp_id:
        raise ValueError("Please select an employee.")
    if not country:
        raise ValueError("Country is required.")
    if not purpose_title:
        raise ValueError("Purpose title is required.")
    return emp_id, country, purpose_title, purpose_detail, visit_date


def add_visit(emp_id, country, purpose_title, purpose_detail, visit_date):
    emp_id, country, purpose_title, purpose_detail, visit_date = _clean_visit(
        emp_id, country, purpose_title, purpose_detail, visit_date
    )
    with closing(_connect()) as conn, conn:
        _assert_visit_allowed(conn, emp_id, visit_date)
        cur = conn.execute(
            """INSERT INTO visits (emp_id, country, purpose_title, purpose_detail, visit_date)
               VALUES (?, ?, ?, ?, ?)""",
            (emp_id, country, purpose_title, purpose_detail, visit_date),
        )
        row_id = cur.lastrowid
    _log_sync('visits', row_id, 'INSERT', {'emp_id': emp_id, 'country': country, 'visit_date': visit_date})
    return row_id


def update_visit(visit_id, emp_id, country, purpose_title, purpose_detail, visit_date):
    emp_id, country, purpose_title, purpose_detail, visit_date = _clean_visit(
        emp_id, country, purpose_title, purpose_detail, visit_date
    )
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM visits WHERE id = ?", (visit_id,)).fetchone()
        if not exists:
            raise ValueError("This visit record no longer exists.")
        _assert_visit_allowed(conn, emp_id, visit_date, exclude_id=visit_id)
        conn.execute(
            """UPDATE visits
               SET emp_id = ?, country = ?, purpose_title = ?, purpose_detail = ?, visit_date = ?
                WHERE id = ?""",
            (emp_id, country, purpose_title, purpose_detail, visit_date, visit_id),
        )
    _log_sync('visits', visit_id, 'UPDATE', {'emp_id': emp_id, 'country': country, 'visit_date': visit_date})


def get_visit(visit_id):
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT v.*, e.name AS emp_name
               FROM visits v JOIN employees e ON e.emp_id = v.emp_id
               WHERE v.id = ?""",
            (visit_id,),
        ).fetchone()


def delete_visit(visit_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
    _log_sync('visits', visit_id, 'DELETE')


def list_visits(year=None, emp_id=None):
    query = """SELECT v.id, v.visit_date, v.emp_id, e.name AS emp_name,
                      v.country, v.purpose_title, v.purpose_detail,
                      p.name AS project_name
               FROM visits v JOIN employees e ON e.emp_id = v.emp_id
               LEFT JOIN projects p ON p.id = e.project_id"""
    where, params = [], []
    if year and str(year) != "All":
        where.append("substr(v.visit_date, 1, 4) = ?")
        params.append(str(year))
    if emp_id:
        where.append("v.emp_id = ?")
        params.append(emp_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY v.visit_date DESC, v.id DESC"
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchall()


def years_present():
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(visit_date, 1, 4) AS y FROM visits ORDER BY y DESC"
        ).fetchall()
        return [r["y"] for r in rows]


def summary(year):
    year = str(year)
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT e.emp_id, e.name, e.designation, e.max_visits,
                      e.emp_type, p.name AS project_name,
                      (SELECT COUNT(*) FROM visits v
                        WHERE v.emp_id = e.emp_id
                          AND substr(v.visit_date, 1, 4) = ?) AS used_this_year,
                      (SELECT COUNT(*) FROM (
                           SELECT substr(v.visit_date, 1, 4) AS y, COUNT(*) AS c
                           FROM visits v
                           WHERE v.emp_id = e.emp_id
                           GROUP BY substr(v.visit_date, 1, 4))
                       WHERE e.max_visits > 0 AND c >= e.max_visits) AS times_max_reached
               FROM employees e
               LEFT JOIN projects p ON p.id = e.project_id
               ORDER BY e.name COLLATE NOCASE""",
            (year,),
        ).fetchall()


# ── Organizations ──────────────────────────────────────────────

def ensure_organization(name, _do_sync=True):
    text = " ".join((name or "").split())
    if not text:
        return None
    with closing(_connect()) as conn:
        row = conn.execute("SELECT id FROM organizations WHERE name = ?", (text,)).fetchone()
        if row:
            return row["id"]
    inserted = False
    with closing(_connect()) as conn, conn:
        try:
            cur = conn.execute("INSERT INTO organizations (name) VALUES (?)", (text,))
            row_id = cur.lastrowid
            inserted = True
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM organizations WHERE name = ?", (text,)).fetchone()
            if not row:
                raise
            row_id = row["id"]
            inserted = False
    if inserted and _do_sync:
        _log_sync('organizations', row_id, 'INSERT', {'name': text})
    return row_id


def list_organizations():
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT o.id, o.name,
                      (SELECT COUNT(*) FROM employee_tenures t WHERE t.organization_id = o.id) AS tenures_used,
                      (SELECT COUNT(*) FROM employee_assignments a WHERE a.organization_id = o.id) AS assignments_used
               FROM organizations o
               ORDER BY o.name COLLATE NOCASE"""
        ).fetchall()


def delete_organization(org_id):
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM organizations WHERE id = ?", (org_id,)).fetchone()
        if not exists:
            raise ValueError("This organization no longer exists.")
        conn.execute("UPDATE employee_tenures SET organization_id = NULL WHERE organization_id = ?", (org_id,))
        conn.execute("UPDATE employee_assignments SET organization_id = NULL WHERE organization_id = ?", (org_id,))
        conn.execute("DELETE FROM organizations WHERE id = ?", (org_id,))
    _log_sync('organizations', org_id, 'DELETE')


# ── Tenure helpers ─────────────────────────────────────────────

TENURE_TYPES = ("Government", "Project", "Contract", "Deputation", "Other")


def _parse_tenure_date(value, field_name, allow_empty=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_empty:
            return None
        raise ValueError(f"{field_name} is required (YYYY-MM-DD).")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        if allow_empty:
            return None
        raise ValueError(f"{field_name} is required (YYYY-MM-DD).")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format.")


def _check_tenure_overlap(conn, emp_id, join_date, release_date, exclude_id=None):
    """Raise if new interval overlaps any existing tenure for the employee.

    NULL release_date is treated as open-ended (9999-12-31). Single-day
    release == join is not considered overlap with next join on next day,
    but overlapping on same day is forbidden.
    """
    new_end = release_date or "9999-12-31"
    sql = """SELECT id, join_date, release_date FROM employee_tenures
             WHERE emp_id = ?
               AND join_date <= ?
               AND COALESCE(release_date, '9999-12-31') >= ?"""
    params = [emp_id, new_end, join_date]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    rows = conn.execute(sql, params).fetchall()
    if rows:
        r = rows[0]
        end_disp = r["release_date"] or "ongoing"
        raise ValueError(
            f"Overlapping tenure with existing record #{r['id']} "
            f"({r['join_date']} -> {end_disp}). "
            f"An employee cannot have two overlapping postings at IEDCR."
        )


def add_tenure(emp_id, join_date, release_date=None, tenure_type="Government",
               project=None, organization=None, role="", notes=""):
    emp_id = (emp_id or "").strip()
    if not emp_id:
        raise ValueError("Employee is required.")
    join_date = _parse_tenure_date(join_date, "Join date")
    release_date = _parse_tenure_date(release_date, "Release date", allow_empty=True)
    if release_date and release_date < join_date:
        raise ValueError("Release date cannot be before join date.")
    _today = date.today().isoformat()
    if join_date > _today:
        raise ValueError("Join date cannot be in the future.")
    if release_date and release_date > _today:
        raise ValueError("Release date cannot be in the future.")
    tenure_type = (tenure_type or "Government").strip()
    if tenure_type not in TENURE_TYPES:
        raise ValueError(f"Tenure type must be one of: {', '.join(TENURE_TYPES)}.")
    role = (role or "").strip()
    notes = (notes or "").strip()
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (emp_id,)).fetchone()
        if not exists:
            raise ValueError(f"Employee '{emp_id}' does not exist.")
        _check_tenure_overlap(conn, emp_id, join_date, release_date)
        project_id = ensure_project(project, _do_sync=False) if project else None
        organization_id = ensure_organization(organization, _do_sync=False) if organization else None
        # Re-resolve IDs within same connection to avoid separate connection race;
        # ensure_* already commits, but get ids again via lookup to keep FK consistent
        if project and project_id is None:
            project_id = ensure_project(project, _do_sync=False)
        if organization and organization_id is None:
            organization_id = ensure_organization(organization, _do_sync=False)
        cur = conn.execute(
            """INSERT INTO employee_tenures
               (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes),
        )
        row_id = cur.lastrowid
    _log_sync('employee_tenures', row_id, 'INSERT', {'emp_id': emp_id, 'join_date': join_date})
    return row_id


def update_tenure(tenure_id, emp_id, join_date, release_date=None, tenure_type="Government",
                  project=None, organization=None, role="", notes=""):
    emp_id = (emp_id or "").strip()
    if not emp_id:
        raise ValueError("Employee is required.")
    join_date = _parse_tenure_date(join_date, "Join date")
    release_date = _parse_tenure_date(release_date, "Release date", allow_empty=True)
    if release_date and release_date < join_date:
        raise ValueError("Release date cannot be before join date.")
    _today = date.today().isoformat()
    if join_date > _today:
        raise ValueError("Join date cannot be in the future.")
    if release_date and release_date > _today:
        raise ValueError("Release date cannot be in the future.")
    tenure_type = (tenure_type or "Government").strip()
    if tenure_type not in TENURE_TYPES:
        raise ValueError(f"Tenure type must be one of: {', '.join(TENURE_TYPES)}.")
    role = (role or "").strip()
    notes = (notes or "").strip()
    with closing(_connect()) as conn, conn:
        exists = conn.execute("SELECT 1 FROM employee_tenures WHERE id = ?", (tenure_id,)).fetchone()
        if not exists:
            raise ValueError("This tenure record no longer exists.")
        emp_exists = conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (emp_id,)).fetchone()
        if not emp_exists:
            raise ValueError(f"Employee '{emp_id}' does not exist.")
        _check_tenure_overlap(conn, emp_id, join_date, release_date, exclude_id=tenure_id)
        project_id = ensure_project(project, _do_sync=False) if project else None
        organization_id = ensure_organization(organization, _do_sync=False) if organization else None
        conn.execute(
            """UPDATE employee_tenures
               SET emp_id = ?, join_date = ?, release_date = ?, tenure_type = ?,
                    project_id = ?, organization_id = ?, role = ?, notes = ?
                WHERE id = ?""",
            (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes, tenure_id),
        )
    _log_sync('employee_tenures', tenure_id, 'UPDATE', {'emp_id': emp_id, 'join_date': join_date})


def delete_tenure(tenure_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employee_tenures WHERE id = ?", (tenure_id,))
    _log_sync('employee_tenures', tenure_id, 'DELETE')


def get_tenure(tenure_id):
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT t.*, e.name AS emp_name, p.name AS project_name, o.name AS organization_name
               FROM employee_tenures t
               JOIN employees e ON e.emp_id = t.emp_id
               LEFT JOIN projects p ON p.id = t.project_id
               LEFT JOIN organizations o ON o.id = t.organization_id
               WHERE t.id = ?""",
            (tenure_id,),
        ).fetchone()


def list_tenures(emp_id=None, tenure_type=None, include_ongoing=True):
    query = """SELECT t.*, e.name AS emp_name, e.designation, p.name AS project_name, o.name AS organization_name
               FROM employee_tenures t
               JOIN employees e ON e.emp_id = t.emp_id
               LEFT JOIN projects p ON p.id = t.project_id
               LEFT JOIN organizations o ON o.id = t.organization_id"""
    where, params = [], []
    if emp_id:
        where.append("t.emp_id = ?")
        params.append(emp_id)
    if tenure_type and tenure_type != "All":
        where.append("t.tenure_type = ?")
        params.append(tenure_type)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY t.join_date DESC, t.id DESC"
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchall()


# ── Tenure summary / total interval ────────────────────────────

def _days_between(start_iso, end_iso):
    s = datetime.strptime(start_iso, "%Y-%m-%d").date()
    e = datetime.strptime(end_iso, "%Y-%m-%d").date()
    # Inclusive count: join day counts as 1
    return (e - s).days + 1


def _merge_intervals(intervals, today_iso=None):
    """Merge overlapping / contiguous intervals (inclusive).

    intervals: list of (join, release_or_None).  None -> today_iso.
    Returns merged list of (start, end) inclusive.
    """
    if not intervals:
        return []
    if today_iso is None:
        today_iso = date.today().isoformat()
    norm = []
    for s, e in intervals:
        norm.append((s, e or today_iso))
    norm.sort()
    merged = [list(norm[0])]
    for s, e in norm[1:]:
        last_s, last_e = merged[-1]
        # If s <= last_e + 1 day then merge (contiguous counts as continuous)
        last_e_d = datetime.strptime(last_e, "%Y-%m-%d").date()
        s_d = datetime.strptime(s, "%Y-%m-%d").date()
        if s_d <= last_e_d or (s_d - last_e_d).days == 1:
            # extend
            if e > last_e:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def _format_duration(total_days):
    years = total_days // 365
    rem = total_days % 365
    months = rem // 30
    days = rem % 30
    parts = []
    if years:
        parts.append(f"{years}y")
    if months:
        parts.append(f"{months}m")
    if days or not parts:
        parts.append(f"{days}d")
    return " ".join(parts)


def tenure_summary_for_employee(emp_id, today=None):
    """Return detailed tenure info for one employee.

    Result keys: intervals (list of rows with duration_days), merged_intervals,
    total_days, total_formatted, interval_count, first_join, last_release (None if ongoing),
    is_currently_active (bool), current_tenure (row or None).
    """
    if today is None:
        today_iso = date.today().isoformat()
    elif isinstance(today, (date, datetime)):
        today_iso = today.isoformat() if isinstance(today, date) and not isinstance(today, datetime) else today.date().isoformat() if isinstance(today, datetime) else str(today)
        # Normalize datetime -> date
        if isinstance(today, datetime):
            today_iso = today.date().isoformat()
        elif isinstance(today, date):
            today_iso = today.isoformat()
    else:
        today_iso = _parse_tenure_date(today, "today", allow_empty=True) or date.today().isoformat()

    rows = list_tenures(emp_id=emp_id)
    if not rows:
        return {
            "emp_id": emp_id,
            "intervals": [],
            "merged_intervals": [],
            "total_days": 0,
            "total_formatted": "0d",
            "interval_count": 0,
            "first_join": None,
            "last_release": None,
            "is_currently_active": False,
            "current_tenure": None,
        }
    intervals = []
    for r in rows:
        end = r["release_date"] or today_iso
        days = _days_between(r["join_date"], end)
        intervals.append({**dict(r), "duration_days": days, "duration_formatted": _format_duration(days)})

    raw_pairs = [(r["join_date"], r["release_date"]) for r in rows]
    merged = _merge_intervals(raw_pairs, today_iso=today_iso)
    total_days = sum(_days_between(s, e) for s, e in merged)
    # Determine first join and last release
    sorted_by_join = sorted(rows, key=lambda x: x["join_date"])
    first_join = sorted_by_join[0]["join_date"]
    # last_release is None if any ongoing (release_date IS NULL)
    ongoing = [r for r in rows if r["release_date"] is None]
    if ongoing:
        last_release = None
        is_active = True
        current_tenure = max(ongoing, key=lambda x: x["join_date"])
    else:
        last_release = max(r["release_date"] for r in rows)
        is_active = False
        current_tenure = None

    return {
        "emp_id": emp_id,
        "intervals": sorted(intervals, key=lambda x: x["join_date"]),
        "merged_intervals": merged,
        "total_days": total_days,
        "total_formatted": _format_duration(total_days),
        "interval_count": len(rows),
        "first_join": first_join,
        "last_release": last_release,
        "is_currently_active": is_active,
        "current_tenure": dict(current_tenure) if current_tenure else None,
    }


def tenure_summary_all(today=None):
    """Summary across all employees.

    Returns list of dicts sorted by emp name, each with emp_id, name, designation,
    interval_count, total_days, total_formatted, first_join, last_release,
    is_currently_active.
    """
    if today is None:
        today_iso = date.today().isoformat()
    else:
        today_iso = _parse_tenure_date(today, "today", allow_empty=True) or date.today().isoformat()
    with closing(_connect()) as conn:
        emps = conn.execute("SELECT emp_id, name, designation FROM employees ORDER BY name COLLATE NOCASE").fetchall()
    result = []
    for e in emps:
        s = tenure_summary_for_employee(e["emp_id"], today=today_iso)
        result.append({
            "emp_id": e["emp_id"],
            "name": e["name"],
            "designation": e["designation"],
            "interval_count": s["interval_count"],
            "total_days": s["total_days"],
            "total_formatted": s["total_formatted"],
            "merged_intervals": s["merged_intervals"],
            "first_join": s["first_join"],
            "last_release": s["last_release"],
            "is_currently_active": s["is_currently_active"],
        })
    # Sort currently active first optionally - keep name order
    return result


# ── Assignments (project/role/collaborator during a tenure) ───

def add_assignment(emp_id, start_date, end_date=None, project=None, organization=None,
                   role="", notes="", tenure_id=None):
    emp_id = (emp_id or "").strip()
    if not emp_id:
        raise ValueError("Employee is required.")
    start_date = _parse_tenure_date(start_date, "Start date")
    end_date = _parse_tenure_date(end_date, "End date", allow_empty=True)
    if end_date and end_date < start_date:
        raise ValueError("End date cannot be before start date.")
    role = (role or "").strip()
    notes = (notes or "").strip()
    with closing(_connect()) as conn, conn:
        if not conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (emp_id,)).fetchone():
            raise ValueError(f"Employee '{emp_id}' does not exist.")
        if tenure_id is not None:
            if not conn.execute("SELECT 1 FROM employee_tenures WHERE id = ?", (tenure_id,)).fetchone():
                raise ValueError("Linked tenure does not exist.")
        project_id = ensure_project(project, _do_sync=False) if project else None
        organization_id = ensure_organization(organization, _do_sync=False) if organization else None
        cur = conn.execute(
            """INSERT INTO employee_assignments
               (emp_id, tenure_id, project_id, organization_id, role, start_date, end_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (emp_id, tenure_id, project_id, organization_id, role, start_date, end_date, notes),
        )
        row_id = cur.lastrowid
    _log_sync('employee_assignments', row_id, 'INSERT', {'emp_id': emp_id, 'start_date': start_date})
    return row_id


def update_assignment(assign_id, emp_id, start_date, end_date=None, project=None, organization=None,
                      role="", notes="", tenure_id=None):
    start_date = _parse_tenure_date(start_date, "Start date")
    end_date = _parse_tenure_date(end_date, "End date", allow_empty=True)
    if end_date and end_date < start_date:
        raise ValueError("End date cannot be before start date.")
    with closing(_connect()) as conn, conn:
        if not conn.execute("SELECT 1 FROM employee_assignments WHERE id = ?", (assign_id,)).fetchone():
            raise ValueError("This assignment no longer exists.")
        if not conn.execute("SELECT 1 FROM employees WHERE emp_id = ?", (emp_id,)).fetchone():
            raise ValueError(f"Employee '{emp_id}' does not exist.")
        project_id = ensure_project(project, _do_sync=False) if project else None
        organization_id = ensure_organization(organization, _do_sync=False) if organization else None
        conn.execute(
            """UPDATE employee_assignments
               SET emp_id=?, tenure_id=?, project_id=?, organization_id=?, role=?, start_date=?, end_date=?, notes=?
                WHERE id=?""",
            (emp_id, tenure_id, project_id, organization_id, (role or "").strip(), start_date, end_date, (notes or "").strip(), assign_id),
        )
    _log_sync('employee_assignments', assign_id, 'UPDATE', {'emp_id': emp_id, 'start_date': start_date})


def delete_assignment(assign_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employee_assignments WHERE id = ?", (assign_id,))
    _log_sync('employee_assignments', assign_id, 'DELETE')


def list_assignments(emp_id=None, tenure_id=None):
    query = """SELECT a.*, e.name AS emp_name, p.name AS project_name, o.name AS organization_name
               FROM employee_assignments a
               JOIN employees e ON e.emp_id = a.emp_id
               LEFT JOIN projects p ON p.id = a.project_id
               LEFT JOIN organizations o ON o.id = a.organization_id"""
    where, params = [], []
    if emp_id:
        where.append("a.emp_id = ?")
        params.append(emp_id)
    if tenure_id is not None:
        where.append("a.tenure_id = ?")
        params.append(tenure_id)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY a.start_date DESC, a.id DESC"
    with closing(_connect()) as conn:
        return conn.execute(query, params).fetchall()


def get_assignment(assign_id):
    with closing(_connect()) as conn:
        return conn.execute(
            """SELECT a.*, e.name AS emp_name, p.name AS project_name, o.name AS organization_name
               FROM employee_assignments a
               JOIN employees e ON e.emp_id = a.emp_id
               LEFT JOIN projects p ON p.id = a.project_id
               LEFT JOIN organizations o ON o.id = a.organization_id
               WHERE a.id = ?""",
            (assign_id,),
        ).fetchone()


# ── Auth / Login ─────────────────────────────────────────────

def _hash_password(password, salt_hex=None):
    if salt_hex is None:
        salt = secrets.token_bytes(16)
        salt_hex = salt.hex()
    else:
        salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return dk.hex(), salt_hex


def _verify_password(password, stored_hash, stored_salt):
    calc, _ = _hash_password(password, stored_salt)
    return secrets.compare_digest(calc, stored_hash)


def _validate_phone(phone):
    p = (phone or "").strip()
    if not p:
        raise ValueError("Phone / mobile number is required.")
    # allow digits, +, -, spaces removed for strict check
    digits = re.sub(r"\D", "", p)
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("Enter a valid mobile number (8-15 digits).")
    return p


def _validate_email(email):
    e = (email or "").strip()
    if not e:
        raise ValueError("Email is required.")
    if "@" not in e or "." not in e.split("@")[-1]:
        raise ValueError("Please enter a valid email address.")
    return e.lower()


def _validate_name(name):
    n = (name or "").strip()
    if not n:
        raise ValueError("Name is required.")
    if len(n) < 2:
        raise ValueError("Name must be at least 2 characters.")
    return n


def count_users():
    with closing(_connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]


def list_app_users():
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM app_users ORDER BY id ASC").fetchall()


def get_user_by_id(user_id):
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM app_users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_identifier(identifier):
    ident = (identifier or "").strip()
    if not ident:
        return None
    with closing(_connect()) as conn:
        # try phone exact, then email case-insensitive
        row = conn.execute("SELECT * FROM app_users WHERE phone = ?", (ident,)).fetchone()
        if row:
            return row
        row = conn.execute("SELECT * FROM app_users WHERE LOWER(email) = LOWER(?)", (ident,)).fetchone()
        return row


def get_user_by_phone(phone):
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM app_users WHERE phone = ?", ((phone or "").strip(),)).fetchone()


def get_user_by_email(email):
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM app_users WHERE LOWER(email)=LOWER(?)", ((email or "").strip(),)).fetchone()


def create_app_user(name, phone, email, password, role="admin"):
    name = _validate_name(name)
    phone = _validate_phone(phone)
    email = _validate_email(email)
    pw = (password or "").strip()
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if role not in ("admin", "user"):
        role = "admin"
    # first user must be admin
    if count_users() == 0:
        role = "admin"
    pwd_hash, salt = _hash_password(pw)
    with closing(_connect()) as conn, conn:
        try:
            cur = conn.execute(
                "INSERT INTO app_users (name, phone, email, password_hash, password_salt, role) VALUES (?,?,?,?,?,?)",
                (name, phone, email, pwd_hash, salt, role),
            )
        except sqlite3.IntegrityError as exc:
            msg = str(exc).lower()
            if "phone" in msg:
                raise ValueError(f"Phone '{phone}' is already registered.")
            if "email" in msg:
                raise ValueError(f"Email '{email}' is already registered.")
            raise ValueError("Phone or email already registered.")
    _log_sync('app_users', cur.lastrowid, 'INSERT', {'phone': phone, 'email': email})
    return cur.lastrowid


def verify_login(identifier, password):
    ident = (identifier or "").strip()
    pw = (password or "")
    if not ident or not pw:
        return None
    user = get_user_by_identifier(ident)
    if not user:
        return None
    if not user["is_active"]:
        raise ValueError("This account is deactivated. Contact admin.")
    if not _verify_password(pw, user["password_hash"], user["password_salt"]):
        return None
    return user


def set_user_logged_in(user_id, logged_in=True):
    with closing(_connect()) as conn, conn:
        if logged_in:
            # exclusive login? keep simple - mark this user logged_in, others maybe keep
            conn.execute("UPDATE app_users SET is_logged_in=1, last_login=datetime('now') WHERE id=?", (user_id,))
        else:
            conn.execute("UPDATE app_users SET is_logged_in=0 WHERE id=?", (user_id,))
    _log_sync('app_users', user_id, 'UPDATE', {'is_logged_in': logged_in})


def logout_all():
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE app_users SET is_logged_in=0")
    _log_sync('app_users', 'all', 'UPDATE', {'is_logged_in': 0})


def update_user_password_by_phone(phone, new_password):
    phone = _validate_phone(phone)
    pw = (new_password or "").strip()
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters.")
    pwd_hash, salt = _hash_password(pw)
    with closing(_connect()) as conn, conn:
        cur = conn.execute("UPDATE app_users SET password_hash=?, password_salt=? WHERE phone=?", (pwd_hash, salt, phone))
        if cur.rowcount == 0:
            raise ValueError(f"No local user found with mobile '{phone}'.")
        rowc = cur.rowcount
    _log_sync('app_users', phone, 'UPDATE', {'password': 'reset'})
    return rowc


def update_user_password_by_id(user_id, new_password):
    pw = (new_password or "").strip()
    if len(pw) < 6:
        raise ValueError("Password must be at least 6 characters.")
    pwd_hash, salt = _hash_password(pw)
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE app_users SET password_hash=?, password_salt=? WHERE id=?", (pwd_hash, salt, user_id))
    _log_sync('app_users', user_id, 'UPDATE', {'password': 'reset'})


def set_app_state(key, value):
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO app_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    if not str(key).startswith("turso_") and not str(key).startswith("sync_"):
        _log_sync('app_state', key, 'UPDATE', {'key': key})


def get_app_state(key, default=None):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def delete_app_state(key):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM app_state WHERE key=?", (key,))
    if not str(key).startswith("turso_") and not str(key).startswith("sync_"):
        _log_sync('app_state', key, 'DELETE')


def get_stay_logged_in_user():
    val = get_app_state("stay_logged_in_user_id")
    if not val:
        return None
    stay = get_app_state("stay_logged_in")
    if stay != "1":
        return None
    try:
        uid = int(val)
    except Exception:
        return None
    user = get_user_by_id(uid)
    if not user or not user["is_active"]:
        return None
    return user


def set_stay_logged_in(user_id, enabled=True):
    if enabled:
        set_app_state("stay_logged_in", "1")
        set_app_state("stay_logged_in_user_id", str(user_id))
    else:
        delete_app_state("stay_logged_in")
        delete_app_state("stay_logged_in_user_id")
    # already logged via set/delete_app_state, no extra sync


def clear_stay_logged_in():
    delete_app_state("stay_logged_in")
    delete_app_state("stay_logged_in_user_id")
    # already logged


# ── App Config / Supabase Keys (persisted in DB for compiled builds) ──
# Environment variables cannot be packed into a compiled build, so these
# keys must also be configurable via the DB `app_state` table and managed
# from Admin Setup UI.
# Keys used: supabase_api_key, supabase_secret_key, supabase_url (optional override)

def get_supabase_config():
    """Return dict with api_key, secret_key, url from app_state (DB config)."""
    return {
        "api_key": get_app_state("supabase_api_key") or get_app_state("supabase_key") or "",
        "secret_key": get_app_state("supabase_secret_key") or "",
        "url": get_app_state("supabase_url") or "",
    }


def set_supabase_config(api_key=None, secret_key=None, url=None):
    """Persist Supabase keys to app_state. Empty/None clears the key.

    Returns the stored config dict.
    Validation is minimal here; UI layer should enforce required fields.
    """
    if api_key is not None:
        v = (api_key or "").strip()
        if v:
            set_app_state("supabase_api_key", v)
        else:
            delete_app_state("supabase_api_key")
            # also clear legacy fallback if present
            delete_app_state("supabase_key")
    if secret_key is not None:
        v = (secret_key or "").strip()
        if v:
            set_app_state("supabase_secret_key", v)
        else:
            delete_app_state("supabase_secret_key")
    if url is not None:
        v = (url or "").strip()
        if v:
            set_app_state("supabase_url", v)
        else:
            delete_app_state("supabase_url")
    # already logged via set/delete_app_state
    return get_supabase_config()


def is_supabase_configured():
    """True if both keys are present in DB or env (usable for Forgot Password)."""
    cfg = get_supabase_config()
    # also consider env as fallback
    api_env = (os.environ.get("SUPABASE_API_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
    sec_env = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    api = (cfg["api_key"] or api_env).strip()
    sec = (cfg["secret_key"] or sec_env).strip()
    # if only one is set, legacy mode allows single key for both headers
    return bool(api or sec)


# ── Turso Sync log + trigger (non-blocking) ─────────────
def _log_sync(table_name: str, record_id, operation: str, payload=None):
    """Insert sync_log entry then trigger background sync. Never blocks UI.

    - Local DB write is already committed before calling this.
    - Log insert runs in background thread (1-2ms) so delete/add never hangs.
    - payload optional, sync_log itself excluded from logging to avoid loop.
    """
    def _do_log():
        try:
            with closing(_connect()) as conn, conn:
                try:
                    p = json.dumps(payload, ensure_ascii=False) if payload is not None else None
                except Exception:
                    p = str(payload) if payload is not None else None
                conn.execute(
                    "INSERT INTO sync_log (table_name, record_id, operation, payload) VALUES (?,?,?,?)",
                    (str(table_name), str(record_id), str(operation), p),
                )
        except Exception:
            pass
        try:
            import turso_sync  # type: ignore
            turso_sync.schedule_sync()
        except Exception:
            pass
    try:
        threading.Thread(target=_do_log, daemon=True).start()
    except Exception:
        # fallback sync without log
        try:
            import turso_sync
            turso_sync.schedule_sync()
        except Exception:
            pass

def _trigger_turso_sync():
    """Legacy generic trigger: log generic event and schedule background sync."""
    def _do():
        try:
            with closing(_connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO sync_log (table_name, record_id, operation) VALUES (?,?,?)",
                    ("mixed", "0", "UPDATE"),
                )
        except Exception:
            pass
        try:
            import turso_sync  # type: ignore
            turso_sync.schedule_sync()
        except Exception:
            pass
    try:
        threading.Thread(target=_do, daemon=True).start()
    except Exception:
        try:
            import turso_sync
            turso_sync.schedule_sync()
        except Exception:
            pass

def get_sync_log_pending_count():
    try:
        with closing(_connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sync_log WHERE synced=0").fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0

def get_sync_log_recent(limit=20):
    try:
        with closing(_connect()) as conn:
            return conn.execute(
                "SELECT id, table_name, record_id, operation, created_at, synced, error FROM sync_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    except Exception:
        return []
