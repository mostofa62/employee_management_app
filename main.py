import datetime
import os
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from PIL import Image, ImageTk

import db
import auth
import reporting
from countries import COUNTRIES
try:
    import turso_sync  # type: ignore
    _HAS_TURSO = True
except Exception:
    turso_sync = None  # type: ignore
    _HAS_TURSO = False

DATE_HINT = "YYYY-MM-DD"


def _make_tree(parent, columns):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    for cid, text, width, anchor in columns:
        tree.heading(cid, text=text)
        tree.column(cid, width=width, anchor=anchor)
    return frame, tree


# ── Calendar / Date Picker ───────────────────────────────────
import calendar as _cal

def _parse_iso_date(text):
    s = (text or "").strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

def _open_calendar(var, parent_widget, title="Select date", disable_future=False):
    # try tkcalendar first if available
    try:
        from tkcalendar import Calendar as _TKCal  # type: ignore
        top = tk.Toplevel(parent_widget)
        top.title(title)
        top.resizable(False, False)
        top.transient(parent_widget)
        top.grab_set()
        init = _parse_iso_date(var.get()) or datetime.date.today()
        cal_kwargs = dict(selectmode="day", year=init.year, month=init.month, day=init.day,
                          date_pattern="yyyy-mm-dd", showweeknumbers=False)
        if disable_future:
            cal_kwargs["maxdate"] = datetime.date.today()
        cal = _TKCal(top, **cal_kwargs)
        cal.pack(padx=10, pady=10)
        def _pick():
            sel = cal.get_date()
            # enforce disable_future
            if disable_future:
                try:
                    d = datetime.datetime.strptime(sel, "%Y-%m-%d").date()
                    if d > datetime.date.today():
                        messagebox.showwarning("Invalid date", "Future dates are not allowed.", parent=top)
                        return
                except Exception:
                    pass
            var.set(sel)
            top.destroy()
        btns = ttk.Frame(top)
        btns.pack(fill="x", padx=10, pady=(0,10))
        ttk.Button(btns, text="Today", command=lambda: cal.selection_set(datetime.date.today())).pack(side="left")
        ttk.Button(btns, text="Select", command=_pick).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right")
        # position near widget
        try:
            x = parent_widget.winfo_rootx() + 20
            y = parent_widget.winfo_rooty() + parent_widget.winfo_height() + 6
            top.geometry(f"+{x}+{y}")
        except Exception:
            pass
        top.wait_window()
        return
    except Exception:
        pass
    _CalendarPopup(var, parent_widget, title, disable_future=disable_future)


class _CalendarPopup(tk.Toplevel):
    def __init__(self, var, parent_widget, title="Select date", disable_future=False):
        super().__init__(parent_widget)
        self.var = var
        self.disable_future = bool(disable_future)
        self._today = datetime.date.today()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent_widget)
        # self.grab_set() will be set after wait_visibility
        self._sel = _parse_iso_date(var.get()) or self._today
        # clamp initial selection to today if future disabled
        if self.disable_future and self._sel > self._today:
            self._sel = self._today
        self._view_year = self._sel.year
        self._view_month = self._sel.month
        self._build()
        # position near parent widget
        try:
            self.update_idletasks()
            x = parent_widget.winfo_rootx() + 10
            y = parent_widget.winfo_rooty() + parent_widget.winfo_height() + 4
            # keep on screen
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            w = self.winfo_reqwidth()
            h = self.winfo_reqheight()
            if x + w > sw - 10:
                x = sw - w - 10
            if y + h > sh - 40:
                y = parent_widget.winfo_rooty() - h - 4
                if y < 0:
                    y = 10
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass
        self.wait_visibility()
        self.grab_set()
        self.focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_out(self, evt):
        # don't close if focus goes to child
        try:
            if evt.widget is self and self.focus_get() is None:
                return
        except Exception:
            pass

    def _build(self):
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)

        nav = ttk.Frame(outer)
        nav.pack(fill="x", pady=(0, 6))
        self.btn_prev_year = ttk.Button(nav, width=3, text="◀◀", command=self._prev_year)
        self.btn_prev_year.pack(side="left")
        self.btn_prev_month = ttk.Button(nav, width=3, text="◀", command=self._prev_month)
        self.btn_prev_month.pack(side="left", padx=2)
        self.lbl_month = ttk.Label(nav, anchor="center", font=("Segoe UI", 10, "bold"))
        self.lbl_month.pack(side="left", expand=True, fill="x")
        self.btn_next_month = ttk.Button(nav, width=3, text="▶", command=self._next_month)
        self.btn_next_month.pack(side="right", padx=2)
        self.btn_next_year = ttk.Button(nav, width=3, text="▶▶", command=self._next_year)
        self.btn_next_year.pack(side="right")

        # weekday header
        hdr = ttk.Frame(outer)
        hdr.pack(fill="x")
        for idx, name in enumerate(["Mo","Tu","We","Th","Fr","Sa","Su"]):
            ttk.Label(hdr, text=name, anchor="center", width=4, font=("Segoe UI", 9, "bold"),
                      foreground="#006633" if idx < 5 else "#aa0000").grid(row=0, column=idx, padx=1)

        self.grid_frame = ttk.Frame(outer)
        self.grid_frame.pack(pady=(4, 6))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(6, 0))
        ttk.Button(bottom, text="Today", command=self._go_today).pack(side="left")
        ttk.Button(bottom, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")

        self._refresh()

    def _refresh(self):
        self.lbl_month.config(text=f"{_cal.month_name[self._view_month]} {self._view_year}")
        # navigation disable for future if needed
        if self.disable_future:
            # disable next if viewing current or future month
            cur_y, cur_m = self._today.year, self._today.month
            is_cur_or_future_month = (self._view_year, self._view_month) >= (cur_y, cur_m)
            is_cur_or_future_year = self._view_year >= cur_y
            try:
                self.btn_next_month.config(state="disabled" if is_cur_or_future_month else "normal")
                self.btn_next_year.config(state="disabled" if is_cur_or_future_year else "normal")
            except Exception:
                pass
        for w in self.grid_frame.winfo_children():
            w.destroy()
        first_wday, n_days = _cal.monthrange(self._view_year, self._view_month)
        # monthrange Monday=0 -> our header Monday=0, so offset is first_wday
        row = 0
        col = first_wday
        # leading blanks
        # we create 6 rows x 7 cols buttons; simpler fill sequentially
        today = self._today
        for day in range(1, n_days + 1):
            d = datetime.date(self._view_year, self._view_month, day)
            is_today = d == today
            is_selected = d == self._sel
            is_future = self.disable_future and d > today
            # need to place at correct row/col
            r = (first_wday + day - 1) // 7
            c = (first_wday + day - 1) % 7
            if is_future:
                btn = tk.Button(self.grid_frame, text=str(day), width=4, relief="flat",
                                bg="#e0e0e0", fg="#9e9e9e",
                                font=("Segoe UI", 9, "normal"),
                                bd=1, highlightthickness=0, state="disabled")
            else:
                btn = tk.Button(self.grid_frame, text=str(day), width=4, relief="flat",
                                bg=("#e8f5e9" if is_selected else ("#fff3cd" if is_today else "white")),
                                fg=("#006633" if is_selected else ("#aa0000" if c >=5 else "black")),
                                font=("Segoe UI", 9, "bold" if is_selected or is_today else "normal"),
                                bd=1, highlightthickness=0,
                                command=lambda d=d: self._pick(d))
                # hover
                btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#c8e6c9") if b["bg"] not in ("#e8f5e9",) else None)
                btn.bind("<Leave>", lambda e, b=btn, sel=is_selected, td=is_today: b.config(bg=("#e8f5e9" if sel else ("#fff3cd" if td else "white"))))
                if is_selected:
                    btn.config(relief="solid", bd=1)
            btn.grid(row=r, column=c, padx=1, pady=1, ipadx=2, ipady=2)

    def _pick(self, d):
        if self.disable_future and d > self._today:
            messagebox.showwarning("Invalid date", "Future dates are not allowed for tenure.", parent=self)
            return
        self.var.set(d.isoformat())
        self.destroy()

    def _prev_month(self):
        if self._view_month == 1:
            self._view_month = 12
            self._view_year -= 1
        else:
            self._view_month -= 1
        self._refresh()

    def _next_month(self):
        if self._view_month == 12:
            self._view_month = 1
            self._view_year += 1
        else:
            self._view_month += 1
        self._refresh()

    def _prev_year(self):
        self._view_year -= 1
        self._refresh()

    def _next_year(self):
        self._view_year += 1
        self._refresh()

    def _go_today(self):
        t = datetime.date.today()
        self._view_year, self._view_month = t.year, t.month
        self._sel = t
        self._refresh()
        self.var.set(t.isoformat())

    def _clear(self):
        self.var.set("")
        self.destroy()

def _make_date_picker_button(parent_frame, var, anchor_widget=None, disable_future=False):
    """Add a small calendar button next to an Entry bound to var. Returns button."""
    # Use text "Cal" (ASCII-safe) instead of emoji to avoid cp1252 console issues;
    # Tk renders fine; emoji can be enabled by changing to "\U0001F4C5" if font supports it.
    btn = ttk.Button(parent_frame, text="Cal", width=4,
                     command=lambda: _open_calendar(var, anchor_widget or parent_frame, disable_future=disable_future))
    return btn


class EmployeeDialog(tk.Toplevel):
    def __init__(self, master, employee=None):
        super().__init__(master)
        self.employee = employee
        self.saved = False
        self.title("Edit Employee" if employee else "Add Employee")
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=15)
        frm.grid(sticky="nsew")

        self.vars = {
            "emp_id": tk.StringVar(),
            "name": tk.StringVar(),
            "designation": tk.StringVar(),
            "phone_primary": tk.StringVar(),
            "phone_secondary": tk.StringVar(),
            "email": tk.StringVar(),
            "max_visits": tk.StringVar(value="2"),
        }
        base_rows = [
            ("Employee ID *", "emp_id", None),
            ("Name *", "name", None),
            ("Designation *", "designation", None),
            ("Phone (Primary)", "phone_primary", None),
            ("Phone (Secondary)", "phone_secondary", None),
            ("Email", "email", None),
            ("Max visits / year (0 = Unlimited)", "max_visits", "spin"),
        ]
        for i, (label, key, kind) in enumerate(base_rows):
            ttk.Label(frm, text=label + ":").grid(row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            if kind == "spin":
                ttk.Spinbox(frm, from_=0, to=999, textvariable=self.vars[key], width=28).grid(row=i, column=1, sticky="we", pady=4)
            else:
                ent = ttk.Entry(frm, textvariable=self.vars[key], width=30)
                ent.grid(row=i, column=1, sticky="we", pady=4)

        self.var_emp_type = tk.StringVar(value="Government")
        self.var_project = tk.StringVar()

        ttk.Label(frm, text="Employee type *").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Combobox(frm, textvariable=self.var_emp_type, state="readonly", width=28,
                     values=list(db.EMPLOYEE_TYPES)).grid(row=7, column=1, sticky="we", pady=4)
        ttk.Label(frm, text="Project").grid(row=8, column=0, sticky="w", padx=(0, 10), pady=4)
        cmb_project = ttk.Combobox(frm, textvariable=self.var_project, width=28)
        cmb_project.grid(row=8, column=1, sticky="we", pady=4)
        cmb_project["values"] = [p["name"] for p in db.list_projects()]
        hint = ttk.Label(frm, text="Type a new project or pick one - duplicates are merged automatically.",
                         foreground="#666666")
        hint.grid(row=9, column=1, sticky="w", pady=(0, 4))

        if employee:
            for key, var in self.vars.items():
                var.set(str(employee[key]))
            for w in frm.grid_slaves(row=0, column=1):
                w.state(["disabled"])
            self.var_emp_type.set(employee["emp_type"] or "Government")
            self.var_project.set(employee["project_name"] or "")

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())

        self.transient(master)
        self.wait_visibility()
        self.grab_set()
        self.focus_set()

    def _save(self):
        data = {key: var.get() for key, var in self.vars.items()}
        project = " ".join(self.var_project.get().split())
        try:
            if self.employee:
                db.update_employee(
                    self.employee["emp_id"],
                    data["emp_id"],
                    data["name"],
                    data["designation"],
                    data["phone_primary"],
                    data["phone_secondary"],
                    data["email"],
                    data["max_visits"],
                    emp_type=self.var_emp_type.get(),
                    project=project or None,
                )
            else:
                db.add_employee(
                    **data,
                    emp_type=self.var_emp_type.get(),
                    project=project or None,
                )
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        self.saved = True
        self.destroy()


class VisitForm(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=10)
        self.emp_map = {}
        self.on_change = None

        self.var_emp = tk.StringVar()
        self.var_country = tk.StringVar()
        self.var_title = tk.StringVar()
        self.var_date = tk.StringVar(value=datetime.date.today().isoformat())

        ttk.Label(self, text="Employee *").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.cmb_emp = ttk.Combobox(self, textvariable=self.var_emp, state="readonly", width=44)
        self.cmb_emp.grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(self, text="Country *").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.cmb_country = ttk.Combobox(self, textvariable=self.var_country, state="readonly", width=44, values=COUNTRIES)
        self.cmb_country.grid(row=1, column=1, sticky="we", pady=4)

        ttk.Label(self, text="Purpose title *").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(self, textvariable=self.var_title, width=46).grid(row=2, column=1, sticky="we", pady=4)

        ttk.Label(self, text="Purpose details").grid(row=3, column=0, sticky="nw", padx=(0, 10), pady=4)
        self.txt_detail = tk.Text(self, width=48, height=5, wrap="word", relief="solid", bd=1)
        self.txt_detail.grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(self, text="Date of visit *").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        date_wrap = ttk.Frame(self)
        date_wrap.grid(row=4, column=1, sticky="w", pady=4)
        self.ent_date = ttk.Entry(date_wrap, textvariable=self.var_date, width=13)
        self.ent_date.pack(side="left")
        _make_date_picker_button(date_wrap, self.var_date, self.ent_date, disable_future=False).pack(side="left", padx=(4, 0))
        ttk.Label(date_wrap, text=f"  ({DATE_HINT})").pack(side="left", padx=(4, 0))

        self.columnconfigure(1, weight=1)
        self.cmb_emp.bind("<<ComboboxSelected>>", lambda e: self._changed())
        self.var_date.trace_add("write", lambda *_: self._changed())

    def _changed(self):
        if self.on_change:
            self.on_change()

    def set_employees(self, emp_rows):
        previous = self.var_emp.get()
        self.emp_map = {
            f"{r['emp_id']} - {r['name']} ({r['designation']})": r for r in emp_rows
        }
        self.cmb_emp["values"] = list(self.emp_map)
        if previous in self.emp_map:
            self.var_emp.set(previous)

    def selected_employee(self):
        return self.emp_map.get(self.var_emp.get())

    def set_visit(self, record):
        target = None
        for disp, row in self.emp_map.items():
            if row["emp_id"] == record["emp_id"]:
                target = disp
                break
        if target:
            self.var_emp.set(target)
        self.var_country.set(record["country"])
        self.var_title.set(record["purpose_title"])
        self.txt_detail.delete("1.0", "end")
        self.txt_detail.insert("1.0", record["purpose_detail"])
        self.var_date.set(record["visit_date"])

    def clear(self, keep_employee=False):
        keep = self.var_emp.get() if keep_employee else ""
        self.var_country.set("")
        self.var_title.set("")
        self.txt_detail.delete("1.0", "end")
        self.var_date.set(datetime.date.today().isoformat())
        if keep and keep in self.emp_map:
            self.var_emp.set(keep)


class VisitDialog(tk.Toplevel):
    def __init__(self, master, visit_record, app):
        super().__init__(master)
        self.visit_record = visit_record
        self.app = app
        self.saved = False
        self.title(f"Edit Visit #{visit_record['id']}")

        box = ttk.Frame(self, padding=5)
        box.grid(sticky="nsew")
        self.form = VisitForm(box)
        self.form.grid(sticky="nsew")
        self.form.set_employees(db.get_employees())
        self.form.set_visit(visit_record)

        btns = ttk.Frame(box)
        btns.grid(row=99, column=0, columnspan=2, sticky="e", pady=(8, 2))
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        self.bind("<Escape>", lambda e: self.destroy())
        self.resizable(False, False)
        self.transient(master)
        self.wait_visibility()
        self.grab_set()
        self.focus_set()

    def _save(self):
        emp = self.form.selected_employee()
        if not emp:
            messagebox.showwarning("No employee", "Please select an employee.", parent=self)
            return
        try:
            db.update_visit(
                self.visit_record["id"],
                emp["emp_id"],
                self.form.var_country.get(),
                self.form.var_title.get(),
                self.form.txt_detail.get("1.0", "end"),
                self.form.var_date.get(),
            )
        except ValueError as exc:
            title = "Entry blocked" if str(exc).startswith("BLOCKED") else "Cannot update visit"
            messagebox.showerror(title, str(exc), parent=self)
            return
        self.saved = True
        self.app.set_status(f"Visit #{self.visit_record['id']} updated.")
        self.destroy()


class TenureDialog(tk.Toplevel):
    def __init__(self, master, tenure=None):
        super().__init__(master)
        self.tenure = tenure
        self.saved = False
        self.title("Edit Tenure" if tenure else "Add Tenure / Posting")
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=15)
        frm.grid(sticky="nsew")

        # Employee combobox
        ttk.Label(frm, text="Employee *").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_emp = tk.StringVar()
        self.cmb_emp = ttk.Combobox(frm, textvariable=self.var_emp, state="readonly", width=30)
        emps = db.get_employees()
        self.emp_map = {f"{r['emp_id']} - {r['name']}": r["emp_id"] for r in emps}
        self.cmb_emp["values"] = list(self.emp_map.keys())
        self.cmb_emp.grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="Join date * (YYYY-MM-DD)").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_join = tk.StringVar(value=datetime.date.today().isoformat())
        join_wrap = ttk.Frame(frm)
        join_wrap.grid(row=1, column=1, sticky="we", pady=4)
        self.ent_join = ttk.Entry(join_wrap, textvariable=self.var_join, width=24)
        self.ent_join.pack(side="left")
        self.btn_join_cal = _make_date_picker_button(join_wrap, self.var_join, self.ent_join, disable_future=True)
        self.btn_join_cal.pack(side="left", padx=(4, 0))

        ttk.Label(frm, text="Release date (YYYY-MM-DD)").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        release_wrap = ttk.Frame(frm)
        release_wrap.grid(row=2, column=1, sticky="we", pady=4)
        self.var_release = tk.StringVar()
        self.ent_release = ttk.Entry(release_wrap, textvariable=self.var_release, width=14)
        self.ent_release.pack(side="left")
        self.btn_release_cal = _make_date_picker_button(release_wrap, self.var_release, self.ent_release, disable_future=True)
        self.btn_release_cal.pack(side="left", padx=(4, 0))
        self.var_ongoing = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(release_wrap, text="Still working (no release)", variable=self.var_ongoing, command=self._toggle_release)
        chk.pack(side="left", padx=(8, 0))

        ttk.Label(frm, text="Tenure type *").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_type = tk.StringVar(value="Government")
        ttk.Combobox(frm, textvariable=self.var_type, state="readonly", width=28,
                     values=list(db.TENURE_TYPES)).grid(row=3, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="Project").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_project = tk.StringVar()
        cmb_proj = ttk.Combobox(frm, textvariable=self.var_project, width=28)
        cmb_proj.grid(row=4, column=1, sticky="we", pady=4)
        cmb_proj["values"] = [p["name"] for p in db.list_projects()]

        ttk.Label(frm, text="Collaborator Org.").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_org = tk.StringVar()
        cmb_org = ttk.Combobox(frm, textvariable=self.var_org, width=28)
        cmb_org.grid(row=5, column=1, sticky="we", pady=4)
        try:
            cmb_org["values"] = [o["name"] for o in db.list_organizations()]
        except Exception:
            cmb_org["values"] = []

        ttk.Label(frm, text="Role / Position").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=4)
        self.var_role = tk.StringVar()
        ttk.Entry(frm, textvariable=self.var_role, width=32).grid(row=6, column=1, sticky="we", pady=4)

        ttk.Label(frm, text="Notes").grid(row=7, column=0, sticky="nw", padx=(0, 10), pady=4)
        self.txt_notes = tk.Text(frm, width=34, height=3, wrap="word", relief="solid", bd=1)
        self.txt_notes.grid(row=7, column=1, sticky="we", pady=4)

        hint = ttk.Label(frm, text="Leave release blank if still working. Type new project/org to create automatically.",
                         foreground="#666666", wraplength=420, justify="left")
        hint.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))

        if tenure:
            # tenure is sqlite Row with joins
            disp = next((k for k, v in self.emp_map.items() if v == tenure["emp_id"]), None)
            if disp:
                self.var_emp.set(disp)
                self.cmb_emp.state(["disabled"])
            self.var_join.set(tenure["join_date"])
            if tenure["release_date"]:
                self.var_release.set(tenure["release_date"])
                self.var_ongoing.set(False)
            else:
                self.var_release.set("")
                self.var_ongoing.set(True)
            self.var_type.set(tenure["tenure_type"])
            self.var_project.set(tenure["project_name"] or "")
            self.var_org.set(tenure["organization_name"] or "")
            self.var_role.set(tenure["role"] or "")
            self.txt_notes.delete("1.0", "end")
            self.txt_notes.insert("1.0", tenure["notes"] or "")

        self._toggle_release()

        btns = ttk.Frame(frm)
        btns.grid(row=9, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(master)
        self.wait_visibility()
        self.grab_set()
        self.focus_set()

    def _toggle_release(self):
        if self.var_ongoing.get():
            self.ent_release.configure(state="disabled")
            try:
                self.btn_release_cal.configure(state="disabled")
            except Exception:
                pass
            self.var_release.set("")
        else:
            self.ent_release.configure(state="normal")
            try:
                self.btn_release_cal.configure(state="normal")
            except Exception:
                pass

    def _save(self):
        emp_disp = self.var_emp.get().strip()
        emp_id = self.emp_map.get(emp_disp)
        if not emp_id:
            messagebox.showerror("Invalid input", "Please select an employee.", parent=self)
            return
        join_date = self.var_join.get().strip()
        release_date = None if self.var_ongoing.get() else self.var_release.get().strip() or None
        tenure_type = self.var_type.get().strip() or "Government"
        project = " ".join(self.var_project.get().split()) or None
        organization = " ".join(self.var_org.get().split()) or None
        role = self.var_role.get().strip()
        notes = self.txt_notes.get("1.0", "end").strip()
        try:
            if self.tenure:
                db.update_tenure(self.tenure["id"], emp_id, join_date, release_date,
                                 tenure_type=tenure_type, project=project,
                                 organization=organization, role=role, notes=notes)
            else:
                db.add_tenure(emp_id, join_date, release_date,
                              tenure_type=tenure_type, project=project,
                              organization=organization, role=role, notes=notes)
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        self.saved = True
        self.destroy()


class App(tk.Tk):
    def __init__(self, current_user=None):
        super().__init__()
        self.current_user = current_user  # sqlite Row from app_users
        self.title("Employee Information & Abroad Visit Tracker - IEDCR")
        try:
            self.state("zoomed")
        except tk.TclError:
            self.geometry("1200x800")
        self.minsize(1000, 680)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        
        style.configure(".", font=("Segoe UI", 11))
        style.configure("Heading.TLabel", font=("Segoe UI", 15, "bold"), foreground="#006633")
        style.configure("SubHeading.TLabel", font=("Segoe UI", 12), foreground="#444444")
        style.configure("Treeview", rowheight=32, font=("Segoe UI", 11))
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"))
        style.configure("TNotebook.Tab", font=("Segoe UI", 11, "bold"), padding=[14, 10])
        style.configure("TButton", font=("Segoe UI", 11), padding=6)
        style.configure("TEntry", font=("Segoe UI", 11))
        style.configure("TCombobox", font=("Segoe UI", 11))

        db.init_db()
        # Init Turso sync (background, on each update when network available)
        if _HAS_TURSO and turso_sync is not None:
            try:
                turso_sync.init_turso()
            except Exception:
                pass
        # Ensure current_user is fresh from DB (stay-logged-in path)
        if self.current_user is not None:
            try:
                fresh = db.get_user_by_id(self.current_user["id"])
                if fresh:
                    self.current_user = fresh
            except Exception:
                pass

        # Top Header Frame (IEDCR Logo & Title)
        header_frame = ttk.Frame(self, padding=12)
        header_frame.pack(fill="x", side="top")

        # Try logo.png first (better for Tk), fallback to logo.svg if PNG missing
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        _logo_svg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.svg")
        _logo_found = None
        if os.path.isfile(_logo_path):
            _logo_found = _logo_path
        elif os.path.isfile(_logo_svg):
            _logo_found = _logo_svg
        if _logo_found:
            try:
                # Use thumbnail to preserve aspect ratio (logo is portrait 783x954)
                pil_img = Image.open(_logo_found)
                # For SVG, PIL may not support - try cairosvg fallback
                if pil_img.format == "SVG" or _logo_found.lower().endswith(".svg"):
                    raise ValueError("SVG not directly supported by PIL")
                pil_img.thumbnail((56, 56), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(pil_img)
                logo_lbl = ttk.Label(header_frame, image=self.logo_img)
                logo_lbl.pack(side="left", padx=(0, 14))
            except Exception:
                # Try SVG via cairosvg -> PNG in memory
                try:
                    import cairosvg
                    import io
                    png_data = cairosvg.svg2png(url=_logo_found, scale=2)
                    from PIL import Image as _PIL
                    pil_img = _PIL.open(io.BytesIO(png_data))
                    pil_img.thumbnail((56, 56), _PIL.Image.Resampling.LANCZOS)
                    self.logo_img = ImageTk.PhotoImage(pil_img)
                    logo_lbl = ttk.Label(header_frame, image=self.logo_img)
                    logo_lbl.pack(side="left", padx=(0, 14))
                except Exception:
                    pass

        title_wrap = ttk.Frame(header_frame)
        title_wrap.pack(side="left", fill="x", expand=True)
        ttk.Label(title_wrap, text="Institute of Epidemiology, Disease Control and Research (IEDCR)", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(title_wrap, text="Employee Information & Abroad Visit Tracker System", style="SubHeading.TLabel").pack(anchor="w")

        # Right side: user info + actions
        if self.current_user is not None:
            user_box = ttk.Frame(header_frame)
            user_box.pack(side="right", padx=(10,0), anchor="e")
            uname = self.current_user["name"]
            urole = self.current_user["role"]
            uphone = self.current_user["phone"]
            ttk.Label(user_box, text=f"{uname} ({urole})", font=("Segoe UI", 10, "bold"), foreground="#006633").pack(anchor="e")
            ttk.Label(user_box, text=uphone, font=("Segoe UI", 9), foreground="#555").pack(anchor="e")
            btn_box = ttk.Frame(user_box)
            btn_box.pack(anchor="e", pady=(4,0))
            if urole == "admin":
                ttk.Button(btn_box, text="Admin Setup", width=12, command=self.open_admin_setup).pack(side="left", padx=2)
                ttk.Button(btn_box, text="Manage Users", width=13, command=self.open_manage_users).pack(side="left", padx=2)
                if _HAS_TURSO:
                    ttk.Button(btn_box, text="Turso Sync", width=12, command=self.open_turso_setup).pack(side="left", padx=2)
            else:
                if _HAS_TURSO:
                    # non-admin can view sync status (read-only)
                    ttk.Button(btn_box, text="Sync Status", width=12, command=self.open_turso_setup).pack(side="left", padx=2)
            ttk.Button(btn_box, text="Logout", width=10, command=self.logout).pack(side="left", padx=2)
        else:
            # No user (should not happen after auth gate) - show login button
            user_box = ttk.Frame(header_frame)
            user_box.pack(side="right")
            ttk.Button(user_box, text="Login", command=self.logout).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(0, 4))

        # Bottom Footer Frame
        footer_frame = ttk.Frame(self, padding=8)
        footer_frame.pack(fill="x", side="bottom")
        ttk.Separator(footer_frame, orient="horizontal").pack(fill="x", pady=(0, 4))
        ttk.Label(
            footer_frame,
            text="Courtesy: This software has been developed by Golam Mostofa, Computer Programmer, Chittagong University",
            font=("Segoe UI", 10, "italic"),
            foreground="#555555",
            anchor="center"
        ).pack(fill="x")

        # Status Bar + Turso sync indicator (non-blocking, with busy progress)
        self.status_var = tk.StringVar(value="Ready.")
        self.turso_status_var = tk.StringVar(value="")
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", side="bottom")
        ttk.Label(status_frame, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4), font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True)
        if _HAS_TURSO:
            # container for turso label + busy progress
            turso_box = ttk.Frame(status_frame, relief="sunken", padding=(2,2))
            turso_box.pack(side="right", fill="y")
            self.lbl_turso = ttk.Label(turso_box, textvariable=self.turso_status_var, anchor="w", padding=(6, 4), font=("Segoe UI", 9), foreground="#006633", width=32)
            self.lbl_turso.pack(side="left", fill="y")
            self.lbl_turso.bind("<Button-1>", lambda e: self.open_turso_setup())
            self.turso_progress = ttk.Progressbar(turso_box, mode="indeterminate", length=90)
            # hidden initially; packed when syncing
            self._turso_progress_visible = False
            # register callback so bg thread can trigger UI update via after
            try:
                turso_sync.set_status_callback(lambda st: self.after(0, self._refresh_turso_status))
            except Exception:
                pass
            self._refresh_turso_status()
            self.after(4000, self._poll_turso_status)

        # Main Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=8)
        self.tab_emp = ttk.Frame(self.nb, padding=12)
        self.tab_projects = ttk.Frame(self.nb, padding=12)
        self.tab_orgs = ttk.Frame(self.nb, padding=12)
        self.tab_tenure = ttk.Frame(self.nb, padding=12)
        self.tab_new = ttk.Frame(self.nb, padding=12)
        self.tab_visits = ttk.Frame(self.nb, padding=12)
        self.tab_summary = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_emp, text=" Employees ")
        self.nb.add(self.tab_projects, text=" Projects ")
        self.nb.add(self.tab_orgs, text=" Collaborator Orgs ")
        self.nb.add(self.tab_tenure, text=" Tenure History ")
        self.nb.add(self.tab_new, text=" New Visit ")
        self.nb.add(self.tab_visits, text=" Visit Records ")
        self.nb.add(self.tab_summary, text=" Yearly Summary ")

        self._build_employees_tab()
        self._build_projects_tab()
        self._build_organizations_tab()
        self._build_tenure_tab()
        self._build_new_visit_tab()
        self._build_visits_tab()
        self._build_summary_tab()

        self.refresh_all()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh_all())

    def set_status(self, msg):
        self.status_var.set(msg)

    def _build_employees_tab(self):
        tab = self.tab_emp
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.search_var, width=32)
        ent.pack(side="left", padx=(4, 12))
        self.search_var.trace_add("write", lambda *_: self.refresh_employees())
        ttk.Button(top, text="Add Employee", command=self.add_employee).pack(side="left", padx=3)
        ttk.Button(top, text="Edit Selected", command=self.edit_employee).pack(side="left", padx=3)
        ttk.Button(top, text="Delete Selected", command=self.delete_employee).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh", command=lambda: self.refresh_employees()).pack(side="left", padx=3)

        cols = [
            ("emp_id", "ID", 80, "w"),
            ("name", "Name", 150, "w"),
            ("designation", "Designation", 120, "w"),
            ("emp_type", "Type", 100, "w"),
            ("project_name", "Project", 130, "w"),
            ("phone_primary", "Phone (Primary)", 110, "w"),
            ("phone_secondary", "Phone (Secondary)", 110, "w"),
            ("email", "Email", 160, "w"),
            ("max_visits", "Yearly Limit", 90, "center"),
        ]
        frame, self.tree_emp = _make_tree(tab, cols)
        frame.grid(row=1, column=0, sticky="nsew")
        self.tree_emp.bind("<Double-1>", lambda e: self.edit_employee())

    def _build_new_visit_tab(self):
        tab = self.tab_new
        tab.columnconfigure(0, weight=0)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(tab, text="Record a new abroad visit", padding=6)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))

        self.vform = VisitForm(left)
        self.vform.pack(fill="x")
        self.vform.on_change = self.update_usage_panel

        save_wrap = ttk.Frame(left)
        save_wrap.pack(fill="x", pady=(8, 2))
        ttk.Button(save_wrap, text="Save Visit", command=self.save_new_visit).pack(side="right")
        ttk.Label(save_wrap, text="Entry is blocked automatically when the yearly limit is reached.").pack(side="left")

        right = ttk.LabelFrame(tab, text="Limit status for the selected employee and year", padding=8)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self.usage_var = tk.StringVar(value="Select an employee to see their yearly limit status.")
        ttk.Label(right, textvariable=self.usage_var, wraplength=430, justify="left",
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        cols = [
            ("date", "Date", 90, "center"),
            ("country", "Country", 130, "w"),
            ("title", "Purpose Title", 210, "w"),
        ]
        frame, self.tree_usage = _make_tree(right, cols)
        frame.grid(row=1, column=0, sticky="nsew")

    def _build_visits_tab(self):
        tab = self.tab_visits
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Year:").pack(side="left")
        self.visits_year_var = tk.StringVar(value="All years")
        self.cmb_visits_year = ttk.Combobox(top, textvariable=self.visits_year_var, state="readonly", width=10)
        self.cmb_visits_year.pack(side="left", padx=(4, 12))
        ttk.Label(top, text="Employee:").pack(side="left")
        self.visits_emp_var = tk.StringVar(value="All employees")
        self.cmb_visits_emp = ttk.Combobox(top, textvariable=self.visits_emp_var, state="readonly", width=38)
        self.cmb_visits_emp.pack(side="left", padx=(4, 12))
        ttk.Button(top, text="Refresh", command=self.refresh_visits).pack(side="left", padx=3)
        ttk.Button(top, text="Edit Selected", command=self.edit_visit).pack(side="left", padx=3)
        ttk.Button(top, text="Delete Selected", command=self.delete_visit).pack(side="left", padx=3)

        cols = [
            ("id", "#", 45, "center"),
            ("date", "Date", 90, "center"),
            ("emp_id", "Emp ID", 70, "w"),
            ("emp_name", "Employee", 130, "w"),
            ("project_name", "Project", 120, "w"),
            ("country", "Country", 105, "w"),
            ("purpose_title", "Purpose Title", 160, "w"),
            ("purpose_detail", "Purpose Details", 210, "w"),
        ]
        frame, self.tree_visits = _make_tree(tab, cols)
        frame.grid(row=1, column=0, sticky="nsew")
        self.tree_visits.bind("<Double-1>", lambda e: self.edit_visit())

    def _build_summary_tab(self):
        tab = self.tab_summary
        tab.rowconfigure(2, weight=1)
        tab.columnconfigure(0, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Year:").pack(side="left")
        current_year = str(datetime.date.today().year)
        self.summary_year_var = tk.StringVar(value=current_year)
        self.cmb_summary_year = ttk.Combobox(top, textvariable=self.summary_year_var, state="readonly", width=10)
        self.cmb_summary_year.pack(side="left", padx=(4, 12))
        self.cmb_summary_year.bind("<<ComboboxSelected>>", lambda e: self.refresh_summary())
        ttk.Button(top, text="Refresh", command=self.refresh_summary).pack(side="left", padx=3)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="Report:").pack(side="left")
        ttk.Button(top, text="Export PDF", command=self.export_report_pdf).pack(side="left", padx=3)
        ttk.Button(top, text="Export Excel", command=self.export_report_excel).pack(side="left", padx=3)
        ttk.Button(top, text="Export CSV", command=self.export_report_csv).pack(side="left", padx=3)
        ttk.Button(top, text="Print Report", command=self.print_report).pack(side="left", padx=3)

        self.summary_totals_var = tk.StringVar(value="")
        ttk.Label(tab, textvariable=self.summary_totals_var, font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=(0, 6))

        cols = [
            ("emp_id", "ID", 75, "w"),
            ("name", "Name", 130, "w"),
            ("designation", "Designation", 105, "w"),
            ("emp_type", "Type", 90, "w"),
            ("project_name", "Project", 115, "w"),
            ("limit", "Yearly Limit", 80, "center"),
            ("used", f"Visits ({current_year})", 90, "center"),
            ("remaining", "Remaining", 80, "center"),
            ("status", "Status", 150, "w"),
            ("reached", "Times Max Reached", 120, "center"),
        ]
        frame, self.tree_summary = _make_tree(tab, cols)
        frame.grid(row=2, column=0, sticky="nsew")

    def _build_tenure_tab(self):
        tab = self.tab_tenure
        tab.rowconfigure(1, weight=2)
        tab.rowconfigure(3, weight=1)
        tab.columnconfigure(0, weight=1)

        # Top filter / actions
        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Employee:").pack(side="left")
        self.tenure_emp_var = tk.StringVar(value="All employees")
        self.cmb_tenure_emp = ttk.Combobox(top, textvariable=self.tenure_emp_var, state="readonly", width=28)
        self.cmb_tenure_emp.pack(side="left", padx=(4, 10))
        self.cmb_tenure_emp.bind("<<ComboboxSelected>>", lambda e: self.refresh_tenures())
        ttk.Label(top, text="Type:").pack(side="left")
        self.tenure_type_var = tk.StringVar(value="All")
        self.cmb_tenure_type = ttk.Combobox(top, textvariable=self.tenure_type_var, state="readonly", width=12,
                                            values=["All"] + list(db.TENURE_TYPES))
        self.cmb_tenure_type.pack(side="left", padx=(4, 10))
        self.cmb_tenure_type.bind("<<ComboboxSelected>>", lambda e: self.refresh_tenures())
        ttk.Button(top, text="Add Tenure", command=self.add_tenure).pack(side="left", padx=3)
        ttk.Button(top, text="Edit Selected", command=self.edit_tenure).pack(side="left", padx=3)
        ttk.Button(top, text="Delete Selected", command=self.delete_tenure).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh", command=self.refresh_tenure_tab).pack(side="left", padx=3)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="Report:").pack(side="left")
        ttk.Button(top, text="Export PDF", command=self.export_tenure_pdf).pack(side="left", padx=3)
        ttk.Button(top, text="Export Excel", command=self.export_tenure_excel).pack(side="left", padx=3)
        ttk.Button(top, text="Export CSV", command=self.export_tenure_csv).pack(side="left", padx=3)
        ttk.Button(top, text="Print", command=self.print_tenure).pack(side="left", padx=3)

        # Tenure details tree (middle)
        cols = [
            ("id", "#", 45, "center"),
            ("emp_id", "Emp ID", 75, "w"),
            ("emp_name", "Employee", 140, "w"),
            ("join", "Join Date", 95, "center"),
            ("release", "Release Date", 105, "center"),
            ("type", "Type", 95, "w"),
            ("project", "Project", 115, "w"),
            ("org", "Collaborator Org", 135, "w"),
            ("role", "Role", 125, "w"),
            ("duration", "Duration", 90, "center"),
        ]
        frame, self.tree_tenure = _make_tree(tab, cols)
        frame.grid(row=1, column=0, sticky="nsew", pady=(0, 6))
        self.tree_tenure.bind("<Double-1>", lambda e: self.edit_tenure())
        self.tree_tenure.bind("<<TreeviewSelect>>", lambda e: self._on_tenure_select())

        # Summary section label
        ttk.Separator(tab, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=4)
        summary_label = ttk.Label(tab, text="Summary: Total time each employee has spent at IEDCR (click a tenure row to see details below)", font=("Segoe UI", 10, "bold"))
        summary_label.grid(row=2, column=0, sticky="w")

        # Summary tree (bottom) + detail frame side by side
        bottom = ttk.Frame(tab)
        bottom.grid(row=3, column=0, sticky="nsew")
        bottom.columnconfigure(0, weight=1)
        bottom.columnconfigure(1, weight=1)
        bottom.rowconfigure(0, weight=1)

        # Left: summary per employee
        left = ttk.LabelFrame(bottom, text="Total Interval Summary (per employee)", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        scols = [
            ("emp_id", "ID", 70, "w"),
            ("name", "Name", 135, "w"),
            ("intervals", "# Periods", 80, "center"),
            ("total", "Total Time", 110, "center"),
            ("first_join", "First Join", 95, "center"),
            ("last_release", "Last Release", 105, "center"),
            ("status", "Current Status", 115, "w"),
        ]
        frame_s, self.tree_tenure_summary = _make_tree(left, scols)
        frame_s.grid(row=0, column=0, sticky="nsew")
        self.tree_tenure_summary.bind("<<TreeviewSelect>>", lambda e: self._on_summary_select())
        self.tree_tenure_summary.bind("<Double-1>", lambda e: self._on_summary_select())

        # Right: detail for selected employee
        right = ttk.LabelFrame(bottom, text="Details: intervals for selected employee", padding=6)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.tenure_detail_var = tk.StringVar(value="Select an employee in the summary to see all join/release periods.")
        ttk.Label(right, textvariable=self.tenure_detail_var, wraplength=420, justify="left", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        dcols = [
            ("join", "Join", 95, "center"),
            ("release", "Release", 105, "center"),
            ("type", "Type", 85, "w"),
            ("project", "Project", 110, "w"),
            ("role", "Role", 120, "w"),
            ("duration", "Duration", 85, "center"),
        ]
        frame_d, self.tree_tenure_detail = _make_tree(right, dcols)
        frame_d.grid(row=1, column=0, sticky="nsew")

    def refresh_all(self):
        self.refresh_employees()
        self.refresh_projects()
        self.refresh_organizations()
        self.refresh_tenure_filters()
        self.refresh_tenures()
        self.refresh_tenure_summary()
        self.refresh_visits_filters()
        self.refresh_visits()
        self.refresh_summary_years()
        self.refresh_summary()

    def refresh_employees(self):
        rows = db.get_employees(self.search_var.get())
        self.tree_emp.delete(*self.tree_emp.get_children())
        for r in rows:
            limit_txt = "Unlimited" if r["max_visits"] == 0 else str(r["max_visits"])
            self.tree_emp.insert("", "end", iid=r["emp_id"], values=(
                r["emp_id"], r["name"], r["designation"], r["emp_type"],
                r["project_name"] or "", r["phone_primary"], r["phone_secondary"],
                r["email"], limit_txt,
            ))
        self.vform.set_employees(rows)
        self.update_usage_panel()

    def _build_projects_tab(self):
        tab = self.tab_projects
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Project name:").pack(side="left")
        self.var_new_project = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.var_new_project, width=34)
        ent.pack(side="left", padx=(4, 8))
        ent.bind("<Return>", lambda e: self.add_project())
        ttk.Button(top, text="Add Project", command=self.add_project).pack(side="left", padx=3)
        ttk.Button(top, text="Delete Selected", command=self.delete_project).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh", command=self.refresh_projects).pack(side="left", padx=3)
        ttk.Label(top, text="(Same name is merged into one project automatically.)",
                  foreground="#666666").pack(side="left", padx=10)

        cols = [
            ("pid", "ID", 60, "center"),
            ("name", "Project Name", 320, "w"),
            ("used", "Employees Assigned", 150, "center"),
        ]
        frame, self.tree_projects = _make_tree(tab, cols)
        frame.grid(row=1, column=0, sticky="nsew")

    def _build_organizations_tab(self):
        tab = self.tab_orgs
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="Organization name:").pack(side="left")
        self.var_new_org = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.var_new_org, width=34)
        ent.pack(side="left", padx=(4, 8))
        ent.bind("<Return>", lambda e: self.add_organization())
        ttk.Button(top, text="Add Organization", command=self.add_organization).pack(side="left", padx=3)
        ttk.Button(top, text="Delete Selected", command=self.delete_organization).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh", command=self.refresh_organizations).pack(side="left", padx=3)
        ttk.Label(top, text="(Same name merged automatically - also auto-created from Tenure History)",
                  foreground="#666666").pack(side="left", padx=10)

        cols = [
            ("oid", "ID", 60, "center"),
            ("name", "Organization Name", 340, "w"),
            ("tenures", "Tenures Linked", 130, "center"),
            ("assigns", "Assignments Linked", 150, "center"),
        ]
        frame, self.tree_orgs = _make_tree(tab, cols)
        frame.grid(row=1, column=0, sticky="nsew")
        # double-click to rename? reuse add
        self.tree_orgs.bind("<Double-1>", lambda e: self._edit_organization_dialog())

    def refresh_organizations(self):
        rows = db.list_organizations()
        self.tree_orgs.delete(*self.tree_orgs.get_children())
        for o in rows:
            self.tree_orgs.insert("", "end", iid=str(o["id"]), values=(
                o["id"], o["name"], o["tenures_used"], o["assignments_used"],
            ))

    def add_organization(self):
        name = " ".join(self.var_new_org.get().split())
        if not name:
            messagebox.showinfo("Empty name", "Enter an organization name first.", parent=self)
            return
        existing = {o["name"].lower() for o in db.list_organizations()}
        oid = db.ensure_organization(name)
        self.var_new_org.set("")
        if name.lower() in existing:
            self.set_status(f'Organization "{name}" already exists - existing entry reused.')
        else:
            self.set_status(f'Organization "{name}" added.')
        self.refresh_all()
        if oid is not None:
            try:
                self.tree_orgs.selection_set(str(oid))
                self.nb.select(self.tab_orgs)
            except Exception:
                pass

    def delete_organization(self):
        sel = self.tree_orgs.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select an organization first.", parent=self)
            return
        oid = int(sel[0])
        row = next((o for o in db.list_organizations() if o["id"] == oid), None)
        label = row["name"] if row else f"#{oid}"
        if messagebox.askyesno(
            "Confirm delete",
            f'Delete collaborator organization "{label}"?\nLinked tenure/assignment records will keep but lose the link.',
            parent=self,
        ):
            db.delete_organization(oid)
            self.set_status("Organization deleted.")
            self.refresh_all()

    def _edit_organization_dialog(self):
        sel = self.tree_orgs.selection()
        if not sel:
            return
        oid = int(sel[0])
        row = next((o for o in db.list_organizations() if o["id"] == oid), None)
        if not row:
            return
        new_name = tk.simpledialog.askstring("Rename Organization", f"New name for \"{row['name']}\":", parent=self)
        if new_name is None:
            return
        new_name = " ".join(new_name.split())
        if not new_name:
            messagebox.showinfo("Empty name", "Name cannot be empty.", parent=self)
            return
        if new_name.lower() == row["name"].lower():
            return
        # check duplicate
        if any(o["name"].lower() == new_name.lower() for o in db.list_organizations()):
            messagebox.showerror("Duplicate", f'An organization named \"{new_name}\" already exists.', parent=self)
            return
        # simple direct SQL rename (no helper yet)
        import sqlite3
        from contextlib import closing
        with closing(db._connect()) as conn, conn:
            conn.execute("UPDATE organizations SET name = ? WHERE id = ?", (new_name, oid))
        self.set_status(f'Organization renamed to \"{new_name}\".')
        self.refresh_all()

    def refresh_projects(self):
        rows = db.list_projects()
        self.tree_projects.delete(*self.tree_projects.get_children())
        for p in rows:
            self.tree_projects.insert("", "end", iid=str(p["id"]), values=(
                p["id"], p["name"], p["employees_used"],
            ))

    def add_project(self):
        name = " ".join(self.var_new_project.get().split())
        if not name:
            messagebox.showinfo("Empty name", "Enter a project name first.", parent=self)
            return
        existing = {p["name"].lower() for p in db.list_projects()}
        pid = db.ensure_project(name)
        self.var_new_project.set("")
        if name.lower() in existing:
            self.set_status(f'Project "{name}" already exists - existing entry reused.')
        else:
            self.set_status(f'Project "{name}" added.')
        self.refresh_all()
        if pid is not None:
            self.tree_projects.selection_set(str(pid))

    def delete_project(self):
        sel = self.tree_projects.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a project first.", parent=self)
            return
        pid = int(sel[0])
        row = next((p for p in db.list_projects() if p["id"] == pid), None)
        label = row["name"] if row else f"#{pid}"
        if messagebox.askyesno(
            "Confirm delete",
            f'Delete project "{label}"?\nAssigned employees will simply have no project.',
            parent=self,
        ):
            db.delete_project(pid)
            self.set_status(f"Project deleted.")
            self.refresh_all()

    # ── Tenure History ───────────────────────────────────────
    def refresh_tenure_filters(self):
        prev = self.tenure_emp_var.get()
        choices = ["All employees"] + [f"{r['emp_id']} - {r['name']}" for r in db.get_employees()]
        self.cmb_tenure_emp["values"] = choices
        if prev not in choices:
            self.tenure_emp_var.set("All employees")

    def refresh_tenures(self):
        emp_disp = self.tenure_emp_var.get()
        emp_id = None
        if emp_disp and not emp_disp.startswith("All"):
            emp_id = emp_disp.split(" - ")[0].strip()
        ttype = self.tenure_type_var.get()
        rows = db.list_tenures(emp_id=emp_id, tenure_type=None if ttype == "All" else ttype)
        self.tree_tenure.delete(*self.tree_tenure.get_children())
        today = datetime.date.today().isoformat()
        for r in rows:
            end = r["release_date"] or today
            try:
                days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(r["join_date"])).days + 1
                dur = db._format_duration(days) if hasattr(db, "_format_duration") else f"{days}d"
            except Exception:
                dur = ""
            release_disp = r["release_date"] or "Ongoing"
            self.tree_tenure.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["emp_id"], r["emp_name"], r["join_date"], release_disp,
                r["tenure_type"], r["project_name"] or "", r["organization_name"] or "",
                r["role"] or "", dur,
            ))

    def refresh_tenure_summary(self):
        rows = db.tenure_summary_all()
        self.tree_tenure_summary.delete(*self.tree_tenure_summary.get_children())
        for s in rows:
            last_rel = s["last_release"] or "Ongoing"
            status = "Active" if s["is_currently_active"] else "Released"
            first = s["first_join"] or "-"
            total = s["total_formatted"] if s["interval_count"] else "-"
            self.tree_tenure_summary.insert("", "end", iid=s["emp_id"], values=(
                s["emp_id"], s["name"], s["interval_count"], total, first, last_rel, status,
            ))

    def _on_tenure_select(self):
        sel = self.tree_tenure.selection()
        if not sel:
            return
        tid = int(sel[0])
        row = db.get_tenure(tid)
        if not row:
            return
        # Show detail for this employee
        self._show_tenure_detail(row["emp_id"])

    def _on_summary_select(self):
        sel = self.tree_tenure_summary.selection()
        if not sel:
            return
        emp_id = sel[0]
        self._show_tenure_detail(emp_id)
        # also filter top combo to that employee for convenience
        # self.tenure_emp_var.set(next((v for v in self.cmb_tenure_emp["values"] if v.startswith(emp_id)), "All employees"))
        # self.refresh_tenures()

    def _show_tenure_detail(self, emp_id):
        info = db.tenure_summary_for_employee(emp_id)
        if info["interval_count"] == 0:
            self.tenure_detail_var.set(f"{emp_id}: no tenure records yet.")
            self.tree_tenure_detail.delete(*self.tree_tenure_detail.get_children())
            return
        total = info["total_formatted"]
        status = "Active (still working)" if info["is_currently_active"] else f"Released on {info['last_release']}"
        self.tenure_detail_var.set(
            f"{emp_id} - {info['intervals'][0]['emp_name']}: {info['interval_count']} period(s), "
            f"total {total} ({info['total_days']} days), {status}. First join: {info['first_join']}"
        )
        self.tree_tenure_detail.delete(*self.tree_tenure_detail.get_children())
        for iv in sorted(info["intervals"], key=lambda x: x["join_date"]):
            rel = iv["release_date"] or "Ongoing"
            dur = iv.get("duration_formatted", "")
            self.tree_tenure_detail.insert("", "end", values=(
                iv["join_date"], rel, iv["tenure_type"], iv["project_name"] or "", iv["role"] or "", dur,
            ))

    def refresh_tenure_tab(self):
        self.refresh_tenure_filters()
        self.refresh_tenures()
        self.refresh_tenure_summary()

    def add_tenure(self):
        dlg = TenureDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.set_status("Tenure record added.")
            self.refresh_all()

    def edit_tenure(self):
        sel = self.tree_tenure.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a tenure record first.", parent=self)
            return
        rec = db.get_tenure(int(sel[0]))
        if not rec:
            messagebox.showerror("Not found", "This tenure record no longer exists.", parent=self)
            return
        dlg = TenureDialog(self, rec)
        self.wait_window(dlg)
        if dlg.saved:
            self.set_status(f"Tenure #{rec['id']} updated.")
            self.refresh_all()

    def delete_tenure(self):
        sel = self.tree_tenure.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a tenure record first.", parent=self)
            return
        tid = int(sel[0])
        rec = db.get_tenure(tid)
        label = f"{rec['emp_name']} ({rec['emp_id']}) {rec['join_date']} -> {rec['release_date'] or 'Ongoing'}" if rec else f"#{tid}"
        if messagebox.askyesno("Confirm delete", f"Delete tenure record:\n{label}?", parent=self):
            db.delete_tenure(tid)
            self.set_status(f"Tenure #{tid} deleted.")
            self.refresh_all()

    def refresh_visits_filters(self):
        years = ["All years"] + sorted(set(db.years_present()) | {str(datetime.date.today().year)}, reverse=True)
        self.cmb_visits_year["values"] = years
        if self.visits_year_var.get() not in years:
            self.visits_year_var.set("All years")

        prev = self.visits_emp_var.get()
        choices = ["All employees"] + [f"{r['emp_id']} - {r['name']}" for r in db.get_employees()]
        self.cmb_visits_emp["values"] = choices
        if prev not in choices:
            self.visits_emp_var.set("All employees")

    def refresh_visits(self):
        year = self.visits_year_var.get()
        emp_disp = self.visits_emp_var.get()
        emp_id = None
        if emp_disp and not emp_disp.startswith("All"):
            emp_id = emp_disp.split(" - ")[0].strip()
        rows = db.list_visits(year=None if year == "All years" else year, emp_id=emp_id)
        self.tree_visits.delete(*self.tree_visits.get_children())
        for r in rows:
            self.tree_visits.insert("", "end", iid=str(r["id"]), values=(
                r["id"], r["visit_date"], r["emp_id"], r["emp_name"],
                r["project_name"] or "", r["country"], r["purpose_title"], r["purpose_detail"],
            ))
        self.set_status(f"{len(rows)} visit record(s) shown.")

    def refresh_summary_years(self):
        years = sorted(set(db.years_present()) | {str(datetime.date.today().year)}, reverse=True)
        self.cmb_summary_year["values"] = years
        if self.summary_year_var.get() not in years:
            self.summary_year_var.set(str(datetime.date.today().year))

    def refresh_summary(self):
        year = self.summary_year_var.get() or str(datetime.date.today().year)
        rows = db.summary(year)
        self.tree_summary.heading("used", text=f"Visits ({year})")
        self.tree_summary.delete(*self.tree_summary.get_children())
        blocked_count = 0
        for r in rows:
            limit = r["max_visits"]
            used = r["used_this_year"]
            if limit == 0:
                remaining, status = "Unlimited", "Unlimited visits"
            elif used >= limit:
                remaining, status = "0", "MAX REACHED - blocked"
                blocked_count += 1
            else:
                remaining, status = str(limit - used), f"{limit - used} visit(s) left"
            self.tree_summary.insert("", "end", iid=r["emp_id"], values=(
                r["emp_id"], r["name"], r["designation"],
                r["emp_type"] or "", r["project_name"] or "",
                "Unlimited" if limit == 0 else limit,
                used, remaining, status, r["times_max_reached"],
            ))
        total = len(rows)
        self.summary_totals_var.set(
            f"{blocked_count} of {total} employee(s) have reached their maximum abroad-visit limit in {year}."
        )

    def update_usage_panel(self):
        emp = self.vform.selected_employee()
        if not emp:
            self.usage_var.set("Select an employee to see their yearly limit status.")
            self.tree_usage.delete(*self.tree_usage.get_children())
            return
        raw_date = self.vform.var_date.get().strip()
        try:
            iso = db.normalize_date(raw_date)
        except ValueError:
            self.usage_var.set("Enter a valid date of visit (YYYY-MM-DD) to check the limit.")
            self.tree_usage.delete(*self.tree_usage.get_children())
            return
        year = iso[:4]
        used = db.yearly_usage(emp["emp_id"], year)
        limit = emp["max_visits"]
        name_disp = f'{emp["name"]} ({emp["emp_id"]})'
        if limit == 0:
            self.usage_var.set(
                f'{name_disp}: UNLIMITED facility.\nVisits recorded in {year}: {used}\nEntry always allowed.'
            )
        elif used >= limit:
            self.usage_var.set(
                f'{name_disp}: MAXIMUM REACHED in {year} ({used}/{limit}).\nFurther entry will be BLOCKED.'
            )
        else:
            self.usage_var.set(
                f'{name_disp}: {used} of {limit} visit(s) used in {year}.\n{limit - used} more allowed this year.'
            )
        self.tree_usage.delete(*self.tree_usage.get_children())
        for v in db.list_visits(year=year, emp_id=emp["emp_id"]):
            self.tree_usage.insert("", "end", values=(v["visit_date"], v["country"], v["purpose_title"]))

    def _selected_emp_id(self):
        sel = self.tree_emp.selection()
        return sel[0] if sel else None

    def add_employee(self):
        dlg = EmployeeDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.set_status("Employee added.")
            self.refresh_all()

    def edit_employee(self):
        emp_id = self._selected_emp_id()
        if not emp_id:
            messagebox.showinfo("No selection", "Select an employee in the table first.", parent=self)
            return
        dlg = EmployeeDialog(self, db.get_employee(emp_id))
        self.wait_window(dlg)
        if dlg.saved:
            self.set_status(f'Employee {emp_id} updated.')
            self.refresh_all()

    def delete_employee(self):
        emp_id = self._selected_emp_id()
        if not emp_id:
            messagebox.showinfo("No selection", "Select an employee in the table first.", parent=self)
            return
        emp = db.get_employee(emp_id)
        if messagebox.askyesno(
            "Confirm delete",
            f'Delete "{emp["name"]}" ({emp_id})?\n\nAll of their visit records will also be deleted.',
            parent=self,
        ):
            db.delete_employee(emp_id)
            self.set_status(f"Employee {emp_id} deleted.")
            self.refresh_all()

    def save_new_visit(self):
        emp = self.vform.selected_employee()
        if not emp:
            messagebox.showwarning("No employee", "Please select an employee first.", parent=self)
            return
        detail = self.vform.txt_detail.get("1.0", "end")
        try:
            db.add_visit(
                emp["emp_id"],
                self.vform.var_country.get(),
                self.vform.var_title.get(),
                detail,
                self.vform.var_date.get(),
            )
        except ValueError as exc:
            title = "Entry blocked" if str(exc).startswith("BLOCKED") else "Cannot add visit"
            messagebox.showerror(title, str(exc), parent=self)
            self.update_usage_panel()
            return
        self.set_status(f'Visit saved for {emp["name"]} on {db.normalize_date(self.vform.var_date.get())}.')
        self.vform.clear(keep_employee=True)
        self.refresh_all()

    def edit_visit(self):
        sel = self.tree_visits.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a visit record first.", parent=self)
            return
        record = db.get_visit(int(sel[0]))
        dlg = VisitDialog(self, record, self)
        self.wait_window(dlg)
        if dlg.saved:
            self.refresh_all()

    def delete_visit(self):
        sel = self.tree_visits.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a visit record first.", parent=self)
            return
        vid = int(sel[0])
        if messagebox.askyesno("Confirm delete", f"Delete visit record #{vid}?", parent=self):
            db.delete_visit(vid)
            self.set_status(f"Visit record #{vid} deleted.")
            self.refresh_all()

    def _current_report_year(self):
        year = self.summary_year_var.get()
        return year if year else str(datetime.date.today().year)

    def _save_report(self, ext, filetype, fn):
        year = self._current_report_year()
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=ext,
            initialfile=f"visit_report_{year}{ext}",
            filetypes=[filetype, ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fn(path, year)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.set_status(f"Report exported: {path}")
        if messagebox.askyesno("Export complete", "Open the exported report now?", parent=self):
            try:
                os.startfile(path)
            except OSError as exc:
                messagebox.showerror("Cannot open file", str(exc), parent=self)

    def export_report_csv(self):
        self._save_report(".csv", ("CSV file", "*.csv"), reporting.export_csv)

    def export_report_excel(self):
        self._save_report(".xlsx", ("Excel workbook", "*.xlsx"), reporting.export_xlsx)

    def export_report_pdf(self):
        self._save_report(".pdf", ("PDF file", "*.pdf"), reporting.export_pdf)

    def print_report(self):
        year = self._current_report_year()
        path = os.path.join(tempfile.gettempdir(), f"visit_report_{year}.pdf")
        try:
            reporting.export_pdf(path, year)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        sent = False
        if hasattr(os, "startfile"):
            try:
                os.startfile(path, "print")
                sent = True
                self.set_status(f"Report for {year} sent to printer (PDF: {path}).")
            except OSError:
                pass
        if not sent:
            try:
                os.startfile(path)
                self.set_status("Opened report PDF - use your PDF viewer's Print option.")
            except OSError as exc:
                messagebox.showerror("Print failed", str(exc), parent=self)

    # ── Tenure Report Exports ───────────────────────────────
    def _save_tenure_report(self, ext, filetype, fn):
        today = datetime.date.today().isoformat()
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=ext,
            initialfile=f"tenure_report_{today}{ext}",
            filetypes=[filetype, ("All files", "*.*")],
        )
        if not path:
            return
        try:
            fn(path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.set_status(f"Tenure report exported: {path}")
        if messagebox.askyesno("Export complete", "Open the exported report now?", parent=self):
            try:
                os.startfile(path)
            except OSError as exc:
                messagebox.showerror("Cannot open file", str(exc), parent=self)

    def export_tenure_csv(self):
        self._save_tenure_report(".csv", ("CSV file", "*.csv"), reporting.export_tenure_csv)

    def export_tenure_excel(self):
        self._save_tenure_report(".xlsx", ("Excel workbook", "*.xlsx"), reporting.export_tenure_xlsx)

    def export_tenure_pdf(self):
        self._save_tenure_report(".pdf", ("PDF file", "*.pdf"), reporting.export_tenure_pdf)

    def print_tenure(self):
        today = datetime.date.today().isoformat()
        path = os.path.join(tempfile.gettempdir(), f"tenure_report_{today}.pdf")
        try:
            reporting.export_tenure_pdf(path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        sent = False
        if hasattr(os, "startfile"):
            try:
                os.startfile(path, "print")
                sent = True
                self.set_status(f"Tenure report sent to printer (PDF: {path}).")
            except OSError:
                pass
        if not sent:
            try:
                os.startfile(path)
                self.set_status("Opened tenure report PDF - use your PDF viewer's Print option.")
            except OSError as exc:
                messagebox.showerror("Print failed", str(exc), parent=self)

    # ── Auth / Admin Setup ────────────────────────────────
    def open_admin_setup(self):
        """Admin Setup - Supabase keys stored in DB config table (required for compiled builds)."""
        if not self.current_user or self.current_user["role"] != "admin":
            messagebox.showerror("Access denied", "Only admin can open Admin Setup.", parent=self)
            return
        dlg = auth.SupabaseConfigDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.set_status("Supabase config saved to DB (Admin Setup). Compiled builds will use it.")
        # also warn if not configured
        try:
            cfg = db.get_supabase_config()
            if not (cfg.get("api_key") or "").strip() or not (cfg.get("secret_key") or "").strip():
                # check env fallback still Warn but don't block
                if not db.is_supabase_configured():
                    self.set_status("Warning: Supabase keys not configured - Forgot Password will fail. Open Admin Setup to fix.")
        except Exception:
            pass

    def open_supabase_config(self):
        # alias for backward compat / quick access
        return self.open_admin_setup()

    def open_turso_setup(self):
        """Turso Sync config - save DB to turso.tech on each update when network available."""
        if not _HAS_TURSO or turso_sync is None:
            messagebox.showerror("Not available", "Turso sync module not found.", parent=self)
            return
        # admin required to change config, but non-admin can view status
        if self.current_user and self.current_user["role"] != "admin":
            # read-only view - still allow dialog but save will be blocked by enabled check? allow open
            # let them see status, but admin change will be warned inside dialog
            pass
        elif not self.current_user or self.current_user["role"] != "admin":
            messagebox.showerror("Access denied", "Only admin can configure Turso Sync.", parent=self)
            return
        dlg = turso_sync.TursoConfigDialog(self)  # type: ignore
        self.wait_window(dlg)
        if getattr(dlg, "saved", False):
            self.set_status("Turso config saved. Sync enabled on network available.")
            self._refresh_turso_status()
            # trigger immediate sync if configured
            try:
                if turso_sync.is_turso_configured():
                    self.set_status("Turso syncing...")
                    turso_sync.schedule_sync(delay=1.0)
            except Exception:
                pass

    def _refresh_turso_status(self):
        if not _HAS_TURSO or turso_sync is None:
            return
        try:
            # quick, non-blocking - never hangs UI (no live socket on UI thread)
            st = turso_sync.get_status(check_network=False)
            syncing = st.get("syncing") == "True"
            log_pending = int(st.get("log_pending","0") or 0)
            # busy progress handling - shows in footer that background sync is active
            try:
                if syncing or st.get("last_status") == "syncing":
                    if not self._turso_progress_visible:
                        self.turso_progress.pack(side="left", padx=(4,0), pady=2)
                        self.turso_progress.start(10)
                        self._turso_progress_visible = True
                    if log_pending > 0:
                        self.turso_status_var.set(f"Turso: syncing... ({log_pending} pending)")
                    else:
                        self.turso_status_var.set("Turso: syncing...")
                    try: self.lbl_turso.configure(foreground="#006633")
                    except Exception: pass
                    return
                else:
                    if self._turso_progress_visible:
                        try:
                            self.turso_progress.stop()
                            self.turso_progress.pack_forget()
                        except Exception:
                            pass
                        self._turso_progress_visible = False
            except Exception:
                pass

            if not st["url"] and not st["configured"] == "True":
                self.turso_status_var.set("Turso: not configured")
                try: self.lbl_turso.configure(foreground="#cc7700")
                except Exception: pass
            elif st["last_status"] == "pending":
                if log_pending > 0:
                    self.turso_status_var.set(f"Turso: pending sync... ({log_pending})")
                else:
                    self.turso_status_var.set("Turso: pending sync...")
                try: self.lbl_turso.configure(foreground="#cc7700")
                except Exception: pass
            elif st["last_status"] == "offline":
                if log_pending > 0:
                    self.turso_status_var.set(f"Turso: offline - queued ({log_pending})")
                else:
                    self.turso_status_var.set("Turso: offline - queued")
                try: self.lbl_turso.configure(foreground="#cc7700")
                except Exception: pass
            elif st["last_status"] == "syncing":
                self.turso_status_var.set("Turso: syncing...")
                try: self.lbl_turso.configure(foreground="#006633")
                except Exception: pass
            elif st["last_status"] == "ok":
                when = st["last_sync"][:16] if st["last_sync"] else "now"
                # show failed retry info if any
                if st.get("last_error"):
                    err = st["last_error"][:20].replace("\n"," ").strip()
                    self.turso_status_var.set(f"Turso: synced {when} (next retry if failed)")
                else:
                    self.turso_status_var.set(f"Turso: synced {when}")
                try: self.lbl_turso.configure(foreground="#006633")
                except Exception: pass
            elif st["last_status"] == "error":
                err = st["last_error"][:26] if st["last_error"] else "error"
                err = err.replace("\n"," ").strip()
                # standard retry interval 60s
                self.turso_status_var.set(f"Turso: error - {err} (retry 60s)")
                try: self.lbl_turso.configure(foreground="#cc0000")
                except Exception: pass
            else:
                net = st.get("network","unknown")
                self.turso_status_var.set(f"Turso: {net}")
                try: self.lbl_turso.configure(foreground="#555")
                except Exception: pass
        except Exception:
            pass

    def _poll_turso_status(self):
        try:
            self._refresh_turso_status()
        except Exception:
            pass
        try:
            self.after(4000, self._poll_turso_status)
        except Exception:
            pass

    def open_manage_users(self):
        if not self.current_user or self.current_user["role"] != "admin":
            messagebox.showerror("Access denied", "Only admin can manage users.", parent=self)
            return
        dlg = auth.ManageUsersDialog(self)
        self.wait_window(dlg)
        # refresh status if user self-deleted? just keep
        try:
            fresh = db.get_user_by_id(self.current_user["id"])
            if not fresh:
                # current admin was deleted -> force logout
                self._do_logout(clear_stay=True)
                return
            self.current_user = fresh
        except Exception:
            pass

    def logout(self):
        if messagebox.askyesno("Logout", "Log out and return to login screen?", parent=self):
            self._do_logout(clear_stay=False)

    def _do_logout(self, clear_stay=False):
        try:
            if self.current_user:
                db.set_user_logged_in(self.current_user["id"], False)
            if clear_stay:
                db.clear_stay_logged_in()
            else:
                # only clear stay if user unchecked stay? keep stay flag but clear logged_in
                # Actually logout should clear stay_logged_in to require re-login unless user explicitly wants stay.
                # We respect: if logout clicked, clear stay so next launch shows login.
                db.clear_stay_logged_in()
                db.logout_all()
        except Exception:
            pass
        self.destroy()
        # Re-launch login flow
        _run_app_with_auth()

    def _on_close(self):
        try:
            if _HAS_TURSO and turso_sync is not None:
                try:
                    turso_sync.stop_turso()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self.current_user:
                db.set_user_logged_in(self.current_user["id"], False)
                # Do NOT clear stay_logged_in on window close - that is the "stay logged in" feature.
                # Only clear stay if stay flag is 0.
                if db.get_app_state("stay_logged_in") != "1":
                    db.clear_stay_logged_in()
                    db.logout_all()
        except Exception:
            pass
        self.destroy()


def _run_app_with_auth():
    """Handle first-admin + login with stay-logged-in before creating App."""
    import tkinter as tk
    db.init_db()

    # Fast path: stay logged in
    stay = db.get_stay_logged_in_user()
    if stay:
        try:
            db.set_user_logged_in(stay["id"], True)
        except Exception:
            pass
        app = App(current_user=stay)
        app.protocol("WM_DELETE_WINDOW", app._on_close)
        app.mainloop()
        return

    # hidden root for dialogs - keep it withdrawn but let wait_window pump events
    hidden = tk.Tk()
    hidden.withdraw()
    try:
        hidden.update_idletasks()
    except Exception:
        pass

    user = auth.do_login_flow(hidden)
    try:
        hidden.destroy()
    except Exception:
        pass

    if not user:
        # user exited login
        return

    app = App(current_user=user)
    # override close to handle logged_out in db
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    app.mainloop()


if __name__ == "__main__":
    _run_app_with_auth()
