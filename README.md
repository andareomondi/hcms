# HCMS: Hospital Clinical Management System

A modular Django application for running a hospital's outpatient, emergency, ward, pharmacy,
radiology, laboratory and billing work in one system. It is built as a modular monolith: each
clinical or administrative function is a Django app, and they share one database and one
permissions layer.

The system is designed to run on a hospital's local network. Every change is audited, and money is
never silently released.

> **Status:** under active development. Several modules are complete in code but not yet covered by
> an automated test run. The device gate is planned but not yet implemented. See
> [Known limitations](#known-limitations).

---

## Contents

- [Key principles](#key-principles)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation (development)](#installation-development)
- [Configuration](#configuration)
- [Production setup](#production-setup)
- [First run](#first-run)
- [Roles and permissions](#roles-and-permissions)
- [Patient journey](#patient-journey)
- [Modules](#modules)
- [Payments and the pre-pay gate](#payments-and-the-pre-pay-gate)
- [Audit and data export](#audit-and-data-export)
- [Management commands](#management-commands)
- [Testing](#testing)
- [Security](#security)
- [Operations](#operations)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [Contributing](#contributing)
- [Licence](#licence)

---

## Key principles

These rules are enforced in code, not left to convention.

1. **Nothing clinical or financial is deleted.** Corrections are new rows that record who made
   them and why. Released lab results are amended, not overwritten. Payments are append-only.
2. **Every state change is audited.** Views call `log_action` with a named `AuditAction`.
   Login and logout are logged by signal handlers.
3. **Permissions are capabilities, not job titles.** Views declare `@requires(Cap.X)`.
   Templates use `{% if "X" in caps %}`. Administrators can view, not act clinically.
4. **Money gates care.** Medicines, scans and lab tests are released only when their line is
   fully paid, the visit is an emergency bypass, or billing has approved a credit exception with
   a reason. The gate runs before any stock is touched.
5. **Second-person control on results.** A laboratory result is verified and released by a
   different person from the one who entered it.
6. **Untrusted browsers see nothing** *(planned)*. A device gate with admin-issued enrolment links
   will sit in front of every page, including the login screen. It is not yet implemented.

---

## Architecture

| Layer | Technology |
|---|---|
| Language and framework | Python, Django 5.2 (LTS) |
| Database | PostgreSQL |
| Front end | Django templates, Tailwind CSS, Alpine.js, htmx |
| Charts | Chart.js (export and dashboard pages) |
| Static files | WhiteNoise |
| Password hashing | Argon2id (PBKDF2 as fallback) |
| Second factor | `django-otp` installed; enforcement not yet enabled |
| History | `django-simple-history` on selected models |
| Export encryption | `pyzipper` (AES-256 zip) |

**Apps are modular.** Each clinical or administrative function is a Django app, with its own models,
views, templates and static files. Apps communicate through services and signals, not direct model
access.

Apps are listed in `hospital_system/settings.py`:

`core_patients`, `triage_queue`, `doctors_portal`, `pharmacy`, `radiology`, `lab`, `wards`,
`billing`, `dashboard`, `duty`, `notifications`, `maternity`

The `device_trust` app is planned and is not yet in `INSTALLED_APPS`.

**Offline use.** The front-end libraries are meant to be served locally from `static/`, so the
application runs on a LAN with no internet access. See [Static files](#static-files) for the current
state of this.

---

## Requirements

- Python 3.12 or newer (developed on 3.14)
- PostgreSQL 14 or newer (developed on 18)
- The packages in `requirements.txt`, including `argon2-cffi` (required by the password hasher),
  `python-dotenv` (loads `.env`), `whitenoise` (serves static files) and `pyzipper` (exports)

---

## Installation (development)

```bash
# 1. Clone and enter the project
git clone <repository-url> hcms
cd hcms

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the database and its user (as the PostgreSQL superuser)
sudo -u postgres createuser hcms_app --pwprompt        # choose a strong password
sudo -u postgres createdb -O hcms_app hospital_cms

# 5. Configure the environment
cp .env.example .env            # then edit .env (see Configuration)

# 6. Apply migrations
python manage.py migrate

# 7. Seed the hospital structure (departments, wards, beds, tests, allergens)
python seed_db.py

# 8. Create the first administrator
python manage.py createsuperuser

# 9. Collect static files (needed once, and after changing anything in static/)
python manage.py collectstatic --noinput

# 10. Run
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

---

## Configuration

All secrets and environment-specific values are read from a `.env` file in the project root, or from
real environment variables. Real environment variables take precedence. `.env` is never committed.

Create it from the template:

```bash
cp .env.example .env
```

Generate the secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

### Environment variables

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Cryptographic signing. Unique and private. | 50+ random characters |
| `DJANGO_DEBUG` | No (default `False`) | `True` for local development only | `True` |
| `DJANGO_ALLOWED_HOSTS` | Yes | Comma-separated hostnames and addresses the server answers to | `hcms.hospital.local,192.168.1.10` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | If behind HTTPS or a tunnel | Comma-separated origins with scheme | `https://hcms.hospital.local` |
| `DB_NAME` | Yes | PostgreSQL database name | `hospital_cms` |
| `DB_USER` | Yes | PostgreSQL user | `hcms_app` |
| `DB_PASSWORD` | Yes | PostgreSQL password | from your password manager |
| `DB_HOST` | No (default `127.0.0.1`) | PostgreSQL host | `127.0.0.1` |
| `DB_PORT` | No (default `5432`) | PostgreSQL port | `5432` |

A missing required variable stops Django at startup with a message that names the variable.

### Other settings

Set in `hospital_system/settings.py`, not in `.env`:

| Setting | Value | Purpose |
|---|---|---|
| `EXPORT_MAX_WEB_ROWS` | `500000` | Largest export allowed from the web page. Larger exports use the command. |
| `EXPORT_DIR` | `BASE_DIR / "private_exports"` | Private folder for export files. Outside `MEDIA_ROOT`. |
| `EXPORT_MIN_PASSPHRASE` | `12` | Minimum export passphrase length |

Tariffs (consultation fee, registration fee, scan prices) are in `billing/prices.py`. Laboratory test
prices are in the laboratory catalogue, set through `seed_db.py` or the data manager.

### Planned settings

These are described in the design but are not yet in `settings.py`. Add them when the feature lands:

| Setting | Purpose |
|---|---|
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | Cookies only over HTTPS. Set to `True` in production. |
| `HCMS_PUBLIC_BASE_URL` | Public origin used to build device-enrolment links |
| `HCMS_DEVICE_GATE`, `DEVICE_LINK_HOURS`, `DEVICE_TRUST_DAYS`, `DEVICE_MAX_PER_USER` | Device gate controls |

---

## Production setup

Follow these steps on the server, in order. The development steps above are not enough for a
server that real users reach.

### 1. Prepare the server

- A Linux server with a fixed address on the hospital LAN, and the clock synchronised by NTP.
- A dedicated system user to run the application, for example `hcms`. Do not run it as root or as
  your personal account.
- PostgreSQL installed, and the server's time zone set correctly. The application time zone is
  `Africa/Nairobi`.

### 2. Get the code and install dependencies

```bash
sudo -u hcms git clone <repository-url> /srv/hcms
cd /srv/hcms
sudo -u hcms python3 -m venv venv
sudo -u hcms venv/bin/pip install -r requirements.txt
sudo -u hcms venv/bin/pip install gunicorn
```

### 3. Create the production `.env`

```bash
sudo -u hcms cp .env.example .env
sudo chmod 600 /srv/hcms/.env
```

Edit `.env` and set:

- `DJANGO_DEBUG=False`. This is mandatory. Debug pages leak internals.
- `DJANGO_SECRET_KEY` to a newly generated key. Never reuse the development key.
- `DJANGO_ALLOWED_HOSTS` to the server's hostname and LAN address only.
- `DJANGO_CSRF_TRUSTED_ORIGINS` to the `https://` address users type into the browser.
- `DB_PASSWORD` to a strong, unique password.

### 4. Create the database

Use a strong password for the application's database user. Create the user and database as the
PostgreSQL superuser:

```bash
sudo -u postgres createuser hcms_app --pwprompt
sudo -u postgres createdb -O hcms_app hospital_cms
```

Do not use the PostgreSQL `postgres` account for the application.

### 5. Migrate, seed and collect static files

```bash
cd /srv/hcms
venv/bin/python manage.py migrate
venv/bin/python seed_db.py
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Run `seed_db.py` only once in production. It is safe to re-run, but it overwrites the placeholder
reference ranges and prices, so it should only be re-run deliberately.

### 6. Check the deployment

```bash
venv/bin/python manage.py check --deploy
```

Resolve every warning it raises before going live. Warnings about `SECURE_HSTS_SECONDS`,
`SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` mean those settings still need to be added (see
[Planned settings](#planned-settings)).

### 7. Run the application server

Gunicorn runs the Django application. WhiteNoise serves the static files, so nothing else is needed
for them.

```bash
venv/bin/gunicorn hospital_system.wsgi:application --bind 127.0.0.1:8000 --workers 3
```

Run it under systemd so it starts at boot and restarts on failure. Create
`/etc/systemd/system/hcms.service`:

```ini
[Unit]
Description=HCMS Gunicorn
After=network.target postgresql.service

[Service]
User=hcms
Group=hcms
WorkingDirectory=/srv/hcms
ExecStart=/srv/hcms/venv/bin/gunicorn hospital_system.wsgi:application --bind 127.0.0.1:8000 --workers 3
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hcms
```

### 8. Put a reverse proxy with HTTPS in front

Use Nginx, or another proxy, to terminate HTTPS and forward requests to Gunicorn on
`127.0.0.1:8000`. Use a certificate issued by the hospital's internal CA, or a public certificate if
the server is reachable from the internet. Forward these headers so Django sees the real scheme and
host:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Redirect plain HTTP to HTTPS. Do not expose port 8000 to the network.

### 9. Lock down the folders

```bash
sudo -u hcms mkdir -p /srv/hcms/private_exports
sudo chmod 700 /srv/hcms/private_exports
```

`private_exports/` holds encrypted patient exports. Only the `hcms` user should be able to read it.

### 10. Schedule backups

Back up both the database and the scan images (`MEDIA_ROOT`), and test a restore before go-live.
See [Backups](#backups).

### 11. Go-live checklist

- [ ] `DJANGO_DEBUG=False` in `.env`
- [ ] A new `DJANGO_SECRET_KEY`, not the development key
- [ ] `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` set to the production address
- [ ] `manage.py check --deploy` run and its warnings resolved
- [ ] The database password changed from any value shared during development
- [ ] `collectstatic` run after the last code change
- [ ] Gunicorn running under systemd, and the reverse proxy serving HTTPS
- [ ] Port 8000 closed to the network
- [ ] `.env` readable only by the `hcms` user
- [ ] Backups scheduled, and one restore tested
- [ ] A superuser created, and the password stored in the hospital's password manager

---

## First run

Create the first administrator on a fresh system:

```bash
python manage.py seed_db.py
python manage.py createsuperuser
```

Sign in with that account, then create the other staff accounts through **Staff requests**
(self-registration requests that a superuser approves), or through the data manager.

> **Device enrolment is not yet implemented.** Currently any browser can reach the system. When the
> device gate is built, the first administrator will be bootstrapped with a one-time enrolment link
> created from the Django shell, and every later device link will be issued from
> **Administration → Device links**.

---

## Roles and permissions

Each staff profile has one role. Each role maps to a set of capabilities in
`hospital_system/permissions.py`.

| Role | Main responsibilities | Cannot |
|---|---|---|
| Reception | Register patients, open visits, run the triage board, manage bed status | Clinical notes, prescriptions, payments |
| Triage nurse | Record vitals and complaint, set priority, assign an on-duty doctor | Finance, consultations |
| Doctor | Call patients, write notes, prescribe, order scans and lab tests, admit, complete visits | Finance, reception and triage work |
| Nurse | Give injections and IV medicines, manage bed status | Finance, consultations |
| Pharmacist | Stock and formulary, dispense oral medicines | Clinical notes, finance |
| Radiologist | Upload images, write reports | Finance, consultations |
| Lab technician | Receive samples, enter results and the written report | Verify or release results |
| Lab supervisor | Verify and release results, amend released results, manage tests | Verify a result they entered |
| Billing officer | Post charges, take payments, approve credit exceptions, clear emergency bypasses | Clinical work |
| Administrator (role) | Staff approvals, data manager, audit log, room and bed configuration | Consultations, reception, triage, pharmacy, nursing |
| Superuser | All capabilities, data export, URL explorer in development | — |

Every role also sees the **Role guide** page in the sidebar.

---

## Patient journey

```
Register patient ──► Open visit (pays registration + consultation fees at the till)
        │
        ▼
Triage (vitals, complaint, priority, doctor) ──► Queued (AWAITING_PAYMENT until fees are paid)
        │
        ▼
Doctor calls patient (WAITING → IN_CONSULTATION)
        │
        ├── Prescribe medicine ──► pharmacy or nurse (after the line is paid)
        ├── Request scan ─────────► radiology (after payment)  ─┐
        ├── Order lab tests ──────► laboratory (after payment) ─┤
        │                                                       │
        │      consultation pauses to AWAITING_RESULTS ◄────────┘
        │      doctor resumes when results are released
        │
        ├── Admit to ward (bed-day charges)
        │
        ▼
Complete & close visit (blocked while any scan or lab test is outstanding)
        │
        ▼
Billing: payment, receipts, credit exceptions
```

Emergencies (priority 3 or emergency visit type) skip the pre-pay gate. Each emergency bypass is
recorded with a reason, appears in the billing bypass queue, and is cleared only by billing.

---

## Modules

### Core patients (`core_patients`)
Patient registry and demographics, duplicate detection and merge, guardian and family links,
consent records (including emergency consent with a reason), allergy management (required at
registration, with explicit "none known" and "unknown" options), staff self-registration and approval,
and the printable patient record.

### Triage and queue (`triage_queue`)
Encounter (visit) creation, triage vitals, priority (routine, urgent, resuscitation), the live
waiting board, reassignment between on-duty doctors, and the queue state machine:
`AWAITING_PAYMENT → WAITING → IN_CONSULTATION → AWAITING_RESULTS → IN_CONSULTATION → DISCHARGED`
(with `ADMITTED` for ward patients).

### Doctors' portal (`doctors_portal`)
Doctor dashboard, consultation room (notes and diagnosis, prescriptions, scans, laboratory tests,
admission, completion), formulary search, external prescriptions with an explicit acknowledgement
of responsibility, and cancellation of unpaid scans.

### Pharmacy (`pharmacy`)
Formulary and stock, serial-tracked stock units with batch and expiry, goods receipt, dispensing
(oral) and nurse administration (injections and IV), allergy checks with documented overrides,
stock adjustments and write-offs, and automatic expiry. Medicines are released only after payment.

### Radiology (`radiology`)
Scan worklist, image upload with SHA-256 file hashing, written reports, and report submission
refused for unpaid scans. Images are served only to roles with radiology view access, and every
view is audited.

### Laboratory (`lab`)
A catalogue of tests with specimens, reference ranges, critical thresholds and prices. Doctors
order tests from a modal in the consultation room. Each test is a line with its own charge.
Workflow: order → sample received (paid lines only) → results entered (numeric, positive/negative
or text, with flags) → written report of at least 100 characters → second-person verification and
release → printed report. Released results can be amended, with the original value kept.

### Wards (`wards`)
Wards, beds and their statuses, admissions with planned stay length, discharges, a bed history
log, and daily occupancy.

### Billing (`billing`)
Provisional charges created at the point of service, posting by a billing officer (never by the
person who created the charge), per-line payment allocation, manual and M-Pesa payments with
reference checks, receipts, account statements, credit exceptions, and the emergency bypass queue.

### Dashboard (`dashboard`)
Overview cards built from live data, the audit log, the data manager (superuser), the admin panel,
and the full data export with charts (superuser).

### Duty (`duty`)
Clock-in and clock-out by room, on-duty lists used to assign doctors, and automatic clock-out at logout.

### Notifications (`notifications`)
In-app notifications by role or by person, with a badge that polls the server.

### Maternity (`maternity`)
Births and deliveries, child registration, guardian linking, and immunisation schedules.

### Device trust (`device_trust`) *(planned)*
Browser enrolment with single-use links bound to the browser that first opens them, trusted-browser
management, revocation, and one account per browser. Not yet implemented.

---

## Payments and the pre-pay gate

Bills are placed by the clinical and service sectors, then posted (approved) for payment by a billing
officer, and then paid. Posting and payment are reserved for billing officers.

- **Fees:** a one-time registration fee per patient, and a consultation fee per visit, both
  charged when the visit opens.
- **Per-line release:** a medicine, scan or lab test is processed only when its own charge is
  fully paid. Partial payment does not release the line.
- **Allocation order:** fee lines first, then blocking lines (medicines, scans, lab tests), then
  the rest, oldest first. Emergency visits allocate oldest first.
- **Bills belong to the patient, not the payer.** The payer is whoever hands over the money: the
  patient, a registered guardian, or another adult with name, relationship and ID number. Minors can
  pay for themselves.
- **Credit exceptions:** only a billing officer can release a line without payment, with a written
  reason. Administrators cannot.
- **Cancellation:** an unpaid, unperformed item can be cancelled with a reason; its charge is voided.
  A paid item needs a refund through billing first.
- **Money is never silently released.** Every bypass, credit exception and cancellation is audited.

Charges are created with idempotency keys (`med:`, `scan:`, `lab:`, `reg:`, `consult:`), so a
repeated request cannot create a second charge.

---

## Audit and data export

**Audit log.** Every action writes an `AuditLog` row with the actor, the action and a description.
The IP address is recorded, but it is taken from the connection and should be treated as
informational until proxy handling is hardened (see [Security](#security)). Model saves and deletes
in monitored apps are also recorded, and login and logout are recorded by signal.

**Full data export** (superuser only, from **Data export & charts**, or the command below):

- Every model in every installed app, including audit and history tables, as one CSV per table
  inside a passphrase-protected AES-256 zip.
- Secret fields (password hashes, OTP keys, device and token hashes, session data) are never
  exported.
- A `manifest.json` lists row counts and file hashes.
- The admin re-enters their password and sets a passphrase. The passphrase is never stored.
- Each export writes `EXPORT_STARTED` and `DATA_EXPORTED` audit rows, including the zip's SHA-256.
- Exports are rate-limited, deleted from the server after download, and capped on the web. Larger
  exports use the management command.
- Charts show daily revenue, payments by method, visits, registrations and orders.

Treat every export as patient data. Store it under the same controls as the records.

---

## Management commands

| Command | Purpose |
|---|---|
| `python seed_db.py` | Creates or updates departments, allergen classes and allergens, wards and beds, and the laboratory catalogue. Safe to re-run. Creates no patients or staff. |
| `python manage.py createsuperuser` | Creates an administrator account. |
| `python manage.py migrate` | Applies database migrations. |
| `python manage.py collectstatic --noinput` | Copies static files into `staticfiles/` for WhiteNoise to serve. Run after deployment and after changing `static/`. |
| `python manage.py check` | Validates the URL configuration, templates and circular imports. Run after any change. |
| `python manage.py check --deploy` | Checks production security settings. Run before going live. |
| `python manage.py export_all_data --actor <username>` | Full audited export of every table, run from the server shell. See [Full data export](#full-data-export-export_all_data). |
| `python manage.py expire_stock --actor <username>` | Writes off pharmacy units past their expiry date. |
| `python manage.py reconcile_stock` | Checks that stock totals match serial-level doses. |
| `python manage.py open_balance --expiry YYYY-MM-DD --actor <username>` | Creates opening serials for stock that has no serial records. |

### Full data export: `export_all_data`

Exports every model, including the audit log, login history and simple-history tables, into one
AES-256 encrypted zip. Secret fields are never included.

**Syntax**

```bash
python manage.py export_all_data --actor <username> [--out PATH] [--include-files]
```

**Arguments**

| Argument | Required | Description |
|---|---|---|
| `--actor <username>` | Yes | Username of an existing superuser. Any other account is refused. The actor is recorded in the audit log. |
| `--out PATH` | No | Where to write the zip. Default: a timestamped file `hcms-export-YYYYMMDD-HHMMSS.zip` in `EXPORT_DIR` (`private_exports/` in the project root). The folder is created if it doesn't exist. |
| `--include-files` | No | Also include uploaded scan images and reports. Produces a much larger zip. Without it, the manifest lists the file paths and hashes only. |

**What it asks for**

1. `Your password:`: the actor's own login password. Typed hidden. Wrong passwords are refused and logged.
2. `Export passphrase (min 12 chars):`: a passphrase you choose for this export. It encrypts the zip and is not stored anywhere.
3. `Repeat passphrase:`: must match the one above.

**Passphrase**

The passphrase is the password that unlocks the zip. Choose it when you run the command, and keep it
apart from the file. It cannot be recovered. A lost passphrase means the export cannot be opened.

**Output**

- A zip in `EXPORT_DIR`, with permissions 600.
- One CSV per model under `data/`, plus `metrics/finance_daily.csv` and `metrics/operations_daily.csv`.
- `manifest.json` with row counts per table and the SHA-256 of every included file.
- Two audit entries: `EXPORT_STARTED` before the build and `DATA_EXPORTED` after it, with the zip's
  SHA-256. A failure writes `EXPORT_FAILED`.

**Unzipping on Arch Linux**

The zip uses AES encryption, which the standard `unzip` tool does not support. Use `7z` from the
`p7zip` package:

```bash
sudo pacman -S p7zip                                   # once
7z x hcms-export-YYYYMMDD-HHMMSS.zip -o./hcms-export   # prompts for the passphrase
```

`7z` prompts for the passphrase without echoing it, so it stays out of shell history. The `-o` option
sets the destination folder. Omit it to extract into the current folder.

After extracting, open `hcms-export/manifest.json` and check the row counts against the tables you
expect.

**Handling**

The zip contains patient records. Store it under the same controls as the records themselves, delete
copies once they're no longer needed, and never send the passphrase with the file.

---

## Testing

Test files are written per module under `tests/`. Run them with:

```bash
pytest -v
```

No automated test run has been completed yet. Some test files were written alongside their feature
packs and have not been run against the current code. Run the suite before relying on any module.

Coverage areas to build out and keep green:

- Device gate: unenrolled requests, single-use and expired links, cross-browser refusal, revocation
- Pre-pay gate: pharmacy, radiology and laboratory refuse unpaid lines and pass once paid
- Allocation: fee-first ordering, emergency oldest-first ordering, partial payment
- Laboratory: amend and verify rules, the 100-character report minimum, critical notification
- Data export: secret fields absent, manifest counts equal file rows, admin-only access
- Permissions: each role sees and does only what its capabilities allow

---

## Security

- **Device gate** *(planned)*. Untrusted browsers will get a single "not enrolled" page. Enrolment
  links will be hashed at rest, single use, expire after 24 hours by default, and work only in the
  browser that opened them first.
- **Secrets.** Read from `.env` or the environment, never from the code. `DJANGO_DEBUG` must be
  `False` on any server that real users reach.
- **Sessions and cookies.** Secure, HTTP-only cookies; SameSite Lax. Enable
  `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` in production (see
  [Planned settings](#planned-settings)).
- **Passwords.** Argon2id, minimum length 12, common-password and numeric-only checks.
- **Ownership checks.** Scan and lab cancellations, result verification and amendments check who did
  the earlier step. The same person cannot verify their own work.
- **Injection and access.** ORM queries only; templates escape output; file serving checks the
  capability before opening a file.
- **Honeypot and rate limits** on public staff self-registration.
- **Client IP addresses.** The audit log records the IP from the connection. Behind a reverse proxy,
  this must be read from `X-Forwarded-For` only from trusted proxies, or it can be spoofed. Harden
  this before relying on IP addresses as evidence.
- **Planned, not yet enforced:** second factor (TOTP) for administrators and device-link issuers.
  `django-otp` is installed; enforcement is a later step.
- **URL explorer.** A development tool that lists every route. It exists only when `DEBUG=True`, and
  it appears in the sidebar only for superusers in that mode.

To report a vulnerability, do not open a public issue. Contact the maintainers privately (see
[Contributing](#contributing)).

---

## Operations

### Backups

- Schedule regular `pg_dump` backups of the database. For example:

  ```bash
  pg_dump -U hcms_app -Fc hospital_cms > /backups/hospital_cms-$(date +%F).dump
  ```

- Back up `MEDIA_ROOT` (the `media/` folder with scan images and reports) on the same schedule.
- Store backups off the server, and encrypt them. Backups contain patient data.
- Test a restore on a separate machine before go-live, and at least once a quarter after.

### Static files

WhiteNoise serves files from `staticfiles/`, which `collectstatic` builds from `static/`. Run
`collectstatic` after every deployment and after changing anything under `static/`.

The templates currently load Tailwind, Alpine.js, htmx and Chart.js from CDNs. For an isolated LAN,
download those files into `static/` and point the templates at them, so the system works with no
internet access. Until that is done, pages that use these libraries will not render fully without
internet access.

### Time zone

The server clock must be correct and synchronised (NTP). Time-based rules (device link expiry,
payment windows, TOTP when enabled) depend on it. The application time zone is `Africa/Nairobi`.

### Logs

Keep the audit log and the Django application log. Do not log passwords, passphrases or session
values. Keep the gunicorn and Nginx logs for the same retention period.

### Retention

The application does not delete clinical records. Retention and destruction are operational decisions
for the hospital.

### Updating the application

```bash
cd /srv/hcms
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py check --deploy
sudo systemctl restart hcms
```

Take a database backup before running `migrate`.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `ImproperlyConfigured: Set DJANGO_SECRET_KEY in your .env file` | `.env` is missing, or the variable is blank. Copy `.env.example` and fill it in. |
| Static files (CSS, JavaScript) don't load with `DEBUG=False` | `collectstatic` hasn't been run, or WhiteNoise isn't in `MIDDLEWARE`. Run `collectstatic --noinput`. |
| `DisallowedHost` error | The hostname isn't in `DJANGO_ALLOWED_HOSTS`. Add it and restart. |
| `CSRF verification failed` on forms over HTTPS or a tunnel | The `https://` origin isn't in `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| `ModuleNotFoundError: argon2` | Install `argon2-cffi`. |
| Export fails with `Export passphrase must be at least 12 characters` | Use a longer passphrase. The minimum is set by `EXPORT_MIN_PASSPHRASE`. |
| Migration error on `allergen_class` or `allergenclass.id` | An older migration history is still present. Do not edit applied migrations on a database with data. Ask the maintainers before resetting. |
| `unzip` reports `unsupported compression method 99` | The export is AES-encrypted. Use `7z`, see [Unzipping on Arch Linux](#unzipping-on-arch-linux). |

---

## Project layout

```
hospital_system/      settings, root URLs, permissions, navigation, middleware
core_patients/        patients, registry, consent, allergies, staff approval
triage_queue/         encounters, vitals, queue states, triage board
doctors_portal/       doctor dashboard, consultation room, prescribing
pharmacy/             formulary, stock units, dispensing, administration
radiology/            scan orders, images, reports
lab/                  laboratory catalogue, orders, results, verification, amendments
wards/                wards, beds, admissions, occupancy
billing/              charges, payments, allocation, receipts, credit, bypass queue
dashboard/            overview, audit log, data manager, export and charts
duty/                 clock-in, rooms, on-duty lists
notifications/        in-app notifications
maternity/            births, children, immunisation
device_trust/         browser enrolment and trusted-browser management (planned)
templates/            shared and per-app templates
static/               static source files (served through staticfiles/ with WhiteNoise)
staticfiles/          collected static files (generated; not committed)
private_exports/      encrypted data exports (generated; not committed)
tests/                automated tests
seed_db.py            production structure seed (no patient or staff data)
.env.example          template for the environment file
```

---

## Known limitations

- Sex- and age-specific laboratory reference ranges are not modelled; each test has one range.
- Laboratory panels (bundles with one price) are not modelled; each test is priced separately.
- Sample collection at the bedside is not modelled; samples are received at the laboratory.
- Microbiology culture, blood bank and histopathology workflows are not implemented.
- Single-person release of laboratory results is deliberately refused until the feature is built.
- Automatic cancellation of unpaid scans and tests after a time window is planned, not implemented.
- Inpatient billing (bed days, ward medicines on the admission bill) is not complete.
- The device gate is not implemented; any browser that can reach the server can sign in.
- Administrator second-factor enforcement is not enabled.
- Client IP addresses in the audit log need hardening before they are used as evidence.
- Offline front-end assets are not yet vendored into `static/`.
- Reference ranges, prices and bed-day tariffs in the seed are placeholders for hospital review.
- Some test files have not been run against the current code.

---

## Contributing

1. Create a branch from `main`.
2. Keep changes inside the app they belong to. Cross-app calls go through that app's services module.
3. Add or update tests with each change.
4. Run `python manage.py check` and `pytest` before opening a pull request.
5. Do not commit `.env`, database dumps, exports, `staticfiles/`, `media/` or scan images. The
   `.gitignore` excludes them.
6. Describe in the pull request which roles and permissions the change affects.

For security issues, contact the maintainers privately; do not file them publicly.

---

## Licence

To be decided by the hospital and the maintainers before the repository is made public.
Until then, all rights are reserved.
