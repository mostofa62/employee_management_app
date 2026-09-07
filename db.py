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
    """)


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


# ── Organizations ──────────────────────────────────────────────

def ensure_organization(name):
    text = " ".join((name or "").split())
    if not text:
        return None
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT id FROM organizations WHERE name = ?", (text,)).fetchone()
        if row:
            return row["id"]
        try:
            cur = conn.execute("INSERT INTO organizations (name) VALUES (?)", (text,))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM organizations WHERE name = ?", (text,)).fetchone()
            if not row:
                raise
            return row["id"]


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
        project_id = ensure_project(project) if project else None
        organization_id = ensure_organization(organization) if organization else None
        # Re-resolve IDs within same connection to avoid separate connection race;
        # ensure_* already commits, but get ids again via lookup to keep FK consistent
        if project and project_id is None:
            project_id = ensure_project(project)
        if organization and organization_id is None:
            organization_id = ensure_organization(organization)
        cur = conn.execute(
            """INSERT INTO employee_tenures
               (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes),
        )
        return cur.lastrowid


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
        project_id = ensure_project(project) if project else None
        organization_id = ensure_organization(organization) if organization else None
        conn.execute(
            """UPDATE employee_tenures
               SET emp_id = ?, join_date = ?, release_date = ?, tenure_type = ?,
                   project_id = ?, organization_id = ?, role = ?, notes = ?
               WHERE id = ?""",
            (emp_id, join_date, release_date, tenure_type, project_id, organization_id, role, notes, tenure_id),
        )


def delete_tenure(tenure_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employee_tenures WHERE id = ?", (tenure_id,))


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
        project_id = ensure_project(project) if project else None
        organization_id = ensure_organization(organization) if organization else None
        cur = conn.execute(
            """INSERT INTO employee_assignments
               (emp_id, tenure_id, project_id, organization_id, role, start_date, end_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (emp_id, tenure_id, project_id, organization_id, role, start_date, end_date, notes),
        )
        return cur.lastrowid


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
        project_id = ensure_project(project) if project else None
        organization_id = ensure_organization(organization) if organization else None
        conn.execute(
            """UPDATE employee_assignments
               SET emp_id=?, tenure_id=?, project_id=?, organization_id=?, role=?, start_date=?, end_date=?, notes=?
               WHERE id=?""",
            (emp_id, tenure_id, project_id, organization_id, (role or "").strip(), start_date, end_date, (notes or "").strip(), assign_id),
        )


def delete_assignment(assign_id):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM employee_assignments WHERE id = ?", (assign_id,))


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
