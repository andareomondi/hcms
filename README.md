# HCMS: Hospital Clinical Management System

A modular Django application for running a hospital's outpatient, emergency, ward, pharmacy,
radiology, laboratory and billing work in one system. It is built as a modular monolith: each
clinical or administrative function is a Django app, and they share one database and one
permissions layer.

The system is designed to run on a hospital's local network. Access from untrusted devices is
blocked, every change is audited, and money is never silently released.

> **Status:** under active development. Several modules are complete in code but not yet
> covered by an automated test run. See [Known limitations](#known-limitations).

---

## Contents

- [Key principles](#key-principles)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [First run](#first-run)
- [Roles and permissions](#roles-and-permissions)
- [Patient journey](#patient-journey)
- [Modules](#modules)
- [Payments and the pre-pay gate](#payments-and-the-pre-pay-gate)
- [Audit and data export](#audit-and-data-export)
- [Management commands and scripts](#management-commands-and-scripts)
- [Testing](#testing)
- [Security](#security)
- [Operations](#operations)
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
6. **Untrusted browsers see nothing.** A device gate (admin-issued enrolment links) sits in front
   of every page, including the login screen.

---

## Architecture

| Layer | Technology |
|---|---|
| Language and framework | Python, Django 5.2 (LTS) |
| Database | PostgreSQL |
| Front end | Django templates, Tailwind CSS, Alpine.js, htmx |
| Charts | Chart.js (export and dashboard pages) |
| Password hashing | Argon2id (PBKDF2 as fallback) |
| Second factor | `django-otp` installed; enforcement not yet enabled |
| History | `django-simple-history` on selected models |
| Export encryption | `pyzipper` (AES-256 zip) |

Apps are listed in `hospital_system/settings.py`:

`core_patients`, `triage_queue`, `doctors_portal`, `pharmacy`, `radiology`, `lab`, `wards`,
`billing`, `dashboard`, `duty`, `notifications`, `maternity`, `device_trust`

---

## Requirements

- Python 3.12 or newer (developed on 3.14)
- PostgreSQL 14 or newer
- Node.js is **not** required: Tailwind and the front-end libraries are loaded from CDNs
- On an air-gapped LAN, vendor the CDN assets into `static/` before deployment (see [Operations](#operations))

---

## Installation

```bash
# 1. Clone and enter the project
git clone <repository-url> hcms
cd hcms

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create the database
sudo -u postgres createdb hospital_cms
sudo -u postgres createuser hcms_app --pwprompt   # give it a strong password

# 5. Configure the environment (see Configuration)
cp .env.example .env            # then edit .env

# 6. Apply migrations
python manage.py migrate

# 7. Seed the hospital structure (departments, wards, beds, tests, allergens)
python seed_db.py

# 8. Create the first administrator
python manage.py createsuperuser

# 9. Run
python manage.py runserver
```

Open `http://127.0.0.1:8000/`. You will see the device-enrolment page. Enrol the administrator's
browser first (see [First run](#first-run)).

---

## Configuration

**Do not commit secrets.** The settings file currently contains a development secret key and a
database password. Before any shared or deployed use, move every value below into the
environment (for example a `.env` file loaded with `python-dotenv`, or the host's secret store).

| Setting | Purpose | Example |
|---|---|---|
| `SECRET_KEY` | Cryptographic signing. Must be unique and private. | generated, 50+ characters |
| `DEBUG` | Must be `False` on any shared or deployed server | `False` |
| `ALLOWED_HOSTS` | Hostnames and LAN addresses the server answers to | `hcms.hospital.local,192.168.1.10` |
| `CSRF_TRUSTED_ORIGINS` | Origins allowed to post forms (HTTPS only) | `https://hcms.hospital.local` |
| `DATABASES` | PostgreSQL connection | from environment |
| `HCMS_PUBLIC_BASE_URL` | Public origin used to build device-enrolment links in production | `https://hcms.hospital.local` |
| `HCMS_DEVICE_GATE` | Device gate on or off. Keep on. | `True` |
| `DEVICE_LINK_HOURS` | Lifetime of an enrolment link | `24` |
| `DEVICE_TRUST_DAYS` | How long an enrolled browser stays trusted | `90` |
| `DEVICE_MAX_PER_USER` | Enrolled browsers allowed per account | `5` |
| `EXPORT_MAX_WEB_ROWS` | Largest export allowed from the web page | `500000` |
| `EXPORT_DIR` | Private folder for export files (outside media) | `private_exports/` |
| `EXPORT_MIN_PASSPHRASE` | Minimum export passphrase length | `12` |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | Cookies only over HTTPS | `True` in production |

Tariffs (consultation fee, registration fee, scan prices) are in `billing/prices.py`. Laboratory
test prices are in the laboratory catalogue, set through `seed_db.py` or the data manager.

---

## First run

A new system has no enrolled browser, so the first administrator has to be bootstrapped.

1. Run `python seed_db.py` and `python manage.py createsuperuser`.
2. Create a one-time enrolment link from the Django shell on the server:

   ```python
   from datetime import timedelta
   import secrets
   from django.utils import timezone
   from django.contrib.auth.models import User
   from device_trust.models import EnrolmentToken, hash_secret

   admin = User.objects.get(username="YOUR_SUPERUSER")
   plain = secrets.token_urlsafe(32)
   EnrolmentToken.objects.create(user=admin, token_hash=hash_secret(plain), created_by=admin,
                                 expires_at=timezone.now() + timedelta(hours=1))
   print(f"http://127.0.0.1:8000/device/enrol/{plain}/")
   ```

3. Open the link **in the browser you want to trust**, confirm, and sign in.
4. Every further device link is issued from **Administration → Device links**, by a superuser.
   Send links in person or by a private message. They work only in the browser that opens them first.

Create the other staff accounts through **Staff requests** (self-registration requests that a
superuser approves), or through the data manager.

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
| Superuser | All capabilities, device-link issuing, data export | — |

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
consent records (including emergency consent with a reason), allergy management, staff
self-registration and approval, and the printable patient record.

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

### Device trust (`device_trust`)
Browser enrolment with single-use links that are bound to the browser that first opens them,
trusted-browser management, revocation, and one account per browser.

---

## Payments and the pre-pay gate

- **Fees:** a one-time registration fee per patient, and a consultation fee per visit, both
  charged when the visit opens.
- **Per-line release:** a medicine, scan or lab test is processed only when its own charge is
  fully paid. Partial payment does not release the line.
- **Allocation order:** fee lines first, then blocking lines (medicines, scans, lab tests), then
  the rest, oldest first. Emergency visits allocate oldest first.
- **Payer is recorded, not restricted:** the bill belongs to the patient; the payer is whoever
  hands over the money (the patient, a registered guardian, or another adult with name,
  relationship and ID number). Minors can pay for themselves.
- **Credit exceptions:** only a billing officer can release a line without payment, with a
  written reason. Admin cannot.
- **Cancellation:** an unpaid, unperformed item can be cancelled with a reason; its charge is
  voided. A paid item needs a refund through billing first.
- **Money is never silently released.** Every bypass, credit exception and cancellation is audited.

Charges are created with idempotency keys (`med:`, `scan:`, `lab:`, `reg:`, `consult:`), so a
repeated request cannot create a second charge.

---

## Audit and data export

**Audit log.** Every significant action writes an `AuditLog` row with the actor, the IP address,
the action and a description. Model saves and deletes in monitored apps are also recorded, and
login and logout are recorded by signal.

**Full data export** (superuser only, from **Data export & charts**):

- Every model in every installed app, including audit and history tables, as one CSV per table
  inside a passphrase-protected AES-256 zip.
- Secret fields (password hashes, OTP keys, device and token hashes, session data) are never
  exported.
- A `manifest.json` lists row counts and file hashes.
- The admin re-enters their password and sets a passphrase. The passphrase is never stored.
- Each export writes `EXPORT_STARTED` and `DATA_EXPORTED` audit rows, including the zip's SHA-256.
- Exports are rate-limited, deleted from the server after download, and capped on the web;
  larger exports use the management command.
- Charts show daily revenue, payments by method, visits, registrations and orders.

Treat every export as patient data. Store it under the same controls as the records.

---

## Management commands and scripts

| Command | Purpose |
|---|---|
| `python seed_db.py` | Creates or updates departments, allergen classes and allergens, wards and beds, and the laboratory catalogue. Safe to re-run. Creates no patients or staff. |
| `python manage.py createsuperuser` | Creates the first administrator |
| `python manage.py export_all_data --actor <username>` | Full audited export from the server shell (prompts for password and passphrase) |
| `python manage.py expire_stock --actor <username>` | Writes off pharmacy units past their expiry date |
| `python manage.py reconcile_stock` | Checks that stock totals match serial-level doses |
| `python manage.py open_balance --expiry YYYY-MM-DD --actor <username>` | Creates opening serials for stock that has no serial records |

Run `python manage.py check` after any change. It validates the URL configuration and templates
that Django can load, and reports circular-import problems early.

---

## Testing

Test files are written per module under `tests/`. Run them with:

```bash
pytest -v
```

Coverage areas to build out and keep green:

- Device gate: unenrolled requests, single-use and expired links, cross-browser refusal, revocation
- Pre-pay gate: pharmacy, radiology and laboratory refuse unpaid lines and pass once paid
- Allocation: fee-first ordering, emergency oldest-first ordering, partial payment
- Laboratory: amend and verify rules, the 100-character report minimum, critical notification
- Data export: secret fields absent, manifest counts equal file rows, admin-only access
- Permissions: each role sees and does only what its capabilities allow

Some test files were written alongside their feature packs and have not yet been run against the
current code. Run the suite before relying on any module.

---

## Security

- **Device gate.** Untrusted browsers get one "not enrolled" page. Enrolment links are hashed at
  rest, single use, expire after 24 hours by default, and work only in the browser that opened them first.
- **Sessions and cookies.** Secure, HTTP-only cookies; SameSite Lax. Enable `SESSION_COOKIE_SECURE`
  and `CSRF_COOKIE_SECURE` in production.
- **Passwords.** Argon2id, minimum length 12, common-password and numeric-only checks.
- **Ownership checks.** Scan and lab cancellations, result verification and amendments check who
  did the earlier step. The same person cannot verify their own work.
- **Injection and access.** ORM queries only; templates escape output; file serving checks the
  capability before opening a file.
- **Honeypot and rate limits** on public staff self-registration.
- **Planned, not yet enforced:** second-factor (TOTP) for administrators and device-link issuers.
  `django-otp` is installed; enforcement is a later step.

To report a vulnerability, do not open a public issue. Contact the maintainers privately (see
[Contributing](#contributing)).

---

## Operations

- **Backups.** Schedule regular `pg_dump` backups of the database and a copy of `MEDIA_ROOT`
  (scan images). Test a restore before go-live.
- **Time zone.** The server clock must be correct and synchronised (NTP). Time-based rules
  (enrolment expiry, payment windows, TOTP later) depend on it. The application time zone is
  `Africa/Nairobi`.
- **Static assets on an isolated LAN.** The templates load Tailwind, Alpine, htmx and Chart.js
  from CDNs. Without internet access, download them into `static/` and point the templates at them.
- **Serving.** Run behind a reverse proxy with HTTPS (for example Nginx), with `DEBUG=False`, a
  real `ALLOWED_HOSTS`, and `collectstatic` run during deployment.
- **Logs.** Keep the audit log and the Django application log. Do not log passwords, passphrases
  or session values.
- **Retention.** The application does not delete clinical records. Retention and destruction are
  operational decisions for the hospital.

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
device_trust/         browser enrolment and trusted-browser management
templates/            shared and per-app templates
static/               static files (vendor CDN assets here for offline use)
tests/                automated tests
seed_db.py            production structure seed (no patient or staff data)
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
- Administrator second-factor enforcement is not enabled.
- Reference ranges, prices and bed-day tariffs in the seed are placeholders for hospital review.
- Some test files have not been run against the current code.

---

## Contributing

1. Create a branch from `main`.
2. Keep changes inside the app they belong to. Cross-app calls go through that app's services module.
3. Add or update tests with each change.
4. Run `python manage.py check` and `pytest` before opening a pull request.
5. Do not commit `.env`, database dumps, exports or scan images. The `.gitignore` excludes them.
6. Describe in the pull request which roles and permissions the change affects.

For security issues, contact the maintainers privately; do not file them publicly.

---

## Licence

To be decided by the hospital and the maintainers before the repository is made public.
Until then, all rights are reserved.
