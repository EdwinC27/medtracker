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
- Dose reminders through **Windows notifications** (these work with the browser
  closed) and browser notifications.
- Suspend, resume, complete, edit and delete medications.
- Browse active, completed and suspended medications with their full dose
  history.
- Record medical appointments with notes, the treatment prescribed and the
  follow-up date.
- Configurable appointment reminders: 3 days, 1 day and 3 hours before.
- Link appointments to medications, navigable in both directions.
- A fully bilingual interface, **English / Español**, defaulting to the
  browser's language.
- A global default time for the first dose of the day.

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

There are **two channels**, and they play different roles:

| Channel | How it works | Works with the browser closed? |
|---|---|---|
| **Windows** | The background scheduler sends a toast through `winotify`. | **Yes** — this is the primary channel. |
| **Browser** | The page polls for pending reminders every 30 s and shows them with the Notification API. | No, but **it shows you what you missed** when you next open it (last 12 hours). |

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

### Notification language

Notifications are written in the language chosen in *Settings*. When that is set
to "Automatic", Windows toasts follow the operating system's language — there is
no browser involved at the moment they are sent.

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
2. Turns every unmarked dose more than N minutes late into *Missed* (N is
   configurable, 120 by default).
3. Finds doses that are due and not yet notified → writes a row in
   `notifications`.
4. Finds appointment reminders that are due and unsent → writes another row.
5. Sends the pending Windows toasts.
6. Deletes notifications older than 30 days.

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
| `medications` | medication, dose, form, dates, frequency, first dose time, status |
| `medication_doses` | every calculated dose with its status (`scheduled` / `taken` / `skipped` / `missed`) |
| `appointments` | appointments, notes, prescribed treatment, follow-up date |
| `appointment_reminders` | each appointment's reminders (3 days / 1 day / 3 hours) |
| `appointment_medications` | many-to-many link between appointments and medications |
| `notifications` | persistent queue of generated alerts |
| `settings` | single row holding all preferences |

Backup: copy the `data\` folder. To start over, close the app and delete
`data\medtracker.db` (along with its `.db-wal` / `.db-shm` files).

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
- **Creating** a medication generates the complete treatment.
- **Editing** the schedule only touches **future** doses. A dose already marked
  as taken, skipped or missed is never modified or deleted.
- **Suspending** or **completing** removes future unmarked doses.
- **Resuming** regenerates them from now until the end date.
- A dose is **never** set to *Taken* automatically. The only automatic
  transition is *Scheduled → Missed*.

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
| `tests/test_api.py` | the full HTTP flow and that data survives an application restart |

## 11. Project layout

```text
C:\ProyectoPersonal
├── app/
│   ├── main.py                 entry point (web + scheduler)
│   ├── config.py               paths, options, constants
│   ├── database/db.py          SQLite engine, sessions, pragmas
│   ├── models/models.py        data model
│   ├── routes/
│   │   ├── api.py              JSON API
│   │   ├── pages.py            HTML pages
│   │   └── deps.py             language resolution
│   ├── services/
│   │   ├── scheduling.py       dose calculation (the core logic)
│   │   ├── medications.py      create/edit/statuses/doses
│   │   ├── appointments.py     appointments and reminders
│   │   ├── settings_service.py preferences
│   │   ├── dashboard.py        dashboard data
│   │   ├── textformat.py       server-side date and dose formatting
│   │   └── errors.py           domain errors
│   ├── notifications/
│   │   ├── scheduler.py        APScheduler thread
│   │   ├── dispatcher.py       what is due and how it is announced
│   │   └── windows.py          Windows toasts (winotify)
│   ├── i18n/                   en.json, es.json + helpers
│   ├── templates/              HTML (Jinja2)
│   ├── static/css|js|img|uploads
│   └── utils/timeutil.py       local time
├── data/                       medtracker.db and logs (created automatically)
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
