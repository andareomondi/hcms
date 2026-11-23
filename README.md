# HCMS: Hospital Clinical Management System

A modular Django application for running a hospital's outpatient, emergency, ward, pharmacy,
radiology, laboratory and billing work in one system. It is built as a modular monolith: each
clinical or administrative function is a Django app, and they share one database and one
permissions layer.

The system is designed to run on a hospital's local network. Every change is audited, and money is
never silently released.

> **Status:** under active development. Browser authorisation (approved browsers, a browser key,
> email codes with backup codes, audit logging and admin alerts) is implemented and tested. Several
> other modules are complete in code but not yet covered by an automated test run. See
> [Known limitations](#known-limitations).

---

## Contents

- [Key principles](#key-principles)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation (development)](#installation-development)
- [Configuration](#configuration)
- [Docker](#docker)
- [Production setup](#production-setup)
- [First run](#first-run)
- [Browser authorisation and sign-in](#browser-authorisation-and-sign-in)
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

1. **Nothing clinical or financial is deleted.** Corrections are new rows that record who made
   them and why. Released lab results are amended, not overwritten. Payments are append-only.
2. **Every state change is audited.** Views call `log_action` with a named `AuditAction`.
   Login and logout are logged by signal handlers.
3. **Permissions are capabilities, not job titles.** Views declare `@requires(Cap.X)`.
   Templates use `{% if "X" in caps %}`. Administrators can view, not act clinically.
4. **Money gates care.** Medicines, scans and lab tests are released only when their line is
   fully paid, the visit is an emergency bypass, or billing has approved a credit exception with
   a reason. Ward medicines are billed to the ward stay and settled at discharge.
5. **Second-person control on results.** A laboratory result is verified and released by a
   different person from the one who entered it.
6. **Admission is a doctor's decision.** Only a doctor can admit a patient to a bed, from a
   consultation, with a written reason of at least 100 characters. Ward staff cannot assign beds.
7. **Unapproved browsers see nothing.** Every page is refused for a browser an administrator has not approved, and a copied cookie is not enough (see [Browser authorisation and sign-in](#browser-authorisation-and-sign-in)).

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
| Application server | Gunicorn (production and container) |

**Apps are modular.** Each clinical or administrative function is a Django app. Apps communicate
through services and signals, not direct model access.

Apps are listed in `hospital_system/settings.py`:

`core_patients`, `triage_queue`, `doctors_portal`, `pharmacy`, `radiology`, `lab`, `wards`,
`billing`, `dashboard`, `duty`, `notifications`, `maternity`

The `access` app (browser authorisation) is in `INSTALLED_APPS`, and its middleware runs after the staff-profile middleware.

**Offline use.** The front-end libraries are meant to be served locally from `static/`. See
[Static files](#static-files) for the current state.

---

## Requirements

- Python 3.12 or newer (developed on 3.14)
- PostgreSQL 14 or newer (developed on 18)
- The packages in `requirements.txt`, including `gunicorn` (production), `Pillow` (images),
  `argon2-cffi`, `python-dotenv`, `whitenoise` and `pyzipper`

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
sudo -u postgres createuser hcms_app --pwprompt
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

To check the production server locally, see [Production setup](#production-setup) step 7.

---

## Configuration

Secrets and environment-specific values are read from a `.env` file in the project root, or from
real environment variables. Real environment variables take precedence. `.env` is never committed.

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"   # generate DJANGO_SECRET_KEY
```

### Environment variables

| Variable | Required | Purpose | Example |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Cryptographic signing. Unique and private. | 50+ random characters |
| `DJANGO_DEBUG` | No (default `False`) | `True` for local development only | `True` |
| `DJANGO_ALLOWED_HOSTS` | Yes | Comma-separated hostnames the server answers to | `hcms.hospital.local,192.168.1.10` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | If behind HTTPS or a tunnel | Comma-separated origins with scheme | `https://hcms.hospital.local` |
| `ALLOW_STAFF_REGISTRATION` | No (default `False`) | Enables the public staff request form. Keep `False` on any reachable server. | `False` |
| `DB_NAME` | Yes | PostgreSQL database name | `hospital_cms` |
| `DB_USER` | Yes | PostgreSQL user | `hcms_app` |
| `DB_PASSWORD` | Yes | PostgreSQL password | from your password manager |
| `DB_HOST` | No (default `127.0.0.1`) | PostgreSQL host | `127.0.0.1` |
| `DB_PORT` | No (default `5432`) | PostgreSQL port | `5432` |
| `HCMS_EMAIL_BACKEND` | Yes in production | Email backend for sign-in codes and admin alerts. Default prints to the console. | `django.core.mail.backends.smtp.EmailBackend` |
| `HCMS_EMAIL_HOST` | Yes with SMTP | Mail server | `smtp.hospital.local` |
| `HCMS_EMAIL_PORT` | No (default `25`) | Mail server port | `587` |
| `HCMS_EMAIL_USER`, `HCMS_EMAIL_PASSWORD` | If the server requires login | Mail account | from your password manager |
| `HCMS_EMAIL_TLS` | No (default `1`) | Use TLS to the mail server | `1` |
| `HCMS_FROM_EMAIL` | No | Sender address | `HCMS <no-reply@hospital.local>` |
| `HCMS_TRUST_PROXY` | No (default `0`) | Set to `1` only when a reverse proxy sets `X-Forwarded-For`. Otherwise client addresses can be forged. | `1` |

Write `.env` lines as `NAME=value`, with no spaces around `=`. A line such as
`ALLOW_STAFF_REGISTRATION = False` is read under a different name and is ignored, so the default
applies.

A missing required variable stops Django at startup with a message that names the variable.

### Other settings

Set in `hospital_system/settings.py`:

| Setting | Value | Purpose |
|---|---|---|
| `EXPORT_MAX_WEB_ROWS` | `500000` | Largest export allowed from the web page. Larger exports use the command. |
| `EXPORT_DIR` | `BASE_DIR / "private_exports"` | Private folder for export files. Outside `MEDIA_ROOT`. |
| `EXPORT_MIN_PASSPHRASE` | `12` | Minimum export passphrase length |

Tariffs (consultation fee, registration fee, scan prices, bed-day rates) are set in
`billing/prices.py` and the ward records. Laboratory test prices are in the laboratory catalogue,
set through `seed_db.py` or the data manager. All of these are placeholders for hospital review.

### Settings to add before going live

These are not yet in `settings.py`. Add them before any server that real users reach:

| Setting | Purpose |
|---|---|
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | Cookies only over HTTPS. `True` in production. |
| `SECURE_PROXY_SSL_HEADER` | Needed when behind a proxy or Render, so Django sees HTTPS |
| `HTTPS` in front of the app | Required for browser authorisation. Browsers create their security key only on secure connections, so plain HTTP blocks sign-in. |

---

## Docker

The project ships a `Dockerfile` at the project root. The image contains the application code,
the Python dependencies and the collected static files. It does not contain the database, uploaded
files, exports or any real secret.

Build from the project root, where the Dockerfile and `manage.py` are:

```bash
docker build -t hcms:dev .
```

Run it against an external PostgreSQL database, passing configuration at run time:

```bash
docker run --rm -p 8000:8000 \
  -e DJANGO_SECRET_KEY="<generated key>" \
  -e DJANGO_DEBUG=False \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e DB_NAME=hospital_cms -e DB_USER=hcms_app -e DB_PASSWORD="<password>" \
  -e DB_HOST=host.docker.internal -e DB_PORT=5432 \
  hcms:dev
```

Migrations and seeding are run as separate, deliberate steps, not on container start:

```bash
docker run --rm <same -e options> hcms:dev python manage.py migrate
docker run --rm <same -e options> hcms:dev python seed_db.py
```

Never put real secrets in the Dockerfile or the image. On Render, set them as environment variables
in the service dashboard. Uploaded files (`media/`) and exports (`private_exports/`) must live on a
persistent volume, not inside the image.

---

## Production setup

Follow these steps on the server, in order.

### 1. Prepare the server

- A Linux server with a fixed address on the hospital LAN, and the clock synchronised by NTP.
- A dedicated system user, for example `hcms`. Do not run the application as root.
- PostgreSQL installed. The application time zone is `Africa/Nairobi`.

### 2. Get the code and install dependencies

```bash
sudo -u hcms git clone <repository-url> /srv/hcms
cd /srv/hcms
sudo -u hcms python3 -m venv venv
sudo -u hcms venv/bin/pip install -r requirements.txt
```

`gunicorn` is listed in `requirements.txt`.

### 3. Create the production `.env`

```bash
sudo -u hcms cp .env.example .env
sudo chmod 600 /srv/hcms/.env
```

Set:

- `DJANGO_DEBUG=False`
- `DJANGO_SECRET_KEY` to a newly generated key. Never reuse the development key.
- `DJANGO_ALLOWED_HOSTS` to the server's hostname and LAN address only.
- `DJANGO_CSRF_TRUSTED_ORIGINS` to the `https://` address users type into the browser.
- `ALLOW_STAFF_REGISTRATION=False`
- `DB_PASSWORD` to a strong, unique password.

### 4. Create the database

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

Run `seed_db.py` once in production. It is safe to re-run, but it overwrites the placeholder
reference ranges and prices, so only re-run it deliberately.

### 6. Check the deployment

```bash
venv/bin/python manage.py check --deploy
```

Resolve every warning before going live.

### 7. Run the application server

```bash
venv/bin/gunicorn hospital_system.wsgi:application --bind 127.0.0.1:8000 --workers 3
```

WhiteNoise serves the static files, so nothing else is needed for them. To test Gunicorn locally,
run `DJANGO_DEBUG=False python manage.py collectstatic --noinput` first, and set
`DJANGO_ALLOWED_HOSTS` to include `127.0.0.1`.

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

Nginx terminates HTTPS and forwards to Gunicorn on `127.0.0.1:8000`:

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

### 10. Schedule backups

See [Backups](#backups). Test a restore before go-live.

### 11. Go-live checklist

- [ ] `DJANGO_DEBUG=False`
- [ ] A new `DJANGO_SECRET_KEY`
- [ ] `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` set to the production address
- [ ] `ALLOW_STAFF_REGISTRATION=False`
- [ ] `manage.py check --deploy` run and warnings resolved
- [ ] Secure cookie and proxy settings added (see [Settings to add before going live](#settings-to-add-before-going-live))
- [ ] Database password changed from any value shared during development
- [ ] `collectstatic` run after the last code change
- [ ] Gunicorn under systemd; reverse proxy serving HTTPS; port 8000 closed to the network
- [ ] `.env` readable only by the `hcms` user
- [ ] Backups scheduled and one restore tested
- [ ] A superuser created, password stored in the hospital's password manager

---

## First run

Create the first administrator on a fresh system:

```bash
python seed_db.py
python manage.py createsuperuser
```

Sign in with that account, then create the other staff accounts through **Staff requests**, or
through the data manager.

**Authorise the first browser.** The system refuses every page until a browser is approved, so the
first browser is approved from the server:

```bash
python manage.py approve_first_browser --username <administrator>
```

Open `/access/redeem/` in the browser you want to use, and enter the code the command printed. The
code works once and expires after 30 minutes. See [Browser authorisation and sign-in](#browser-authorisation-and-sign-in).

---

## Browser authorisation and sign-in

A person can use HCMS only from a browser an administrator has approved. The browser holds a random
token in a cookie and a private key that never leaves it. Both are needed, so a copied cookie
does not work in another browser.

### What the user sees

| Step | What happens |
|---|---|
| Unapproved browser opens any page | A bare 403 with no page content. Curl and Postman get the same. |
| Staff member opens `/access/enrol/` | Enters username and password. The reply is always "an administrator has been asked", so nothing is revealed. |
| Administrator approves at `/access/browsers/` | A one-time code is shown once, valid 30 minutes. |
| Staff member opens `/access/redeem/` and enters the code | The browser creates its key pair, stores the private part, and sends the public part. The browser is now approved for 90 days. |
| Staff member signs in at `/accounts/login/` | Password, then an email code (valid 2 minutes). |
| First sign-in only | Confirming the email code finishes setup and shows 10 backup codes once. Save them. |
| Later sign-ins | Email code again, unless the browser was verified in the last 12 hours. |
| Email unavailable | Enter a backup code instead. Each code works once. |

### Rules

- **One account per browser.** A second person cannot sign in on a browser that is already in use
  or approved for someone else. The form says so, and the attempt is audited.
- **Shared workstations** are fine: several staff members can use one computer, each in their own
  browser.
- **Wrong codes.** Three wrong email codes lock the browser until an administrator unlocks it.
  Administrators are alerted.
- **Changed address.** If the browser's IP address changes, the 12-hour trust window ends and the
  user enters a code again.
- **Logging out** does not revoke the browser.
- **Expiry.** Approval lasts 90 days, then the browser must be approved again.
- **Cleared cookies** mean the browser is unknown. An administrator approves it again.
- **Administrators cannot approve their own browser.** The first browser is the only exception, and
  only through the command below.

### Administrator tasks

- **Approve a browser:** `/access/browsers/`, then Approve. Give the code to the person in person or
  by phone. Do not send it by email.
- **Revoke a browser:** Revoke. The browser is refused at once.
- **Unlock a browser:** Unlock, after a lockout.
- **Watch the audit log:** Dashboard, audit log. Browser and sign-in events are recorded there.
- **Read alerts:** security alerts appear in the in-app notifications, and the same alerts are emailed to administrators.

### Emergency access

Use only when no other administrator can act.

1. Open `/access/emergency/` on the browser to be authorised. This page does not need an approved
   browser.
2. Enter an administrator's username and password, then the code emailed to that administrator.
3. A 24-hour pass is shown once. Open `/access/redeem/` on the browser to authorise and enter the pass.
   It works once.
4. Each cycle allows three passes. After that, a developer must run
   `python manage.py reset_emergency_allowance`.
5. Every start, issue and use is audited and alerted.

### First browser (bootstrap)

On a new installation, nobody has an approved browser yet. The developer runs, on the server:

```bash
python manage.py approve_first_browser --username <administrator>
```

The command prints a one-time code, valid 30 minutes. It refuses to run once any approved browser
exists, and it works only for an active administrator. The code works once.

### Script injection in the pages

The browser check is a small script, `static/js/hcms_proof.js`. It is loaded in three places:

| Where | How | Why |
|---|---|---|
| Every page that extends `templates/base.html` | `<script src="{% static 'js/hcms_proof.js' %}" defer>` in the `<head>` | Keeps the browser's proof current while the page is open. |
| Standalone access pages (`templates/access/*.html`) | `{% load static %}` and the script before `</body>` | These pages do not extend the base layout. |
| The browser-check page, served with the 403 | Inline HTML in `access/middleware.py` (`CHECK_PAGE`) | When the proof has expired, this page refreshes it and reloads. |

What the script does:

- **Ordinary pages:** loads the browser key from IndexedDB, signs a fresh challenge from
  `/access/challenge/`, and posts it to `/access/proof/`. It repeats every 2 minutes and when the tab
  becomes visible again. A proof cookie lasts 5 minutes.
- **Check page:** refreshes and reloads, at most twice. If the refresh fails, it shows "This browser
  could not be verified".
- **Redeem page:** an inline script creates the key pair (non-exportable), stores the private key,
  and sends only the public key with the code. The key is never sent to the server.

The script stores nothing on the server and sends nothing else. Browsers without the key see the
verification message.

### Troubleshooting sign-in

| Symptom | Likely cause | What to do |
|---|---|---|
| Bare 403 on every page | Browser is not approved, was revoked, or its approval expired | Ask an administrator to approve it again. |
| "This browser could not be verified" | Cookie copied from another browser, or key missing | Use the approved browser, or ask for a new approval. |
| Code page says "could not be verified" immediately after redeeming | Browser cannot create keys (plain HTTP, or an old browser) | Use HTTPS and a current browser. |
| Email code never arrives | Email settings not configured, or spam filter | Check `HCMS_EMAIL_*`. Use a backup code meanwhile. |
| "Browser is locked" | Three wrong codes | An administrator unlocks it at `/access/browsers/`. |
| "Another staff member is signed in" | A previous session is still open in this browser | That person must sign out first. |

---

## Roles and permissions

Each staff profile has one role. Each role maps to a set of capabilities in
`hospital_system/permissions.py`.

| Role | Main responsibilities | Cannot |
|---|---|---|
| Reception | Register patients, open visits, run the triage board, manage bed status (cleaning, isolation) | Clinical notes, prescriptions, admissions, payments |
| Triage nurse | Record vitals and complaint, set priority, assign an on-duty doctor | Finance, consultations |
| Doctor | Call patients, write notes, prescribe, order scans and lab tests, admit to a bed, complete visits, approve discharge | Finance, reception and triage work |
| Nurse | Give injections and IV medicines, request and confirm ward discharge, manage bed status | Finance, consultations, admissions |
| Pharmacist | Stock and formulary, dispense oral medicines | Clinical notes, finance |
| Radiologist | Upload images, write reports | Finance, consultations |
| Lab technician | Receive samples, enter results and the written report | Verify or release results |
| Lab supervisor | Verify and release results, amend released results, manage tests | Verify a result they entered |
| Billing officer | Post charges, take payments, approve credit exceptions, clear emergency bypasses | Clinical work |
| Administrator (role) | Staff approvals, data manager, audit log | Consultations, reception, triage, pharmacy, nursing |
| Superuser | All capabilities, data export, URL explorer in development | — |

Every role also sees the **Role guide** page in the sidebar.

---

## Patient journey

```
Register patient ──► Open visit (registration + consultation fees at the till)
        │
        ▼
Triage (vitals, complaint, priority, doctor) ──► Queued (AWAITING_PAYMENT until fees are paid)
        │
        ▼
Doctor calls patient (WAITING → IN_CONSULTATION)
        │
        ├── Prescribe medicine ──► pharmacy or nurse (after the line is paid, or billed to the ward stay)
        ├── Request scan ─────────► radiology (after payment)  ─┐
        ├── Order lab tests ──────► laboratory (after payment) ─┤
        │                                                       │
        │      consultation pauses to AWAITING_RESULTS ◄────────┘
        │      doctor resumes when results are released
        │
        ├── Admit to ward (doctor only, reason ≥ 100 characters)
        │       └─ opens a WARD_STAY encounter; bed-day charges go to the stay
        │
        ▼
Complete & close visit (blocked while any scan or lab test is outstanding)
        │
        ▼
Billing: payment, receipts, credit exceptions

Ward stay (separate from the visit):
  Admitted ──► Request discharge (ward staff) ──► Doctor approves ──► Discharged
                                                  (admitting doctor confirms a second time)
```

A ward patient who needs the doctor again is seen on an **inpatient review** visit (`INPATIENT`),
which closes when the review finishes. The stay stays open until discharge.

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
Visit creation, triage vitals, priority (routine, urgent, resuscitation), the live waiting board,
reassignment between on-duty doctors, and the queue states:
`AWAITING_PAYMENT → WAITING → IN_CONSULTATION → AWAITING_RESULTS → IN_CONSULTATION → DISCHARGED`.
Ward patients are not held in the queue; their stay is tracked on the ward.

Visit types: Outpatient, Emergency, Observation, Maternity, Antenatal, Delivery, Child health,
In-patient review (`INPATIENT`, only for an admitted patient), and Ward stay (`WARD_STAY`, opened
by the system on admission, not at reception).

### Doctors' portal (`doctors_portal`)
Doctor dashboard, consultation room (notes and diagnosis, prescriptions, scans, laboratory tests,
admission, completion), formulary search, external prescriptions with an explicit acknowledgement
of responsibility, and cancellation of unpaid scans.

### Pharmacy (`pharmacy`)
Formulary and stock, serial-tracked stock units with batch and expiry, goods receipt, dispensing
(oral) and nurse administration (injections and IV), allergy checks with documented overrides,
stock adjustments and write-offs, and automatic expiry. Medicines are released only after payment,
or when billed to the ward stay. A scan that does not match the required dose is refused so it can
be corrected; an order is closed as stock-short only when the hospital has no usable stock left.

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
Wards, beds and their statuses, admissions made by a doctor from a consultation, a 100-character
reason on every admission, the discharge request and approval flow, a bed history log, and daily
occupancy. Bed assignment is not done from the ward screen; it happens only through admission.

### Billing (`billing`)
Provisional charges created at the point of service, posting by a billing officer (never by the
person who created the charge), per-line payment allocation, manual and M-Pesa payments with
reference checks, receipts, account statements, credit exceptions, and the emergency bypass queue.
Ward stays are billed and settled as one account; an unpaid balance blocks discharge.

### Dashboard (`dashboard`)
Overview cards built from live data, the audit log, the data manager (superuser), the admin panel,
and the full data export with charts (superuser).

### Duty (`duty`)
Clock-in and clock-out by room, on-duty lists used to assign doctors, and automatic clock-out at
logout.

### Notifications (`notifications`)
In-app notifications by role or by person, with a badge that polls the server.

### Maternity (`maternity`)
Births and deliveries, child registration, guardian linking, and immunisation schedules. Vaccines
are checked against recorded allergies before any dose is given.

### Browser authorisation (`access`)
Approved browsers, the browser key and proof, email and backup codes, emergency passes, the
administrator's browser screen, and the audit and alert rules. See
[Browser authorisation and sign-in](#browser-authorisation-and-sign-in).

---

## Payments and the pre-pay gate

- **Fees:** a one-time registration fee per patient, and a consultation fee per visit, both
  charged when the visit opens. Inpatient reviews and child health visits carry no consultation fee.
- **Per-line release:** a medicine, scan or lab test is processed only when its own charge is
  fully paid. Partial payment does not release the line.
- **Ward medicines:** charged to the ward stay and settled at discharge, not at the till.
- **Allocation order:** fee lines first, then blocking lines (medicines, scans, lab tests), then
  the rest, oldest first. Emergency visits allocate oldest first.
- **Bills belong to the patient, not the payer.** The payer is the patient, a registered guardian,
  or another adult with name, relationship and ID number.
- **Credit exceptions:** only a billing officer can release a line without payment, with a written
  reason.
- **Cancellation:** an unpaid, unperformed item can be cancelled with a reason; its charge is voided.
  A paid item needs a refund through billing first.
- **Money is never silently released.** Every bypass, credit exception and cancellation is audited.

Charges are created with idempotency keys (`med:`, `scan:`, `lab:`, `reg:`, `consult:`, `bed:`), so
a repeated request cannot create a second charge.

---

## Audit and data export

**Audit log.** Every action writes an audit row with the actor, the action and a description. The IP
address is recorded from the connection and should be treated as informational until proxy handling
is hardened (see [Security](#security)).

**Full data export** (superuser only, from **Data export & charts**, or the command below):

- Every model in every installed app, as one CSV per table inside a passphrase-protected AES-256 zip.
- Secret fields (password hashes, OTP codes, browser and backup-code hashes, session data) are never exported.
- A `manifest.json` lists row counts and file hashes.
- The admin re-enters their password and sets a passphrase. The passphrase is never stored.
- Each export writes audit rows, including the zip's SHA-256.
- Exports are rate-limited, deleted from the server after download, and capped on the web. Larger
  exports use the command.

Treat every export as patient data.

---

## Management commands

| Command | Purpose |
|---|---|
| `python seed_db.py` | Creates or updates departments, allergen classes and allergens, wards and beds, and the laboratory catalogue. Safe to re-run. Creates no patients or staff. |
| `python manage.py createsuperuser` | Creates an administrator account. |
| `python manage.py migrate` | Applies database migrations. |
| `python manage.py collectstatic --noinput` | Copies static files into `staticfiles/` for WhiteNoise. Run after deployment and after changing `static/`. |
| `python manage.py check` | Validates configuration, URLs, templates and circular imports. Run after any change. |
| `python manage.py check --deploy` | Checks production security settings. Run before going live. |
| `python manage.py export_all_data --actor <username>` | Full audited export. See [Full data export](#full-data-export-export_all_data). |
| `python manage.py expire_stock --actor <username>` | Writes off pharmacy units past their expiry date. |
| `python manage.py reconcile_stock` | Checks that stock totals match serial-level doses. |
| `python manage.py approve_first_browser --username <admin>` | Issues a one-time code to authorise the first browser. Works only while no approved browser exists. |
| `python manage.py reset_emergency_allowance` | Restarts the three emergency passes per cycle. Run by a developer, not by staff. |

### Full data export: `export_all_data`

```bash
python manage.py export_all_data --actor <username> [--out PATH] [--include-files]
```

- `--actor` must be an existing superuser. Other accounts are refused.
- `--out` sets the zip path. Default: a timestamped file in `private_exports/`.
- `--include-files` also includes scan images and reports. Produces a much larger zip.

It asks for your password, then a passphrase of at least 12 characters, entered twice. The
passphrase encrypts the zip and is not stored anywhere. A lost passphrase cannot be recovered.

The zip uses AES encryption. Use `7z` to open it (the standard `unzip` tool does not support it):

```bash
7z x hcms-export-YYYYMMDD-HHMMSS.zip -o./hcms-export
```

The zip contains patient records. Store it under the same controls as the records, and never send
the passphrase with the file.

---

## Testing

Each app has its own tests. Run the whole suite with:

```bash
python manage.py check
python manage.py test --noinput
```

`--noinput` avoids a prompt about deleting a leftover test database, which can otherwise hang the
run. The suite runs on PostgreSQL, the same database as production.

Expected result: **269 passed and 1 skipped**. The skipped test is the real-browser test, which
needs Playwright and Chromium. To run it, install Playwright in the environment and point
`HCMS_CHROMIUM` at a Chromium binary:

```bash
HCMS_CHROMIUM=/path/to/chrome python manage.py test access.test_browser_flow --noinput
```

Browser authorisation is switched on for the access tests and off for the rest of the suite. The
switch is `BROWSER_AUTHORISATION_ENFORCED` in `hospital_system/settings.py`. It is off only during a
`manage.py test` run.

Coverage areas to build out:

- Ward flow: admission rules (doctor only, reason length), discharge request and token-based approval, outstanding balance blocking discharge
- Intake: inpatient review only for admitted patients, no outpatient visits for admitted patients, stays excluded from triage
- Pre-pay gate: pharmacy, radiology and laboratory refuse unpaid lines and pass once paid
- Vaccine allergy gate: a recorded allergy stops the dose before stock is touched
- Allocation: fee-first ordering, emergency oldest-first ordering, partial payment
- Laboratory: amend and verify rules, the 100-character report minimum, critical notification
- Data export: secret fields absent, manifest counts equal file rows, admin-only access
- Permissions: each role sees and does only what its capabilities allow
- Browser authorisation: unapproved and copied cookies refused, proof and replay checks, email codes,
  backup codes, lockout, trust window, IP change, one staff login per browser, emergency passes,
  bootstrap command, audit entries and admin alerts (`access`)
- Real browser (optional): approval, key creation, first-login enrolment and a copied cookie in a
  second browser (`access/test_browser_flow.py`)

---

## Security

- **Browser authorisation.** Only approved browsers reach the system. Each approved browser holds a
  non-exportable key, and a copied cookie is not enough. See
  [Browser authorisation and sign-in](#browser-authorisation-and-sign-in).
- **Sign-in codes and backup codes.** Email code at every login (outside the 12-hour trust window),
  three wrong codes lock the browser, and backup codes are single-use.
- **Audit and alerts.** Browser, sign-in, code, IP-change and emergency events are audited. Admins are
  alerted in the app and by email.
- **Secrets.** Read from `.env` or the environment, never from the code. `DJANGO_DEBUG` must be
  `False` on any server that real users reach.
- **Sessions and cookies.** Secure, HTTP-only cookies; SameSite Lax. Enable
  `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` in production.
- **Passwords.** Argon2id, minimum length 12, common-password and numeric-only checks.
- **Ownership checks.** Scan and lab cancellations, result verification and amendments check who did
  the earlier step. The same person cannot verify their own work. A discharge approved by the
  admitting doctor needs a second, separate confirmation within five minutes.
- **Injection and access.** ORM queries only; templates escape output; file serving checks the
  capability before opening a file.
- **Honeypot and rate limits** on public staff self-registration.
- **Client IP addresses.** Behind a reverse proxy, the IP must be read from `X-Forwarded-For` only
  from trusted proxies, or it can be spoofed.
- **Second factor.** Email codes with backup codes are enforced for every staff account. An
  authenticator app (TOTP) is not implemented.
- **URL explorer.** A development tool that exists only when `DEBUG=True`, and only for superusers.

To report a vulnerability, do not open a public issue. Contact the maintainers privately.

---

## Operations

### Backups

- Schedule regular database dumps, for example:

  ```bash
  pg_dump -U hcms_app -Fc hospital_cms > /backups/hospital_cms-$(date +%F).dump
  ```

- Back up `media/` (scan images and reports) on the same schedule.
- Store backups off the server, encrypted. Backups contain patient data.
- Test a restore on a separate machine before go-live, and at least once a quarter after.

### Static files

WhiteNoise serves files from `staticfiles/`, which `collectstatic` builds from `static/`. Run
`collectstatic` after every deployment and after changing anything under `static/`.

The templates currently load Tailwind, Alpine.js, htmx and Chart.js from `static/`. Check that
every file is present there before deploying to a network with no internet access.

### Time zone

The server clock must be correct and synchronised (NTP). The application time zone is
`Africa/Nairobi`.

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
| Static files don't load with `DEBUG=False` | `collectstatic` hasn't been run. Run `collectstatic --noinput`. |
| `DisallowedHost` error | The hostname isn't in `DJANGO_ALLOWED_HOSTS`. Add it and restart. |
| `CSRF verification failed` over HTTPS or a tunnel | The `https://` origin isn't in `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| Staff registration form is open when it shouldn't be | `ALLOW_STAFF_REGISTRATION` was written with spaces around `=`, so it was ignored. Use `ALLOW_STAFF_REGISTRATION=False`. |
| `ModuleNotFoundError: argon2` | Install `argon2-cffi`. |
| `ModuleNotFoundError: PIL` | Install `Pillow`. |
| Export fails with a passphrase error | Use a passphrase of at least 12 characters. |
| `unzip` reports `unsupported compression method 99` | The export is AES-encrypted. Use `7z`. |
| Docker build cannot find `Dockerfile` | The file must be named exactly `Dockerfile` (capital D, lowercase rest) at the project root. |
| Docker `permission denied` on the socket | Your user isn't in the `docker` group. Run `sudo usermod -aG docker $USER`, then log in again. |

---

## Project layout

```
hospital_system/      settings, root URLs, permissions, navigation, middleware
core_patients/        patients, registry, consent, allergies, staff approval
triage_queue/         visits, vitals, queue states, triage board, intake rules
doctors_portal/       doctor dashboard, consultation room, prescribing, admission
pharmacy/             formulary, stock units, dispensing, administration
radiology/            scan orders, images, reports
lab/                  laboratory catalogue, orders, results, verification, amendments
wards/                wards, beds, admissions, discharge request and approval, occupancy
billing/              charges, payments, allocation, receipts, credit, bypass queue
dashboard/            overview, audit log, data manager, export and charts
duty/                 clock-in, rooms, on-duty lists
notifications/        in-app notifications
maternity/            births, children, immunisation
access/               browser authorisation, browser key, email codes, backup codes, emergency access
templates/            shared and per-app templates
static/               static source files, including js/hcms_proof.js (browser check)
staticfiles/          collected static files (generated; not committed)
private_exports/      encrypted data exports (generated; not committed)
tests/                automated tests
seed_db.py            hospital structure seed (no patient or staff data)
Dockerfile            container build (project root)
.dockerignore         files kept out of the image
.env.example          template for the environment file
```

---

## Known limitations

- Sex- and age-specific laboratory reference ranges are not modelled; each test has one range.
- Laboratory panels (bundles with one price) are not modelled; each test is priced separately.
- Sample collection at the bedside is not modelled; samples are received at the laboratory.
- Microbiology culture, blood bank and histopathology workflows are not implemented.
- Single-person release of laboratory results is refused until the feature is built.
- Automatic cancellation of unpaid scans and tests after a time window is planned, not implemented.
- Vaccine dosing with a recorded allergy has no override path yet; the dose is refused.
- Nurse vitals on a ward stay are recorded on the review visit, not on a single ward chart.
- An authenticator app (TOTP) is not implemented; sign-in uses email codes and backup codes.
- A stolen proof cookie and token cookie together work in another browser for up to 5 minutes. The
  browser key blocks longer use.
- Refusals of requests from unapproved browsers are not logged one by one, to avoid flooding the log.
- The real-browser test is optional and needs Playwright, which is not in `requirements.txt`.
- Browser keys need HTTPS. Over plain HTTP the key cannot be created, so no browser can be approved.
- Client IP addresses in the audit log need hardening before they are used as evidence.
- Reference ranges, prices, bed-day tariffs and the immunisation schedule are placeholders for hospital review.
- Some test files have not been run against the current code.

---

## Contributing

1. Create a branch from `main`.
2. Keep changes inside the app they belong to. Cross-app calls go through that app's services module.
3. Add or update tests with each change.
4. Run `python manage.py check` and `python manage.py test` before opening a pull request.
5. Do not commit `.env`, database dumps, exports, `staticfiles/`, `media/` or scan images. The
   `.gitignore` and `.dockerignore` exclude them.
6. Describe in the pull request which roles and permissions the change affects.

For security issues, contact the maintainers privately; do not file them publicly.

---

## Licence

To be decided by the hospital and the maintainers before the repository is made public.
Until then, all rights are reserved.
