# MedTracker

A personal organizer for medications, doses and medical appointments, meant to
run locally on Windows.

> **This is not a medical system.** The application only stores and reminds you
> of what you enter yourself. It does not recommend medications, does not
> suggest or change doses, does not diagnose anything and does not give medical
> advice. Always consult your doctor or pharmacist.

---

## 1. What it does

- Record medications: name, picture, dose, unit, quantity per dose, form and
  free-text comments.
- Set a start date, an end date, a frequency (every 4, 6, 8, 12 or 24 hours)
  and the time of the first dose.
- **Generate the whole dose schedule automatically.** You never create doses by
  hand.
- Mark each dose as *Taken* or *Skipped*; a dose left unmarked long enough
  becomes *Missed* on its own.
- Dose reminders at **seven moments around each dose** — 30, 15 and 5 minutes
  before, at the scheduled time, 15 and 30 minutes after, and once more when it
  goes overdue — each one individually switchable.
- Three independent reminder channels: **Windows notifications** (these work
  with the browser closed), **browser notifications** and **e-mail**.
- Suspend, resume, complete, edit and delete medications.
- Browse active, completed and suspended medications with their full dose
  history.
- Keep a **directory of doctors** with their specialty and phone number, and
  see every appointment you have had with each of them.
- Record medical appointments with notes, the treatment prescribed and the
  follow-up date. Each appointment belongs to a doctor.
- Mark an appointment as a **follow-up of an earlier visit** and navigate
  between the two.
- Configurable appointment reminders: 3 days, 1 day and 3 hours before.
- Link appointments to medications, navigable in both directions.
- A fully bilingual interface, **English / Español**, defaulting to the
  browser's language.
- A global default time for the first dose of the day.
- Only three fields are required to add a medication: name, frequency and start
  date. A treatment with no end date simply runs until you stop it.

## 2. Requirements

| Requirement | Version |
|---|---|
| Windows | 10 or 11 |
| Python | 3.11 or newer ([python.org](https://www.python.org/downloads/windows/), with **Add python.exe to PATH** ticked) |
| Browser | A recent Chrome, Edge or Firefox |

No internet is needed (except to install the dependencies the first time), no
Node.js, no external database and no administrator rights.

## 3. Installation

Open a console in `C:\ProyectoPersonal` and run:

```bat
scripts\install.bat
```

That creates a virtual environment in `.venv` and installs everything listed in
`requirements.txt`. You only do this once.

## 4. Running it

### Normal start

```bat
scripts\start.bat
```

Your browser opens at <http://127.0.0.1:8000>. **A single process runs both the
web server and the reminder system** — there is nothing else to start. Leave the
window open (minimised is fine) for as long as you want reminders.

### Start automatically when you sign in to Windows (recommended)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

This registers a Windows scheduled task named `MedTracker` that starts the
application with no console window every time you sign in, so reminders work
even if you never open the browser.

To start it right now without signing out again:

```powershell
Start-ScheduledTask -TaskName MedTracker
```

To remove it:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1
```

### Stopping it

- Started with `start.bat`: press `Ctrl+C` in that window, or just close it.
- Started by the scheduled task (no window): run `scripts\stop.bat`.
- To stop it starting by itself: `scripts\uninstall_autostart.ps1`.

### From your phone, on the same network

The application listens on every interface (`0.0.0.0:8000`). Find your PC's
address with `ipconfig` and open `http://<your-pc-ip>:8000` on the phone. The
first time, Windows will ask you to allow Python on private networks. The
interface is responsive and works well on a phone screen.

> Do not expose the port to the internet. The application has no authentication
> because it is designed for one person on their own machine.

## 5. Notifications

### When a dose is announced

Every scheduled dose produces up to seven reminders. For a dose at 10:00 AM:

| Reminder | Fires at |
|---|---|
| 30 minutes before | 9:30 AM |
| 15 minutes before | 9:45 AM |
| 5 minutes before | 9:55 AM |
| At the scheduled time | 10:00 AM |
| 15 minutes late | 10:15 AM |
| 30 minutes late | 10:30 AM |
| Overdue | 12:00 PM — the dose also becomes *Missed* |

Each one can be switched off on its own in *Settings → Dose reminders*.

**Marking the dose cancels the rest.** The application checks the dose's status
immediately before every reminder, so if you press *Taken* at 9:50 AM, the
9:55, 10:00, 10:15, 10:30 and overdue reminders never fire. The same applies to
*Skipped*.

The overdue delay is the `missed_after_minutes` setting (2 hours by default).
Once a dose is overdue it stops reminding you, keeps its place in the history,
and records both the time it was due (`scheduled_at`) and the time it changed
(`status_changed_at`).

### The three channels

They are completely independent and each has its own switch in Settings:

| Channel | How it works | Works with the browser closed? |
|---|---|---|
| **Windows** | The background scheduler sends a toast through `winotify`. | **Yes** — this is the primary channel. |
| **Browser** | The page polls for pending reminders every 30 s and shows them with the Notification API. | No, but **it shows you what you missed** when you next open it (last 12 hours). |
| **E-mail** | The scheduler sends a message over SMTP with the settings you enter in the app. | **Yes**, and it reaches your phone too. |

The point worth remembering: **reminders do not depend on keeping a browser tab
open.** They depend on the application's process running, and that is what the
scheduled task in section 4 takes care of.

### Setting up Windows notifications

1. Nothing to install beyond `winotify`, which is already in
   `requirements.txt`.
2. In Windows: *Settings → System → Notifications* must be on, and **Focus
   assist / Do not disturb** must be off — otherwise Windows files the alerts
   in the notification centre without showing them.
3. In the app: *Settings → Notifications → Windows notifications*.
4. Check it with the **Send a test notification** button.

If the host cannot support this channel (for example if you run the app outside
Windows), the Settings screen says so and everything else keeps working.

### Setting up browser notifications

1. Open *Settings* and click **Enable browser notifications**.
2. Accept the permission prompt.
3. If you blocked it earlier you have to allow it again in the browser's site
   settings; the Settings screen tells you when that is the case.

### Setting up e-mail

*Settings → E-mail*. Everything is configured inside the application; nothing is
hard-coded and no credentials live in the source.

| Field | Example |
|---|---|
| Recipient e-mail | `you@gmail.com` |
| SMTP host | `smtp.gmail.com` |
| SMTP port | `587` |
| Connection security | STARTTLS (587), SSL/TLS (465), or none |
| SMTP username | `you@gmail.com` |
| SMTP password | an **app password**, not your normal account password |
| Sender e-mail | optional; the username is used when it is empty |

Then tick **E-mail notifications** and press **Send a test e-mail**. If it
fails, the exact SMTP error is shown next to the button — that is what tells a
wrong port apart from a rejected password.

For Gmail you need 2-step verification enabled and a 16-character app password
from your Google account; Gmail rejects your normal password over SMTP.

#### How the password is stored

On Windows the password is encrypted with **DPAPI** (`CryptProtectData`,
reached through `ctypes` — no extra dependency) before it is written to the
database. DPAPI derives the key from your Windows user account, which means:

* only your Windows account, on this machine, can decrypt it;
* copying `medtracker.db` — or a backup of it — to another PC leaves the
  password unreadable;
* nothing readable is ever written to the database, to a log, or to the repo.

The trade-off is deliberate: reinstalling Windows, or moving the folder to
another machine or user account, makes the stored password undecryptable, and
you type it again in Settings. That is expected behaviour, not a failure.

On a system without DPAPI (used for the test-suite and for development) the
value goes into `data/secret_store.json` with owner-only permissions instead.
That is noticeably weaker, and the Settings screen says which mechanism is
active on the machine you are looking at.

### Notification language

Notifications are written in the language chosen in *Settings*. When that is set
to "Automatic", Windows toasts follow the operating system's language — there is
no browser involved at the moment they are sent.

### Never twice

Every reminder carries a unique key of the form `dose:412:before_15` (or
`appointment:7:day_1`), protected by a unique index in the database. Restarting
the application — or the machine — cannot produce the same alert twice, because
the row already exists.

### Catching up after a shutdown

If the application was closed for a while, on startup it only alerts you about
things that came due in the **last 3 hours**. Anything older is marked as
handled without notifying, so starting the app after a weekend does not produce
a wall of stale alerts. Those doses are still visible in the interface as
*Missed*.

## 6. Changing the language

- By default the browser's language decides: `es-*` → Spanish, anything else →
  English.
- In *Settings → Language* you can pin **English**, **Español** or
  **Automatic**. The choice is stored in the database and is respected
  everywhere, including notifications.
- Every string — menus, forms, statuses, errors, confirmations, notifications,
  dates, empty states — comes from `app/i18n/en.json` and `app/i18n/es.json`.
  No interface text is written inside the code, and a test enforces that.

## 7. How the scheduler works

`app/notifications/scheduler.py` starts an APScheduler thread inside the same
FastAPI process. **Every 60 seconds** it runs one tick
(`app/notifications/dispatcher.py::run_tick`) that:

1. Moves any treatment whose end date has passed to *Completed*.
2. Tops up the doses of open-ended treatments to the rolling horizon.
3. Queues whichever of the six dose reminders are now due, for every dose still
   in the `scheduled` state — a dose you already marked is simply not a
   candidate, which is how marking one cancels the rest.
4. Turns every unmarked dose more than N minutes late into *Missed* (N is
   configurable, 120 by default) and queues the overdue alert.
5. Finds appointment reminders that are due and unsent → queues those too.
6. Sends the pending Windows toasts.
7. Sends the pending e-mails.
8. Deletes notifications older than 30 days.

Steps 3–5 all write to the same `notifications` table, and steps 6–7 drain it.
Adding e-mail in v2 did not add a second scheduler.

**Why in the same process rather than in Windows Task Scheduler:** the web
server has to be running anyway, so this way there is one thing to start and
stop, one database connection pool, and no risk of two schedulers notifying you
twice. Windows Task Scheduler is used for one job only — *starting the
application when you sign in* — which is the piece that makes reminders work
without a browser.

**Nothing lives in memory.** The entire state is in SQLite and every tick is
recomputed from scratch, so restarting the app or the machine loses nothing and
duplicates nothing: `notified_at` on each dose and `sent_at` on each reminder
guarantee that every alert is sent exactly once.

## 8. Date, time and timezone

A deliberate design decision, documented on purpose:

- **Everything is stored as naive local wall-clock time — no timezone, no UTC.**
  A 10:00 PM dose is stored as `22:00` and is displayed, compared and notified
  as `22:00`.
- Dose maths adds hours in that same wall-clock space
  (`10:00 → 18:00 → 02:00 → 10:00…`), so **a daylight-saving change does not
  shift your doses**: the 10:00 dose is still at 10:00 the next day. (The price
  of that choice is that on the changeover night the real interval between two
  doses can be 7 or 9 hours instead of 8 — the displayed time is treated as the
  thing that matters.)
- The browser builds dates from their components explicitly, never with
  `new Date("...Z")`, so no offset is ever applied to them.
- If you move the machine to another timezone, stored times are not
  recalculated: they keep referring to the local clock.
- `app/utils/timeutil.py::now_local()` is the single source of "what time is
  it", which makes the behaviour easy to test.

## 9. Database

SQLite, a single file: `data\medtracker.db`, created on first start. WAL mode is
on so the scheduler thread can write while the web thread reads.

Tables:

| Table | Contents |
|---|---|
| `doctors` | name, specialty, phone, notes — stored once and only referenced |
| `medications` | medication, dose, form, dates, frequency, first dose time, status |
| `medication_doses` | every calculated dose, its status, when it was due and when that status last changed |
| `appointments` | visit, notes, prescribed treatment, `doctor_id`, `follow_up_of_id` |
| `appointment_reminders` | each appointment's reminders (3 days / 1 day / 3 hours) |
| `appointment_medications` | many-to-many link between appointments and medications |
| `notifications` | persistent queue of generated alerts, with a unique `dedupe_key` per event |
| `settings` | single row holding all preferences, including the SMTP configuration |

### How the pieces relate

```text
Doctor
  └── Appointment            (an appointment belongs to exactly one doctor)
        ├── Medication       (many-to-many: the same medication can be reviewed
        │                     at several visits, and one visit can prescribe
        │                     several medications)
        └── Appointment      (follow_up_of_id: a later visit can point back at
                              an earlier one)
```

Nothing is copied: an appointment stores `doctor_id`, not the doctor's name and
phone, so correcting a phone number fixes it everywhere at once.

Backup: copy the `data\` folder. To start over, close the app and delete
`data\medtracker.db` (along with its `.db-wal` / `.db-shm` files).

### Upgrading an existing database

The schema is versioned with SQLite's own `PRAGMA user_version` and migrated at
startup by `app/database/migrations.py`. It is idempotent — running it twice
does nothing the second time — and it never drops a row.

Before the first schema change it writes a full copy of the database to
`data\backups\medtracker-pre-v2-<timestamp>.db`, using SQLite's backup API so
an active WAL is included correctly. If anything ever looks wrong, that file is
the exact state you were in beforehand.

What the v1 → v2 migration does:

| Change | What happens to existing data |
|---|---|
| New `doctors` table | One doctor is created per distinct `appointments.doctor_name`, and every appointment is repointed at it. An appointment saved with a blank name gets a single placeholder record so the new foreign key holds. |
| `appointments.doctor_name` removed | Replaced by `doctor_id`; the name now lives only on the doctor. |
| `appointments.follow_up_of_id` added | Starts empty — follow-up links are something you declare, so nothing is guessed. |
| Dose fields and `end_date` become optional | Values are copied across unchanged; the columns simply stop being `NOT NULL`. |
| `medication_doses.status_changed_at` added | Back-filled from `marked_at` where the user had marked the dose. Doses that v1 auto-missed stay `NULL`, which the UI shows as unknown rather than inventing a timestamp. |
| `notifications` gains `kind`, `dedupe_key`, `email_sent_at` | Existing rows get the key `legacy:<id>` so the new unique index can be created. |
| `settings` gains the e-mail and dose-reminder columns | E-mail starts off; the six dose reminders start on; your language and hours are untouched. |

The migration counts rows before and after each table rebuild and aborts the
whole transaction if the numbers ever disagree, so a partial migration cannot
be committed. `tests/test_migration.py` runs it against a database built with
the exact v1 DDL and checks all of the above.

### Medication statuses

| Status | Meaning | Effect |
|---|---|---|
| `active` | In progress | Generates doses and reminders |
| `completed` | The end date passed, or you finished it yourself | No future doses; history untouched |
| `suspended` | You suspended it | No future doses and no alerts; history untouched; can be resumed |

*Suspend* and *Delete* are different things, deliberately kept apart: suspending
**keeps everything**, while deleting removes the medication and its dose history
and always asks for confirmation first.

### Dose calculation rules

- Doses run from the `first dose time` on the `start date`, every `frequency`
  hours, up to the last slot that still falls on the **end date**.
- With **no end date** the treatment is open-ended: doses are generated 60 days
  ahead (`DOSE_HORIZON_DAYS`) and topped up on every scheduler tick, so the
  table never grows without bound and the dashboard always knows the next dose.
- **Creating** a medication generates the complete treatment.
- **Editing** the schedule only touches **future** doses. A dose already marked
  as taken, skipped or missed is never modified or deleted.
- **Suspending** or **completing** removes future unmarked doses.
- **Resuming** regenerates them from now until the end date.
- A dose is **never** set to *Taken* automatically. The only automatic
  transition is *Scheduled → Missed*.

### Required fields

Adding a medication requires only three things, enforced in the backend and not
just by the browser:

```text
Medication name *
Frequency *
Start date *
```

Picture, dose, unit, quantity, form, comments and end date are all optional. A
medication with no dose recorded simply shows its name and frequency.

### Confirmations

Destructive or surprising actions ask first; ordinary ones do not.

| Action | Asks? |
|---|---|
| Delete a medication, a doctor or an appointment | Always |
| Suspend / resume / complete a treatment | Always |
| **Complete a treatment before its end date** | Yes, and the dialog names both dates: "scheduled to end on August 25 — today is August 20" |
| Complete a treatment on or after its end date | No extra warning beyond the normal one |
| **Mark a dose as taken more than 30 minutes early** | Yes, naming the scheduled time and the current time |
| Mark a dose taken within 30 minutes of its time, or any time after it | No — this is the normal case |

The 30-minute rule is exactly `now < scheduled − 30 min`. It lives in
`app/services/scheduling.py`, is covered by the tests, and the server sends the
resulting threshold to the browser so the rule is never re-derived in
JavaScript.

Deleting a doctor who still has appointments is refused rather than cascading,
because cascading would silently take visit history with it.

### The global first dose time

*Settings → Default first dose time* (10:00 by default):

- It is the starting hour for **every new medication** (still overridable per
  medication in the form).
- **Changing it recalculates the upcoming doses of active medications** so they
  start at the new hour — the behaviour chosen for this project. A confirmation
  dialog tells you how many medications will be affected before anything is
  saved.
- Doses already marked, and anything in the past, are **never** touched.
- Suspended and completed medications are left alone.

## 10. Running the tests

```bat
scripts\run_tests.bat
```

or, with the environment active:

```bat
.venv\Scripts\python.exe -m pytest -v
```

The tests use a temporary database and disable the scheduler, so they never
touch your real data. They cover:

| File | What it checks |
|---|---|
| `tests/test_schedule.py` | schedule maths every 4/6/8/12/24 h, boundary dates, that no dose is generated past `end_date`, behaviour across a DST change |
| `tests/test_medications.py` | validation, active/completed/suspended statuses, the *Missed* rule, edits that preserve history |
| `tests/test_settings.py` | the global first dose time, recalculation of active medications, language persistence |
| `tests/test_appointments.py` | the 3-day / 1-day / 3-hour reminders, the link to medications |
| `tests/test_notifications.py` | the scheduler finds what is due, never repeats an alert, renders text in both languages |
| `tests/test_i18n.py` | both catalogs have exactly the same keys, none empty, and no interface text is hard-coded in the JavaScript or in template attributes |
| `tests/test_dose_notifications.py` | the seven reminders fire at −30/−15/−5/0/+15/+30 minutes and at the 2-hour overdue mark, marking a dose cancels the rest, each offset can be switched off, and repeating a tick never duplicates an alert |
| `tests/test_confirmations.py` | the 30-minute rule at 09:00, 09:29, 09:30, 09:45 and 10:00, and the finish-early rule before, on and after the end date |
| `tests/test_doctors.py` | doctor CRUD, the delete guard, and the Doctor → Appointment → Medication chain in both directions |
| `tests/test_follow_ups.py` | an appointment with no follow-up, one that follows an earlier visit, and the refusal to pick a later or identical appointment as the previous one |
| `tests/test_email.py` | the password never stored or returned in clear text, the channel switch, real message content in both languages, and one send per event |
| `tests/test_migration.py` | the v1 → v2 migration against a database built with the exact v1 DDL: no row lost, statuses intact, doctor extracted, idempotent |
| `tests/test_api.py` | the full HTTP flow and that data survives an application restart |

## 11. Project layout

```text
C:\ProyectoPersonal
├── app/
│   ├── main.py                 entry point (web + scheduler)
│   ├── config.py               paths, options, constants
│   ├── database/
│   │   ├── db.py               SQLite engine, sessions, pragmas
│   │   └── migrations.py       versioned schema upgrades + automatic backup
│   ├── models/models.py        data model
│   ├── routes/
│   │   ├── api.py              JSON API
│   │   ├── pages.py            HTML pages
│   │   └── deps.py             language resolution
│   ├── services/
│   │   ├── scheduling.py       dose calculation (the core logic)
│   │   ├── medications.py      create/edit/statuses/doses
│   │   ├── doctors.py          the doctor directory
│   │   ├── appointments.py     appointments, reminders and follow-ups
│   │   ├── settings_service.py preferences
│   │   ├── dashboard.py        dashboard data
│   │   ├── textformat.py       server-side date and dose formatting
│   │   └── errors.py           domain errors
│   ├── notifications/
│   │   ├── scheduler.py        APScheduler thread
│   │   ├── dispatcher.py       what is due and how it is announced
│   │   ├── email.py            the SMTP channel
│   │   └── windows.py          Windows toasts (winotify)
│   ├── i18n/                   en.json, es.json + helpers
│   ├── templates/              HTML (Jinja2)
│   ├── static/css|js|img|uploads
│   └── utils/
│       ├── timeutil.py         local time
│       └── secretstore.py      DPAPI-protected SMTP password
├── data/                       medtracker.db, logs and backups/ (created automatically)
├── scripts/                    install / start / stop / autostart / tests
├── tests/
├── requirements.txt
└── README.md
```

The frontend is plain HTML, CSS and JavaScript with no framework and no build
step: templates carry the skeleton with `data-i18n` attributes, and the
JavaScript fetches the data from the API and renders it. One stylesheet.

## 12. Known limitations

- **The application has to be running** for reminders to appear. If the machine
  is off or the process is stopped, there are no alerts — which is exactly why
  the auto-start task exists.
- Browser notifications only appear while the app is open in a tab. The reliable
  channel with everything closed is the Windows one. Web Push is not used
  because it would need an external service, which is out of proportion for a
  local application.
- No authentication and no encryption: designed for a single user on their own
  machine.
- No sync between devices and no automatic backup (copy the `data\` folder
  yourself).
- Frequencies are every 4/6/8/12/24 hours. The structure allows adding others
  (`FREQUENCY_OPTIONS` in `app/config.py` plus a matching translation key), but
  there is no "Mondays and Thursdays" style schedule.
- A daylight-saving change preserves the displayed time, not the exact interval
  between doses (see section 8).
- Spanish and English only.
- Seven reminders per dose is a lot of noise if you leave them all on. They are
  all enabled by default because that is what was asked for; turn the ones you
  do not want off in *Settings → Dose reminders*.
- E-mail is sent synchronously inside the scheduler tick, at most ten messages
  per pass. A slow or unreachable SMTP server therefore delays that one tick by
  up to the 20-second socket timeout; it never blocks the web interface.
- The SMTP password is tied to your Windows account by DPAPI. Moving the folder
  to another machine or user means retyping it — see section 5.
- A doctor with appointments cannot be deleted. Delete or move the appointments
  first; this is deliberate, not a missing feature.
- Pictures are stored in `app/static/uploads`; they are only backed up if you
  copy that folder too.
- Windows alerts come from the Python process, so they appear under the
  "MedTracker" application name without a custom icon in the notification
  centre.

## 13. Troubleshooting

| Symptom | What to check |
|---|---|
| `scripts\start.bat` says the environment is missing | Run `scripts\install.bat` first. |
| "Python was not found" | Reinstall Python with *Add python.exe to PATH* ticked. |
| Port 8000 is already in use | Another instance is still alive: run `scripts\stop.bat`. Or change the port with `set MEDTRACKER_PORT=8010` before starting. |
| No Windows toasts | Check Focus assist, check that the option is on in Settings, and try the test notification button. |
| The browser does not notify | The permission is blocked: fix it in the browser's site settings. |
| Settings says the scheduler is stopped | The page is open but the process died; start it again. |
| You want to see what the scheduler did | `data\logs\medtracker.log`. |
