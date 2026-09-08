"""Auth UI: login, first-admin creation, forgot password with Supabase sync."""
import json
import os
import secrets
import string
import tkinter as tk
from tkinter import ttk, messagebox

import db

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False
    Image = None
    ImageTk = None


def _load_logo_image(size=(56, 56)):
    """Load logo.png/svg as PhotoImage centered, like main.py. Returns PhotoImage or None."""
    if not _HAS_PIL:
        return None
    base = os.path.dirname(os.path.abspath(__file__))
    cand_png = os.path.join(base, "logo.png")
    cand_svg = os.path.join(base, "logo.svg")
    path = None
    if os.path.isfile(cand_png):
        path = cand_png
    elif os.path.isfile(cand_svg):
        path = cand_svg
    if not path:
        return None
    try:
        pil = Image.open(path)
        if path.lower().endswith(".svg") or getattr(pil, "format", None) == "SVG":
            raise ValueError("SVG")
        pil.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(pil)
    except Exception:
        try:
            import cairosvg
            import io
            png_data = cairosvg.svg2png(url=path, scale=2)
            pil = Image.open(io.BytesIO(png_data))
            pil.thumbnail(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(pil)
        except Exception:
            return None


def _generate_random_password(length=8):
    # generate like 1233434 with characters - ensure at least 2 letters + digits
    alphabet = string.ascii_letters + string.digits
    # ensure mix: at least one letter and one digit
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw):
            return pw

# ── Supabase config ──────────────────────────────────────────
SUPABASE_URL = "https://zjxeigfwmrtnqatlvjqe.supabase.co/rest/v1/password_resets?on_conflict=mobile"

# Load .env if present (optional, no hard dependency)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Support both new env names and legacy ones. Priority: explicit API/SECRET > SUPABASE_KEY
SUPABASE_API_KEY = (
    os.environ.get("SUPABASE_API_KEY", "")
    or os.environ.get("SUPABASE_ANON_KEY", "")
    or os.environ.get("SUPABASE_KEY", "")
    or ""
)
SUPABASE_SECRET_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY", "")
    or os.environ.get("SUPABASE_SERVICE_KEY", "")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    or ""
)
# Legacy single key fallback for backward compat
SUPABASE_KEY = SUPABASE_API_KEY or SUPABASE_SECRET_KEY

def _get_supabase_keys():
    """Return (api_key, secret_key) resolving DB config first, then env.

    DB (app_state / config table) is primary because env vars cannot be
    packed into a compiled build. Env is fallback for dev / legacy.
    """
    api = SUPABASE_API_KEY
    sec = SUPABASE_SECRET_KEY
    # DB config is primary source (required for compiled builds)
    try:
        # Use helper if available (new), else direct app_state
        if hasattr(db, "get_supabase_config"):
            cfg = db.get_supabase_config()
            db_api = (cfg.get("api_key") or "").strip()
            db_sec = (cfg.get("secret_key") or "").strip()
        else:
            db_api = (db.get_app_state("supabase_api_key") or db.get_app_state("supabase_key") or "").strip()
            db_sec = (db.get_app_state("supabase_secret_key") or "").strip()
        if db_api:
            api = db_api
        if db_sec:
            sec = db_sec
    except Exception:
        pass
    # Env is secondary (dev convenience). Only override if DB did not provide value.
    env_api = (os.environ.get("SUPABASE_API_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "") or os.environ.get("SUPABASE_KEY", "") or "").strip()
    env_sec = (os.environ.get("SUPABASE_SECRET_KEY", "") or os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    if not api and env_api:
        api = env_api
    if not sec and env_sec:
        sec = env_sec
    # Also re-read current env if both were empty at import time but set later
    if not api:
        api = env_api
    if not sec:
        sec = env_sec
    # fallback: if only one is set, use it for both headers (matches curl sample where same key used)
    if not sec:
        sec = api
    if not api:
        api = sec
    return api, sec

def _get_supabase_key():
    api, sec = _get_supabase_keys()
    return api or sec

def _get_supabase_url():
    """Resolve Supabase URL: DB config > default constant."""
    try:
        if hasattr(db, "get_supabase_config"):
            cfg = db.get_supabase_config()
            url = (cfg.get("url") or "").strip()
            if url:
                return url
        else:
            url = (db.get_app_state("supabase_url") or "").strip()
            if url:
                return url
    except Exception:
        pass
    return SUPABASE_URL


def _supabase_headers():
    api_key, secret_key = _get_supabase_keys()
    # Supabase expects apikey = anon/api key, Authorization Bearer = anon or service_role
    # If SECRET_KEY not set, reuse API_KEY for both (backward compat)
    bearer = secret_key or api_key
    apikey = api_key or bearer
    return {
        "apikey": apikey,
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }

def supabase_reset_password(mobile, new_password, timeout=12):
    """POST to Supabase password_resets. Returns (ok, status, body)."""
    api_key, secret_key = _get_supabase_keys()
    if not api_key and not secret_key:
        return False, 0, "Supabase keys not configured. Go to Admin Setup -> Supabase Config and save SUPABASE_API_KEY and SUPABASE_SECRET_KEY (or set env vars SUPABASE_API_KEY / SUPABASE_SECRET_KEY)."
    url = _get_supabase_url()
    try:
        import requests
    except ImportError:
        # fallback to urllib
        import urllib.request, urllib.error
        data = json.dumps({"mobile": mobile, "password": new_password}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=_supabase_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                return 200 <= resp.status < 300, resp.status, body
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore") if hasattr(e, 'read') else str(e)
            return False, e.code, body
        except Exception as e:
            return False, 0, str(e)
    else:
        try:
            resp = requests.post(url, json={"mobile": mobile, "password": new_password},
                                 headers=_supabase_headers(), timeout=timeout)
            return resp.ok, resp.status_code, resp.text
        except Exception as e:
            return False, 0, str(e)


# ── Helpers ──────────────────────────────────────────────────

def _toggle_pw(entry, var_show):
    entry.configure(show="" if var_show.get() else "•")


# ── Create First Admin Dialog ────────────────────────────────

class CreateAdminDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.saved = False
        self.created_user = None
        self.title("Create First Admin - Required")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        frm = ttk.Frame(self, padding=20)
        frm.pack(fill="both", expand=True)

        # Logo + header (centered)
        logo_frame = ttk.Frame(frm)
        logo_frame.pack(fill="x", pady=(0,6))
        _logo = _load_logo_image((52, 52))
        if _logo:
            self._logo_img = _logo  # keep ref
            ttk.Label(logo_frame, image=_logo).pack(side="left", padx=(0,10))
        title_box = ttk.Frame(logo_frame)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Welcome! Create the first Admin account", font=("Segoe UI", 13, "bold"), foreground="#006633").pack(anchor="w")
        ttk.Label(title_box, text="No users exist yet. Please create the initial administrator.", foreground="#555").pack(anchor="w")

        header = ttk.Frame(frm)
        header.pack(fill="x")

        self.var_name = tk.StringVar()
        self.var_phone = tk.StringVar()
        self.var_email = tk.StringVar()
        self.var_pw = tk.StringVar()
        self.var_pw2 = tk.StringVar()
        self.var_show = tk.BooleanVar(value=False)

        form = ttk.Frame(frm)
        form.pack(fill="x", pady=(0,4))
        self._pw_entries = []
        rows = [
            ("Full Name *", self.var_name, False),
            ("Mobile *", self.var_phone, False),
            ("Email *", self.var_email, False),
            ("Password * (min 6)", self.var_pw, True),
            ("Confirm Password *", self.var_pw2, True),
        ]
        for i, (lbl, var, is_pw) in enumerate(rows):
            ttk.Label(form, text=lbl).grid(row=i, column=0, sticky="w", padx=(0,10), pady=5)
            ent = ttk.Entry(form, textvariable=var, width=32, show="•" if is_pw else "")
            ent.grid(row=i, column=1, sticky="we", pady=5)
            if is_pw:
                self._pw_entries.append(ent)
            form.columnconfigure(1, weight=1)

        chk = ttk.Checkbutton(form, text="Show password", variable=self.var_show,
                              command=lambda: [e.configure(show="" if self.var_show.get() else "•") for e in self._pw_entries])
        chk.grid(row=len(rows), column=1, sticky="w", pady=(2,8))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10,0))
        ttk.Button(btns, text="Create Admin", command=self._save, style="Accent.TButton").pack(side="right", padx=4)
        # No cancel - mandatory

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self._on_close())

        try:
            if master.winfo_viewable():
                self.transient(master)
        except Exception:
            pass
        self.update_idletasks()
        self.deiconify()
        # Center on screen / parent (was loading at corner)
        try:
            self.update_idletasks()
            w = self.winfo_width()
            h = self.winfo_height()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            if master.winfo_viewable():
                try:
                    mx = master.winfo_rootx()
                    my = master.winfo_rooty()
                    mw = master.winfo_width()
                    mh = master.winfo_height()
                    x = mx + (mw - w)//2
                    y = my + (mh - h)//2
                except Exception:
                    x = (sw - w)//2
                    y = (sh - h)//2 - 40
            else:
                x = (sw - w)//2
                y = (sh - h)//2 - 40
            x = max(10, min(x, sw - w - 10))
            y = max(10, min(y, sh - h - 40))
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass
        try:
            if master.winfo_viewable():
                self.grab_set()
        except Exception:
            pass
        try:
            self.focus_set()
        except Exception:
            pass

    def _on_close(self):
        # Allow close/minimize — first admin is required, so confirm exit
        if self.saved:
            self.destroy()
            return
        if messagebox.askyesno("Exit", "First admin not created. Exit application?", parent=self):
            master = self.master
            try:
                self.destroy()
            except tk.TclError:
                pass
            try:
                if master is not None and master.winfo_exists():
                    master.destroy()
            except Exception:
                pass

    def _save(self):
        name = self.var_name.get().strip()
        phone = self.var_phone.get().strip()
        email = self.var_email.get().strip()
        pw = self.var_pw.get()
        pw2 = self.var_pw2.get()
        if pw != pw2:
            messagebox.showerror("Mismatch", "Passwords do not match.", parent=self)
            return
        try:
            uid = db.create_app_user(name, phone, email, pw, role="admin")
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)
            return
        self.created_user = db.get_user_by_id(uid)
        self.saved = True
        messagebox.showinfo("Admin created", f"Admin '{name}' created successfully.\nYou can now log in.", parent=self)
        self.destroy()


# ── Forgot / Reset Password Dialog ───────────────────────────

class ForgotPasswordDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Forgot Password - Reset")
        self.resizable(False, False)
        self.saved = False
        self.generated_pw = None

        # Allow dialog to auto-expand for long success messages (wrap + entry)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        # Make columns expand so Entry can stretch and wraplength can be wide
        frm.columnconfigure(1, weight=1)

        # Logo centered top
        logo_top = ttk.Frame(frm)
        logo_top.grid(row=0, column=0, columnspan=2, pady=(0,8), sticky="ew")
        _logo = _load_logo_image((48, 48))
        if _logo:
            self._logo_img = _logo
            ttk.Label(logo_top, image=_logo).pack(side="left", padx=(0,8))
            ttk.Label(logo_top, text="IEDCR", font=("Segoe UI", 11, "bold"), foreground="#006633").pack(side="left")
        ttk.Label(frm, text="Reset Password", font=("Segoe UI", 12, "bold")).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0,4))
        ttk.Label(frm, text="Enter your registered mobile number.\nIf it exists, a random password will be generated\ne.g. 7aB3x9Kp and synced to cloud + local DB.",
                  foreground="#555", justify="left", wraplength=520).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0,10))

        self.var_phone = tk.StringVar()

        ttk.Label(frm, text="Mobile *").grid(row=3, column=0, sticky="w", padx=(0,10), pady=4)
        ent_phone = ttk.Entry(frm, textvariable=self.var_phone, width=32)
        ent_phone.grid(row=3, column=1, sticky="we", pady=4)

        # Status - wide wraplength so it auto-wraps instead of truncating; auto-scale handled via update
        self.lbl_status = ttk.Label(frm, text="", foreground="#006633", wraplength=520, justify="left", anchor="w")
        self.lbl_status.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6,0))

        self.progress = ttk.Progressbar(frm, mode="indeterminate", length=380)
        # hidden until syncing
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4,0))
        self.progress.grid_remove()

        # ── Copy-friendly generated password area (hidden until success) ──
        # Replaces old lbl_generated which was not selectable / truncated
        self.frame_result = ttk.LabelFrame(frm, text="Generated password (copy friendly)", padding=10)
        # not gridded yet - will be gridded on success at row 6
        self.var_generated = tk.StringVar(value="")
        self.lbl_generated_title = ttk.Label(self.frame_result, text="", foreground="#0033cc", font=("Segoe UI", 10, "bold"), wraplength=500, justify="left", anchor="w")
        self.lbl_generated_title.pack(fill="x", pady=(0,6))
        pw_row = ttk.Frame(self.frame_result)
        pw_row.pack(fill="x", pady=(0,6))
        self.ent_pw_display = ttk.Entry(pw_row, textvariable=self.var_generated, width=28, font=("Consolas", 13, "bold"), justify="center", state="readonly")
        self.ent_pw_display.pack(side="left", fill="x", expand=True, padx=(0,8))
        # Make readonly entry selectable: need to allow focus and selection
        self.ent_pw_display.bind("<Button-1>", lambda e: self.ent_pw_display.selection_range(0, tk.END))
        self.ent_pw_display.bind("<FocusIn>", lambda e: self.ent_pw_display.selection_range(0, tk.END))
        self.btn_copy = ttk.Button(pw_row, text="Copy", width=8, command=self._copy_pw)
        self.btn_copy.pack(side="left")
        self.btn_show = ttk.Button(pw_row, text="Show", width=7, command=self._toggle_generated_visibility)
        self.btn_show.pack(side="left", padx=(4,0))
        self._pw_visible = True
        self.lbl_copy_hint = ttk.Label(self.frame_result, text="Password auto-copied to clipboard. Click Copy to copy again. You can select text above and press Ctrl+C.", foreground="#555", wraplength=500, justify="left")
        self.lbl_copy_hint.pack(fill="x")
        # fallback label for simple text (kept for compatibility but now uses frame)
        self.lbl_generated = ttk.Label(frm, text="", foreground="#0033cc", font=("Segoe UI", 10, "bold"), wraplength=520, justify="left", anchor="w")
        # not gridded by default; frame_result will be used instead

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10,0))
        btns.columnconfigure(0, weight=1)
        self.btn_reset = ttk.Button(btns, text="Generate & Reset", command=self._do_reset)
        self.btn_reset.pack(side="right", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")

        self.bind("<Return>", lambda e: self._do_reset())
        self.bind("<Escape>", lambda e: self.destroy())

        try:
            if master.winfo_viewable():
                self.transient(master)
        except Exception:
            pass
        self.update_idletasks()
        self.deiconify()
        # Center dialog on screen (or over parent if viewable) — previously appeared at screen edge
        try:
            self.update_idletasks()
            w = self.winfo_width()
            h = self.winfo_height()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            if master.winfo_viewable():
                mx = master.winfo_rootx()
                my = master.winfo_rooty()
                mw = master.winfo_width()
                mh = master.winfo_height()
                x = mx + (mw - w)//2
                y = my + (mh - h)//2
            else:
                x = (sw - w)//2
                y = (sh - h)//2 - 40
            # keep on screen
            x = max(10, min(x, sw - w - 10))
            y = max(10, min(y, sh - h - 40))
            self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass
        try:
            if master.winfo_viewable():
                self.grab_set()
        except Exception:
            pass
        try:
            self.focus_set()
        except Exception:
            pass
        ent_phone.focus_set()

    def _do_reset(self):
        phone = self.var_phone.get().strip()
        if not phone:
            messagebox.showerror("Missing", "Mobile number is required.", parent=self)
            return
        user = db.get_user_by_phone(phone)
        if not user:
            messagebox.showerror("Not found", f"No user found with mobile '{phone}'.", parent=self)
            return

        # generate random password like 1233434 with characters (8 chars alphanumeric)
        new_pw = _generate_random_password(8)
        self.generated_pw = new_pw

        # Disable button while syncing - show centered progress
        self.btn_reset.configure(state="disabled")
        self.lbl_status.configure(text=f"Generating password and syncing to cloud for {user['phone']}...", foreground="#006633")
        # hide previous result if any
        try:
            self.frame_result.grid_remove()
        except Exception:
            pass
        self.lbl_generated.configure(text="")
        try:
            self.progress.grid()
            self.progress.start(12)
        except Exception:
            pass
        self.update_idletasks()

        ok, status, body = supabase_reset_password(user["phone"], new_pw)

        try:
            self.progress.stop()
            self.progress.grid_remove()
        except Exception:
            pass

        if not ok:
            self.btn_reset.configure(state="normal")
            detail = body[:400] if body else f"HTTP {status}"
            messagebox.showerror("Cloud sync failed",
                                 f"Password reset NOT applied locally.\nCloud sync failed (status {status}).\n\n{detail}\n\nLocal DB was NOT updated (as required).",
                                 parent=self)
            # auto-scale status width for long error
            self.lbl_status.configure(text=f"Failed: {detail[:300]}", foreground="#cc0000")
            self._autosize()
            return

        # Success from API -> now update local sqlite with same generated password
        try:
            db.update_user_password_by_phone(user["phone"], new_pw)
        except Exception as exc:
            messagebox.showerror("Local update failed", f"Cloud succeeded but local update failed:\n{exc}", parent=self)
            self.btn_reset.configure(state="normal")
            return

        self.lbl_status.configure(text="Password synced to cloud and local DB.", foreground="#006633")
        # ── Show copy-friendly result with auto-scale ──
        self.var_generated.set(new_pw)
        self.lbl_generated_title.configure(text=f"New password for {user['name']} ({user['phone']}):")
        # ensure readonly entry shows plain text (visible)
        self._pw_visible = True
        self.ent_pw_display.configure(show="")
        self.btn_show.configure(text="Hide")
        # grid the result frame (was hidden); this will expand dialog width
        try:
            self.frame_result.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8,0))
        except Exception:
            pass
        # also keep label for legacy (hidden)
        self.lbl_generated.configure(text="")
        # auto-scale dialog width to fit message + entry
        self._autosize()
        # copy to clipboard automatically + select text for manual Ctrl+C
        try:
            self.clipboard_clear()
            self.clipboard_append(new_pw)
        except Exception:
            pass
        try:
            self.ent_pw_display.focus_set()
            self.ent_pw_display.selection_range(0, tk.END)
        except Exception:
            pass
        # update hint to confirm copied
        self.lbl_copy_hint.configure(text=f"Copied to clipboard! Select the password above and press Ctrl+C if needed. Use it to login.", foreground="#006633")
        messagebox.showinfo("Success",
                            f"Password reset successful for {user['name']} ({user['phone']}).\n\nGenerated password: {new_pw}\n(Copied to clipboard - also available in the dialog with Copy button)\n\nUse this to login. Supabase + local DB updated.",
                            parent=self)
        self.saved = True
        # keep dialog open so user can see password and copy, but allow close
        self.btn_reset.configure(state="normal")
        self.btn_reset.configure(text="Done", command=self.destroy)
        # ensure still auto-sized after messagebox (which may have changed focus)
        self._autosize()

    def _copy_pw(self):
        pw = self.var_generated.get().strip()
        if not pw:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(pw)
            self.update()
        except Exception:
            pass
        # visual feedback
        orig = self.btn_copy.cget("text")
        self.btn_copy.configure(text="Copied!")
        self.lbl_copy_hint.configure(text="Copied! You can also select the password and press Ctrl+C.", foreground="#006633")
        # ensure entry text is selected for manual copy
        try:
            self.ent_pw_display.selection_range(0, tk.END)
            self.ent_pw_display.focus_set()
        except Exception:
            pass
        self.after(1500, lambda: self.btn_copy.configure(text="Copy") if self.winfo_exists() else None)

    def _toggle_generated_visibility(self):
        self._pw_visible = not self._pw_visible
        self.ent_pw_display.configure(show="" if self._pw_visible else "•")
        self.btn_show.configure(text="Hide" if self._pw_visible else "Show")

    def _autosize(self):
        """Auto-scale dialog width/height to fit wrapped messages and copy entry."""
        try:
            self.update_idletasks()
            # Let ttk compute required size; expand if needed but cap to screen
            req_w = self.winfo_reqwidth()
            req_h = self.winfo_reqheight()
            # ensure minimum width for copy-friendly layout (entry + buttons)
            min_w = 520
            w = max(req_w, min_w)
            # also consider wraplength content: if status text long, ensure dialog wider
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            # cap to 85% of screen width
            max_w = int(sw * 0.85)
            w = min(w, max_w)
            h = min(req_h, int(sh * 0.85))
            # current position
            cur_geo = self.geometry()
            # only adjust size, keep centered if possible
            try:
                x = self.winfo_x()
                y = self.winfo_y()
                # keep on screen
                if x + w > sw - 10:
                    x = sw - w - 10
                if y + h > sh - 40:
                    y = sh - h - 40
                x = max(10, x); y = max(10, y)
                self.geometry(f"{w}x{h}+{x}+{y}")
            except Exception:
                self.geometry(f"{w}x{h}")
        except Exception:
            pass


# ── Login Window ─────────────────────────────────────────────

class LoginWindow(tk.Toplevel):
    def __init__(self, master, on_success=None):
        super().__init__(master)
        self.on_success = on_success
        self.authenticated_user = None
        self.title("Login - Employee Visit Tracker")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Hide parent until login succeeds? caller handles.

        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)

        # Logo + Header centered
        header_top = ttk.Frame(outer)
        header_top.pack(fill="x", pady=(0,10))
        _logo = _load_logo_image((52, 52))
        if _logo:
            self._logo_img = _logo
            ttk.Label(header_top, image=_logo).pack(side="left", padx=(0,12))
        header_text = ttk.Frame(header_top)
        header_text.pack(side="left", fill="x")
        ttk.Label(header_text, text="IEDCR - Employee Visit Tracker", font=("Segoe UI", 13, "bold"), foreground="#006633").pack(anchor="w")
        ttk.Label(header_text, text="Please sign in to continue", foreground="#555").pack(anchor="w")
        # also show logo centered if no image? already handled
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=(0,12))

        form = ttk.Frame(outer)
        form.pack(fill="x")

        ttk.Label(form, text="Mobile or Email *").grid(row=0, column=0, sticky="w", padx=(0,10), pady=5)
        self.var_ident = tk.StringVar()
        ent_ident = ttk.Entry(form, textvariable=self.var_ident, width=32, font=("Segoe UI", 11))
        ent_ident.grid(row=0, column=1, sticky="we", pady=5)

        ttk.Label(form, text="Password *").grid(row=1, column=0, sticky="w", padx=(0,10), pady=5)
        self.var_pw = tk.StringVar()
        self.var_show = tk.BooleanVar(value=False)
        self.ent_pw = ttk.Entry(form, textvariable=self.var_pw, width=32, show="•", font=("Segoe UI", 11))
        self.ent_pw.grid(row=1, column=1, sticky="we", pady=5)
        form.columnconfigure(1, weight=1)

        opts = ttk.Frame(outer)
        opts.pack(fill="x", pady=(4,0))
        ttk.Checkbutton(opts, text="Show password", variable=self.var_show,
                        command=lambda: self.ent_pw.configure(show="" if self.var_show.get() else "•")).pack(side="left")
        self.var_stay = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Stay logged in", variable=self.var_stay).pack(side="left", padx=(16,0))

        self.lbl_err = ttk.Label(outer, text="", foreground="#cc0000", wraplength=320, justify="left")
        self.lbl_err.pack(anchor="w", pady=(6,0))

        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(14,0))
        ttk.Button(btns, text="Forgot Password?", command=self._forgot).pack(side="left")
        ttk.Button(btns, text="Login", command=self._login, style="Accent.TButton").pack(side="right", padx=4)
        # Exit button
        ttk.Button(btns, text="Exit", command=self._on_close).pack(side="right")

        # If no users, offer create admin directly
        if db.count_users() == 0:
            self.lbl_err.configure(text="No users found. Please create the first admin.", foreground="#006633")
            ttk.Button(outer, text="Create First Admin", command=self._create_first_admin).pack(fill="x", pady=(10,0))

        self.bind("<Return>", lambda e: self._login())
        self.bind("<Escape>", lambda e: self._on_close())

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
        ent_ident.focus_set()
        # Center
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"+{(sw-w)//2}+{(sh-h)//2 - 40}")
        except Exception:
            pass

    def _on_close(self):
        # Ask confirm then exit app
        if messagebox.askyesno("Exit", "Exit the application?", parent=self):
            master = self.master
            try:
                self.destroy()
            except tk.TclError:
                pass
            except Exception:
                pass
            try:
                if master is not None and master.winfo_exists():
                    master.destroy()
            except Exception:
                pass

    def _create_first_admin(self):
        dlg = CreateAdminDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.lbl_err.configure(text=f"Admin '{dlg.created_user['name']}' created. Please log in.", foreground="#006633")

    def _forgot(self):
        dlg = ForgotPasswordDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.lbl_err.configure(text="Password reset done. Please log in with new password.", foreground="#006633")

    def _login(self):
        ident = self.var_ident.get().strip()
        pw = self.var_pw.get()
        stay = bool(self.var_stay.get())
        if not ident or not pw:
            self.lbl_err.configure(text="Please enter mobile/email and password.")
            return
        try:
            user = db.verify_login(ident, pw)
        except ValueError as exc:
            self.lbl_err.configure(text=str(exc))
            return
        if not user:
            self.lbl_err.configure(text="Invalid mobile/email or password.")
            return
        # success
        db.set_user_logged_in(user["id"], True)
        if stay:
            db.set_stay_logged_in(user["id"], True)
        else:
            db.clear_stay_logged_in()
        self.authenticated_user = db.get_user_by_id(user["id"])
        self.lbl_err.configure(text="")
        if self.on_success:
            try:
                self.on_success(self.authenticated_user)
            except Exception:
                pass
        self.destroy()


# ── Add User Dialog (for admin) ──────────────────────────────

class AddUserDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.saved = False
        self.title("Add New User")
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Add New User", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0,8))

        self.var_name = tk.StringVar()
        self.var_phone = tk.StringVar()
        self.var_email = tk.StringVar()
        self.var_pw = tk.StringVar()
        self.var_role = tk.StringVar(value="user")
        self.var_show = tk.BooleanVar(value=False)

        rows = [
            ("Full Name *", self.var_name, False),
            ("Mobile *", self.var_phone, False),
            ("Email *", self.var_email, False),
            ("Password *", self.var_pw, True),
        ]
        for i, (lbl, var, is_pw) in enumerate(rows):
            ttk.Label(frm, text=lbl).grid(row=1+i, column=0, sticky="w", padx=(0,10), pady=4)
            ent = ttk.Entry(frm, textvariable=var, width=30, show="•" if is_pw else "")
            ent.grid(row=1+i, column=1, sticky="we", pady=4)
            if is_pw:
                self._pw_ent = ent
        ttk.Label(frm, text="Role *").grid(row=5, column=0, sticky="w", padx=(0,10), pady=4)
        ttk.Combobox(frm, textvariable=self.var_role, state="readonly", width=28, values=["admin","user"]).grid(row=5, column=1, sticky="we", pady=4)
        ttk.Checkbutton(frm, text="Show password", variable=self.var_show,
                        command=lambda: self._pw_ent.configure(show="" if self.var_show.get() else "•")).grid(row=6, column=1, sticky="w", pady=(2,4))
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(10,0))
        ttk.Button(btns, text="Create", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
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

    def _save(self):
        name, phone, email, pw, role = self.var_name.get(), self.var_phone.get(), self.var_email.get(), self.var_pw.get(), self.var_role.get()
        if not pw or len(pw.strip()) < 6:
            messagebox.showerror("Invalid", "Password must be at least 6 characters.", parent=self)
            return
        try:
            db.create_app_user(name, phone, email, pw, role=role)
        except ValueError as exc:
            messagebox.showerror("Invalid input", str(exc), parent=self)
            return
        self.saved = True
        self.destroy()


class ManageUsersDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Manage Users - Admin")
        self.geometry("720x380")
        self.minsize(640, 340)
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        top = ttk.Frame(frm)
        top.pack(fill="x", pady=(0,6))
        ttk.Label(top, text="Users", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top, text="Add User", command=self._add).pack(side="left", padx=8)
        ttk.Button(top, text="Delete Selected", command=self._delete).pack(side="left", padx=4)
        ttk.Button(top, text="Reset Password", command=self._reset_pw).pack(side="left", padx=4)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(top, text="Supabase Config", command=self._supabase_config).pack(side="left", padx=4)
        ttk.Button(top, text="Close", command=self.destroy).pack(side="right")

        cols = [("id","ID",50,"center"),("name","Name",140,"w"),("phone","Mobile",130,"w"),("email","Email",180,"w"),("role","Role",70,"center"),("status","Status",70,"center")]
        frame = ttk.Frame(frm)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for cid, txt, w, a in cols:
            self.tree.heading(cid, text=txt)
            self.tree.column(cid, width=w, anchor=a)
        self.refresh()
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

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for u in db.list_app_users():
            status = "Active" if u["is_active"] else "Disabled"
            if u["is_logged_in"]:
                status += " • LoggedIn"
            self.tree.insert("", "end", iid=str(u["id"]), values=(u["id"], u["name"], u["phone"], u["email"], u["role"], status))

    def _add(self):
        dlg = AddUserDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.refresh()

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a user first.", parent=self)
            return
        uid = int(sel[0])
        if len(db.list_app_users()) <= 1:
            messagebox.showerror("Blocked", "Cannot delete the last remaining user.", parent=self)
            return
        user = db.get_user_by_id(uid)
        if messagebox.askyesno("Confirm", f"Delete user '{user['name']}' ({user['phone']})?", parent=self):
            import sqlite3
            from contextlib import closing
            with closing(db._connect()) as conn, conn:
                conn.execute("DELETE FROM app_users WHERE id=?", (uid,))
            self.refresh()

    def _reset_pw(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a user first.", parent=self)
            return
        uid = int(sel[0])
        user = db.get_user_by_id(uid)
        new_pw = tk.simpledialog.askstring("Reset Password", f"New password for {user['name']} ({user['phone']}):", parent=self, show="•")
        if not new_pw:
            return
        if len(new_pw.strip()) < 6:
            messagebox.showerror("Too short", "Password must be at least 6 characters.", parent=self)
            return
        # Ask confirm cloud sync? For admin manual reset we update both cloud and local; but if cloud fails we still maybe want local? Requirement says based on success from api then local, so enforce.
        if messagebox.askyesno("Sync to cloud?", f"Sync new password to cloud (Supabase) for mobile {user['phone']}?\nLocal DB will be updated only on success.", parent=self):
            ok, status, body = supabase_reset_password(user["phone"], new_pw.strip())
            if not ok:
                messagebox.showerror("Cloud failed", f"Cloud sync failed ({status}): {body[:400]}\nLocal NOT updated.", parent=self)
                return
        try:
            db.update_user_password_by_id(uid, new_pw.strip())
            messagebox.showinfo("Done", "Password updated.", parent=self)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)

    def _supabase_config(self):
        dlg = SupabaseConfigDialog(self)
        self.wait_window(dlg)


class SupabaseConfigDialog(tk.Toplevel):
    """Admin Setup - Supabase Config stored in DB config table (app_state).

    Environment variables cannot be packed into a compiled build, so these
    keys must be persisted in the database. This dialog lets an admin set
    SUPABASE_API_KEY and SUPABASE_SECRET_KEY (and optional URL) into
    app_state so the compiled exe works without env vars.
    Required fields are marked with *.
    """
    def __init__(self, master):
        super().__init__(master)
        self.saved = False
        self.title("Admin Setup - Supabase Config (DB)")
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=18)
        frm.pack(fill="both", expand=True)

        # Header with logo
        logo_top = ttk.Frame(frm)
        logo_top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        _logo = _load_logo_image((40, 40))
        if _logo:
            self._logo_img = _logo
            ttk.Label(logo_top, image=_logo).pack(side="left", padx=(0, 8))
        ttk.Label(logo_top, text="Supabase Configuration - Required for Forgot Password",
                  font=("Segoe UI", 11, "bold"), foreground="#006633").pack(side="left")
        ttk.Label(frm, text="Keys are saved to the database config table (app_state) so a compiled build works without env vars.",
                  foreground="#555", wraplength=520, justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 2))
        ttk.Label(frm, text="Env vars (.env / OS env) are fallback only; DB values take priority.",
                  foreground="#777", wraplength=520, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # Current source hint
        db.init_db()
        cfg = {}
        try:
            cfg = db.get_supabase_config() if hasattr(db, "get_supabase_config") else {
                "api_key": db.get_app_state("supabase_api_key") or db.get_app_state("supabase_key") or "",
                "secret_key": db.get_app_state("supabase_secret_key") or "",
                "url": db.get_app_state("supabase_url") or "",
            }
        except Exception:
            cfg = {"api_key": "", "secret_key": "", "url": ""}

        api_in_db = bool((cfg.get("api_key") or "").strip())
        sec_in_db = bool((cfg.get("secret_key") or "").strip())
        url_in_db = (cfg.get("url") or "").strip() or SUPABASE_URL
        if api_in_db and sec_in_db:
            src_text = "Status: Configured in DB (compiled build will work)."
            src_color = "#006633"
        elif api_in_db or sec_in_db:
            src_text = "Status: Partially configured in DB - both keys are required."
            src_color = "#cc7700"
        else:
            # check env fallback
            env_api = (os.environ.get("SUPABASE_API_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
            env_sec = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
            if env_api or env_sec:
                src_text = "Status: Using env vars (.env) - save to DB to support compiled build."
                src_color = "#cc7700"
            else:
                src_text = "Status: NOT configured - Forgot Password will fail. Both keys are required."
                src_color = "#cc0000"
        self.lbl_status = ttk.Label(frm, text=src_text, foreground=src_color, wraplength=520, justify="left", font=("Segoe UI", 9, "bold"))
        self.lbl_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))

        # Fields
        self.var_api = tk.StringVar(value=cfg.get("api_key") or "")
        self.var_secret = tk.StringVar(value=cfg.get("secret_key") or "")
        self.var_url = tk.StringVar(value=cfg.get("url") or SUPABASE_URL)
        self.var_show = tk.BooleanVar(value=False)

        ttk.Label(frm, text="SUPABASE_API_KEY *").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ent_api = ttk.Entry(frm, textvariable=self.var_api, width=58, show="•")
        self.ent_api.grid(row=4, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Label(frm, text="SUPABASE_SECRET_KEY *").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ent_secret = ttk.Entry(frm, textvariable=self.var_secret, width=58, show="•")
        self.ent_secret.grid(row=5, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Label(frm, text="Supabase URL").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ent_url = ttk.Entry(frm, textvariable=self.var_url, width=58)
        self.ent_url.grid(row=6, column=1, columnspan=2, sticky="we", pady=4)
        hint = ttk.Label(frm, text="Find keys: Supabase Dashboard -> Project Settings -> API. API key = anon public, Secret = service_role.",
                         foreground="#666", wraplength=520, justify="left")
        hint.grid(row=7, column=0, columnspan=3, sticky="w", pady=(2, 6))

        ttk.Checkbutton(frm, text="Show keys", variable=self.var_show,
                        command=self._toggle_show).grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 6))

        self.lbl_msg = ttk.Label(frm, text="", foreground="#cc0000", wraplength=520, justify="left")
        self.lbl_msg.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        ttk.Button(btns, text="Import from .env", command=self._import_env).pack(side="left")
        ttk.Button(btns, text="Clear DB Config", command=self._clear).pack(side="left", padx=6)
        ttk.Button(btns, text="Test Connection", command=self._test).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save to DB", command=self._save).pack(side="right", padx=6)

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
        self.ent_api.configure(show=show)
        self.ent_secret.configure(show=show)

    def _import_env(self):
        # read .env file directly if python-dotenv not loaded, and env vars
        env_api = (os.environ.get("SUPABASE_API_KEY") or os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
        env_sec = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        # also try reading .env file manually as fallback
        if not env_api or not env_sec:
            try:
                base = os.path.dirname(os.path.abspath(__file__))
                env_path = os.path.join(base, ".env")
                if os.path.isfile(env_path):
                    with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
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
        if env_api:
            self.var_api.set(env_api)
        if env_sec:
            self.var_secret.set(env_sec)
        if env_api or env_sec:
            self.lbl_msg.configure(text="Imported keys from env/.env into fields. Click Save to persist to DB.", foreground="#006633")
        else:
            self.lbl_msg.configure(text="No keys found in env or .env file.", foreground="#cc0000")

    def _clear(self):
        if not messagebox.askyesno("Clear DB Config", "Clear Supabase keys from DB config table?\nEnv vars will still work for dev, but compiled build will have no keys.", parent=self):
            return
        try:
            if hasattr(db, "set_supabase_config"):
                db.set_supabase_config(api_key="", secret_key="", url="")
            else:
                db.delete_app_state("supabase_api_key")
                db.delete_app_state("supabase_key")
                db.delete_app_state("supabase_secret_key")
                db.delete_app_state("supabase_url")
            self.var_api.set("")
            self.var_secret.set("")
            self.var_url.set(SUPABASE_URL)
            self.lbl_msg.configure(text="DB config cleared.", foreground="#cc7700")
            self.lbl_status.configure(text="Status: NOT configured - both keys are required.", foreground="#cc0000")
        except Exception as exc:
            messagebox.showerror("Error", str(exc), parent=self)

    def _test(self):
        api = self.var_api.get().strip()
        sec = self.var_secret.get().strip()
        url = self.var_url.get().strip() or SUPABASE_URL
        if not api or not sec:
            messagebox.showerror("Missing", "Both SUPABASE_API_KEY and SUPABASE_SECRET_KEY are required for test.", parent=self)
            return
        self.lbl_msg.configure(text="Testing connection...", foreground="#006633")
        self.update_idletasks()
        # quick header check + optional lightweight GET to password_resets with limit 1
        headers = {
            "apikey": api,
            "Authorization": f"Bearer {sec}",
            "Content-Type": "application/json",
        }
        test_url = url.split("?")[0] + "?select=mobile&limit=1"
        ok, status, body = False, 0, ""
        try:
            import requests
            try:
                resp = requests.get(test_url, headers=headers, timeout=8)
                ok, status, body = resp.ok, resp.status_code, resp.text
            except Exception as e:
                ok, status, body = False, 0, str(e)
        except ImportError:
            import urllib.request, urllib.error
            try:
                req = urllib.request.Request(test_url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=8) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                    ok, status = 200 <= resp.status < 300, resp.status
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="ignore") if hasattr(e, 'read') else str(e)
                ok, status = False, e.code
            except Exception as e:
                ok, status, body = False, 0, str(e)
        if ok:
            self.lbl_msg.configure(text=f"Connection OK (HTTP {status}). Keys look valid.", foreground="#006633")
            messagebox.showinfo("Test OK", f"Supabase reachable (HTTP {status}).\nKeys appear valid.", parent=self)
        else:
            detail = (body or "")[:400]
            self.lbl_msg.configure(text=f"Test failed (HTTP {status}). {detail[:80]}", foreground="#cc0000")
            messagebox.showerror("Test Failed", f"HTTP {status}\n{detail}\n\nCheck keys / URL and network.", parent=self)

    def _save(self):
        api = self.var_api.get().strip()
        sec = self.var_secret.get().strip()
        url = self.var_url.get().strip()
        if not api:
            messagebox.showerror("Required", "SUPABASE_API_KEY is required (anon public key).", parent=self)
            return
        if not sec:
            messagebox.showerror("Required", "SUPABASE_SECRET_KEY is required (service_role key).", parent=self)
            return
        # basic JWT-like check
        if len(api) < 20 or len(sec) < 20:
            if not messagebox.askyesno("Confirm", "Keys look unusually short. Save anyway?", parent=self):
                return
        if url and not url.startswith("http"):
            messagebox.showerror("Invalid URL", "Supabase URL must start with http(s).", parent=self)
            return
        try:
            db.init_db()
            if hasattr(db, "set_supabase_config"):
                db.set_supabase_config(api_key=api, secret_key=sec, url=url if url != SUPABASE_URL else "")
                # if user changed URL from default, store even if empty means default
                if url and url != SUPABASE_URL:
                    db.set_app_state("supabase_url", url)
                elif not url or url == SUPABASE_URL:
                    # store only if custom; remove if back to default to avoid clutter, but keep if explicit
                    # Keep DB clean: delete if equals default
                    try:
                        cur = db.get_app_state("supabase_url")
                        if cur == SUPABASE_URL:
                            pass
                        if url == SUPABASE_URL:
                            db.delete_app_state("supabase_url")
                    except Exception:
                        pass
            else:
                db.set_app_state("supabase_api_key", api)
                db.set_app_state("supabase_secret_key", sec)
                if url and url != SUPABASE_URL:
                    db.set_app_state("supabase_url", url)
                else:
                    db.delete_app_state("supabase_url")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        self.lbl_msg.configure(text="Saved to DB config table. Compiled build will now have keys.", foreground="#006633")
        messagebox.showinfo("Saved", "Supabase keys saved to database config table (app_state).\nCompiled builds will read them without env vars.", parent=self)
        self.saved = True
        self.destroy()


def ensure_first_admin(parent):
    """If no users exist, force create first admin dialog modally."""
    if db.count_users() == 0:
        dlg = CreateAdminDialog(parent)
        parent.wait_window(dlg)
        return dlg.saved
    return True

def do_login_flow(root_hidden):
    """Blocking login flow: handles first-admin then login loop. Returns user or None if exit."""
    db.init_db()
    # Check stay logged in
    stay_user = db.get_stay_logged_in_user()
    if stay_user:
        db.set_user_logged_in(stay_user["id"], True)
        return stay_user

    # If no users, force create admin first
    if db.count_users() == 0:
        # need a temporary hidden root to parent dialogs
        dlg = CreateAdminDialog(root_hidden)
        root_hidden.wait_window(dlg)
        if not dlg.saved:
            return None

    # Now login
    result = {"user": None}
    def on_success(u):
        result["user"] = u

    login = LoginWindow(root_hidden, on_success=on_success)
    root_hidden.wait_window(login)
    if result["user"]:
        return result["user"]
    # LoginWindow may have set authenticated_user if stayed?
    if login.authenticated_user:
        return login.authenticated_user
    return None
