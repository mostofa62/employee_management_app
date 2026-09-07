import os
import sqlite3
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
"""


def _migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(employees)")}
    if "emp_type" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN emp_type TEXT NOT NULL DEFAULT 'Government'")
    if "project_id" not in cols:
        conn.execute(
            "ALTER TABLE employees ADD COLUMN project_id INTEGER REFERENCES projects (id)"
        )


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)


EMPLOYEE_TYPES = ("Government", "Non-Government")


def ensure_project(name):
    text = " ".join((name or "").split())
    if not text:
        return None
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT id FROM projects WHERE name = ?", (text,)).fetchone()
        if row:
            return row["id"]
        try:
            cur = conn.execute("INSERT INTO projects (name) VALUES (?)", (text,))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM projects WHERE name = ?", (text,)).fetchone()
            if not row:
                raise
            return row["id"]


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
    project_id = ensure_project(project)
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


def update_employee(original_emp_id, emp_id, name, designation, phone_primary="", phone_secondary="",
                    email="", max_visits=2, emp_type="Government", project=None):
    data = _clean_employee(emp_id, name, designation, phone_primary, phone_secondary, email, max_visits, emp_type)
    project_id = ensure_project(project)
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


def delete_employee(emp_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employees WHERE emp_id = ?", (emp_id,))


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
        return cur.lastrowid


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
