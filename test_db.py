import os
import sys
import tempfile

os.environ["EMPLOYEE_VISITS_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

db.init_db()
passed = failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {label}")
    else:
        failed += 1
        print(f"FAIL  {label}")


def expect_block(label, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        check(f"{label} -> blocked ({str(e).splitlines()[0][:60]}...)", str(e).startswith("BLOCKED"))
    else:
        check(label, False)


db.add_employee("E001", "Alice Rahman", "Manager", "01711111111", "02-9123456", "alice@x.com", 2)
db.add_employee("E002", "Bob Karim", "Director", "01822222222", "", "bob@x.com", 0)
db.add_employee("E003", "Carol Akter", "Engineer", "01933333333", "", "", 3)

try:
    db.add_employee("E001", "Dup", "X")
    check("duplicate emp_id rejected", False)
except ValueError as e:
    check("duplicate emp_id rejected", "already exists" in str(e))

db.add_visit("E001", "India", "Training", "SQL course", "2026-03-10")
db.add_visit("E001", "Japan", "Conference", "Tech summit", "2026-07-01")
check("E001 usage 2026 = 2", db.yearly_usage("E001", "2026") == 2)
expect_block("E001 3rd visit 2026", db.add_visit, "E001", "Nepal", "Meeting", "", "2026-08-15")

db.add_visit("E001", "Thailand", "Workshop", "", "2025-05-20")
check("E001 new year resets limit", db.yearly_usage("E001", "2025") == 1)

for i in range(1, 6):
    db.add_visit("E002", "Qatar", f"Trip {i}", "", f"2026-01-{i:02d}")
check("unlimited employee not blocked (5 visits)", db.yearly_usage("E002", "2026") == 5)

for i, m in enumerate(["02", "03", "04"], start=1):
    db.add_visit("E003", "Malaysia", f"Job {i}", "", f"2026-{m}-11")
expect_block("E003 4th visit 2026 (limit=3)", db.add_visit, "E003", "Singapore", "Audit", "", "2026-09-09")

vid = db.list_visits(year="2026", emp_id="E003")[0]["id"]
expect_block(
    "moving another employee's visit into full year",
    db.update_visit,
    vid,
    "E001",
    "India",
    "Moved",
    "",
    "2026-12-25",
)
db.update_visit(vid, "E003", "Indonesia", "Renamed purpose", "details here", "2026-04-11")
row = db.get_visit(vid)
check("update keeps own slot when re-dated same year", row["country"] == "Indonesia" and row["purpose_title"] == "Renamed purpose")

try:
    db.add_visit("E001", "China", "Bad date", "", "2026/08/01")
    check("bad date rejected", False)
except ValueError as e:
    check("bad date rejected", "YYYY-MM-DD" in str(e))

summary_2026 = {r["emp_id"]: r for r in db.summary("2026")}
check("summary used E001=2", summary_2026["E001"]["used_this_year"] == 2)
check("summary used E002=5", summary_2026["E002"]["used_this_year"] == 5)
check("summary used E003=3", summary_2026["E003"]["used_this_year"] == 3)
check("E001 max reached once (2026)", summary_2026["E001"]["times_max_reached"] == 1)
check("E002 unlimited never counted", summary_2026["E002"]["times_max_reached"] == 0)

db.delete_employee("E001")
check("delete employee cascades visits", len(db.list_visits(emp_id="E001")) == 0)

try:
    db.update_employee("E999", "EX", "Ghost", "None")
    check("update missing employee rejected", False)
except ValueError:
    check("update missing employee rejected", True)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
