import datetime
import os
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk

import db
import reporting
from countries import COUNTRIES

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
        ttk.Entry(date_wrap, textvariable=self.var_date, width=13).pack(side="left")
        ttk.Label(date_wrap, text=f"  ({DATE_HINT})").pack(side="left")

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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
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

        # Top Header Frame (IEDCR Logo & Title)
        header_frame = ttk.Frame(self, padding=12)
        header_frame.pack(fill="x", side="top")

        if os.path.exists("logo.png"):
            try:
                pil_img = Image.open("logo.png").resize((52, 52), Image.Resampling.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(pil_img)
                logo_lbl = ttk.Label(header_frame, image=self.logo_img)
                logo_lbl.pack(side="left", padx=(0, 14))
            except Exception:
                pass

        title_wrap = ttk.Frame(header_frame)
        title_wrap.pack(side="left", fill="x", expand=True)
        ttk.Label(title_wrap, text="Institute of Epidemiology, Disease Control and Research (IEDCR)", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(title_wrap, text="Employee Information & Abroad Visit Tracker System", style="SubHeading.TLabel").pack(anchor="w")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(0, 4))

        # Bottom Footer Frame
        footer_frame = ttk.Frame(self, padding=8)
        footer_frame.pack(fill="x", side="bottom")
        ttk.Separator(footer_frame, orient="horizontal").pack(fill="x", pady=(0, 4))
        ttk.Label(
            footer_frame,
            text="This software is developed by Golam Mostofa, Computer Programmer, Chittagong University",
            font=("Segoe UI", 10, "italic"),
            foreground="#555555",
            anchor="center"
        ).pack(fill="x")

        # Status Bar
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4), font=("Segoe UI", 10)).pack(fill="x", side="bottom")

        # Main Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=8)
        self.tab_emp = ttk.Frame(self.nb, padding=12)
        self.tab_projects = ttk.Frame(self.nb, padding=12)
        self.tab_new = ttk.Frame(self.nb, padding=12)
        self.tab_visits = ttk.Frame(self.nb, padding=12)
        self.tab_summary = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_emp, text=" Employees ")
        self.nb.add(self.tab_projects, text=" Projects ")
        self.nb.add(self.tab_new, text=" New Visit ")
        self.nb.add(self.tab_visits, text=" Visit Records ")
        self.nb.add(self.tab_summary, text=" Yearly Summary ")

        self._build_employees_tab()
        self._build_projects_tab()
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

    def refresh_all(self):
        self.refresh_employees()
        self.refresh_projects()
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


if __name__ == "__main__":
    app = App()
    app.mainloop()
