# Employee Visit App (IEDCR)

Tkinter + SQLite desktop app for tracking employees, tenure history and abroad visits.

## Setup

```powershell
# create venv (optional)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt  # Pillow, requests, cairosvg (optional), python-dotenv (optional)
```

## Environment - Supabase Keys (Required for Forgot Password)

Forgot Password generates a random alphanumeric password (e.g. `7aB3x9Kp`), POSTs it to Supabase, and **only on success** updates the local SQLite password. It needs Supabase API keys.

1. Open your Supabase project: https://supabase.com/dashboard
2. Go to **Project Settings -> API** (or **Settings -> API** / **Connect**).
3. Copy:
   - **Project URL** (already set to `https://zjxeigfwmrtnqatlvjqe.supabase.co` in `auth.py`)
   - **anon public** key -> use as `SUPABASE_API_KEY`
   - **service_role secret** key -> use as `SUPABASE_SECRET_KEY` (keep secret, server-side only)
   > Legacy single key `SUPABASE_KEY` also works (used for both headers).

4. Set via environment before running:

```powershell
# PowerShell (current session)
$env:SUPABASE_API_KEY="eyJ...anon_key"
$env:SUPABASE_SECRET_KEY="eyJ...service_role_key"

# or create .env in project root (auto-loaded if python-dotenv installed)
# .env
SUPABASE_API_KEY=eyJ...anon
SUPABASE_SECRET_KEY=eyJ...service_role
```

```bash
# bash / macOS
export SUPABASE_API_KEY="eyJ...anon"
export SUPABASE_SECRET_KEY="eyJ...service_role"
```

Without these env vars, Forgot Password will show: `Supabase keys not configured.`

Headers sent on reset (as required):
```
apikey: <SUPABASE_API_KEY>
Authorization: Bearer <SUPABASE_SECRET_KEY>
Content-Type: application/json
Prefer: resolution=merge-duplicates,return=representation
POST https://zjxeigfwmrtnqatlvjqe.supabase.co/rest/v1/password_resets?on_conflict=mobile
Body: {"mobile":"01789270125","password":"<generated>"}
```

## Run

```powershell
python main.py
```

- On first launch with empty DB you must **Create First Admin** (name, phone, email, password).
- Login with **Mobile or Email + Password**. Check **Stay logged in** to bypass login next launch.
- **Forgot Password** on login screen: enter mobile only. If mobile exists, a random password like `a3K9p2X7` is generated, sent to Supabase, and on success stored locally (DB update only after cloud success). Generated password is shown and copied to clipboard.
- After login, header shows user/role. **Admin** sees **Manage Users** to add/delete users and reset passwords (optionally sync to Supabase).
- Logout clears `is_logged_in` in DB; close window respects Stay logged in.

## Environment - Turso Sync (Optional, Recommended for Cloud Backup at turso.tech)

Every write (add/edit/delete employee, visit, tenure, project, org) is auto-synced to Turso when network is available. Offline writes are queued and retried.

1. Create a Turso DB: https://turso.tech -> `turso db create employee-visits` (or via dashboard)
2. Get credentials:
   ```powershell
   turso db show employee-visits --url        # -> libsql://employee-visits-xxx.turso.io
   turso db tokens create employee-visits     # -> eyJ...
   ```
3. Configure in app (Admin only): open **Turso Sync** button in header (or Manage Users -> Turso Sync) and paste:
   - **TURSO_DATABASE_URL** = `libsql://employee-visits-xxx.turso.io`  (or `https://...`)
   - **TURSO_AUTH_TOKEN** = token from step 2
   - Enable toggle. Click **Save** then **Test Connection** / **Sync Now**.

   Or set via env before running (fallback, dev only - compiled builds need DB config):
   ```powershell
   $env:TURSO_DATABASE_URL="libsql://employee-visits-xxx.turso.io"
   $env:TURSO_AUTH_TOKEN="eyJ..."
   # or .env
   TURSO_DATABASE_URL=libsql://...
   TURSO_AUTH_TOKEN=eyJ...
   ```
   DB values (app_state) take priority over env for compiled builds.

4. Status is shown in the bottom-right status bar: `Turso: synced ...` / `pending sync...` / `offline - queued` / `not configured`. Click it to open config.

Sync details:
- Local `employees.db` stays the primary (offline-first). Remote is a mirror.
- On each mutation `db.py` calls `turso_sync.schedule_sync()` (debounced 2s, background thread). If offline, `pending` is stored and retried every 60s + on next launch.
- Push uses Hrana HTTP `/v2/pipeline` (no native `libsql` required). If `libsql` is installed, it could also use embedded replica path. Full snapshot (DELETE + INSERT) handles deletions, batched 60 rows/request.
- Requires `requests` (already used for Supabase). Works without `libsql`.

To disable: Admin -> Turso Sync -> uncheck "Enable auto sync" -> Save.

## Database

SQLite file `employees.db` (or `EMPLOYEE_VISITS_DB` env). Tables: `employees`, `visits`, `projects`, `organizations`, `employee_tenures`, plus auth tables `app_users` (hashed passwords via PBKDF2) and `app_state`.

## Troubleshooting

- `Supabase keys not configured` -> set env vars above and restart.
- `No user with mobile` -> mobile must exactly match `app_users.phone`.
- Freeze on startup was fixed by removing blocking `wait_visibility` before mainloop; if still freezing, update to latest `auth.py`/`main.py`.
