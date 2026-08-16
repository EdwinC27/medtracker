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
- A **Today** screen: what is due today, what is next, what is overdue and which
  treatments are ending soon, in one place.
- A **calendar** in month, week and day view, showing doses, appointments and
  the days treatments start and end, with filters by kind, medication or doctor.
- **Snooze a reminder** by 10, 30 or 60 minutes. The dose keeps its scheduled
  time; only the reminder moves.
- **Entering a treatment that is already under way** keeps the doses that came
  before you added it, as history rather than as failures: no reminders are sent
  for them, they never turn into *Missed*, and they do not count against your
  adherence.
- **Treatment progress** on the medication screen — day 3 of 10 — measured in
  calendar days between the dates you entered, and a day-by-day dose timeline.
- **Create a medication from a previous one**: the description is copied, the
  dates and history are not.
- A **global search** over medications, doctors and appointments. It only reads:
  a search result is always a link, never an action.
- A **notification centre**: every reminder the app generated, read and unread,
  with an unread count on the bell.
- **Backups**: automatic on a schedule you choose, manual on demand, with a
  retention limit and a restore that always copies the current database first.
- **Export** to CSV, JSON or PDF, and **import** a JSON export back.
- **Light, dark or follow-the-system** appearance, and a layout that works on a
  phone.

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

## 5. The screens

| Screen | What it is for |
|---|---|
| **Today** | The home screen. Today's doses in order with their *Taken* / *Skipped* / *Remind me later* buttons, today's appointments, the next dose, the next appointment, anything overdue and any treatment about to end. |
| **Calendar** | Month, week or day. Doses, appointments and the days treatments start and end, each on its own day. Filter by kind, by medication or by doctor; click anything to open it. |
| **Medications** | The list, filtered by status, and the button to add one. A new medication can be started from a previous one: the description is copied, the dates and the history are not. |
| **Medication detail** | Everything about one treatment: its data, its progress in days, what is due today, the counts, and the full dose timeline grouped by day with an upcoming / past filter. |
| **Doctors** | The directory, and one screen per doctor with their contact details, their last and next visit, and every medication that came out of their appointments. |
| **Appointments** | Past and upcoming visits, their reminders, the medications prescribed at them and the follow-up links. |
| **History** | Two tabs: the *medical timeline* — every appointment in order with its doctor, medications and follow-ups — and the medication history. |
| **Notifications** | Everything the application has generated, read and unread. Reached from the bell. |
| **Search** | One box over medications, doctors and appointments. Every result is a link. |
| **Settings** | Language, appearance, the first dose time, every reminder switch, e-mail, backups, export and import. |

### Appearance

*Settings → Appearance* offers **light**, **dark** or **follow the system**. The
choice is stored in the database like every other setting, and mirrored into the
browser so a dark theme is already dark on the first paint instead of flashing
white. Changing the select previews it immediately; it is remembered when you
press *Save*.

The layout adapts down to a phone screen, which matters mostly for the "from
your phone, on the same network" case in section 4.

## 6. Notifications

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

### Snoozing a reminder

*Remind me later* on a dose postpones **the reminder**, by 10, 30 or 60 minutes.

The dose itself does not move. A dose scheduled for 10:00 AM and snoozed for 30
minutes still reads "scheduled at 10:00 AM", is still pending, and reminds you
again at 10:30 AM. Nothing about the record of when the dose was due changes,
because that record is the point of the application.

Two consequences worth knowing:

- Snoozing is not "taken". The dose stays pending until you mark it.
- While a snooze is running, the dose is not marked *Missed* — it would have
  thrown away the reminder you asked for. It becomes *Missed* on the first check
  after the snooze has been used up, if you still have not marked it.

*Remind me later* is only offered once the reminders for that dose have started
(30 minutes before it is due). There is nothing to postpone before then.

### Adding a treatment that already started

Entering a medication whose start date is in the past keeps its whole schedule,
including the doses that were due before you added it. Those doses are recorded
as **Before registration**.

The rule is the medication's own registration instant, compared on the date
*and* the time:

```text
dose due before the medication was added  ->  Before registration
anything else                             ->  the normal behaviour
```

So a medication added at 08:30 has its 07:00 and 08:00 doses recorded as
history, and its 09:00 dose scheduled as usual. A medication added *before* its
treatment starts has no history at all.

What that status means in practice:

| | |
|---|---|
| Reminders | None, on any channel. Adding a month-old treatment does not fire a month of notifications. |
| *Missed* | Never. The dose was not yours to miss, and it does not change later just because time passes. |
| Today | Not shown. It is not a task. |
| Calendar | Shown, drawn faded and dashed, so you can see the treatment was already running. |
| Medical timeline | The treatment's own entry says when it started, when you added it and how many doses came first. |
| Medication screen | The whole history, with a *Before registration* filter of its own. |
| Adherence | Excluded from both sides of the count. |

You can still mark a historical dose as *Taken* or *Skipped* if you remember
what happened — it is your record. Undoing that returns it to *Before
registration*, not to the pending queue.

### The notification centre

The bell in the header opens the history of everything the application has
generated, newest first, with an unread count. It is the app's own record and is
independent of delivery: a reminder that Windows refused to show still appears
here, with which channels did and did not deliver it.

Entries older than the *notification history* setting (90 days by default) are
dropped automatically.

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

### One conversation per dose

Every reminder for a single dose arrives in one e-mail thread, and the next dose
starts a new one:

```text
Ryaltris — dose #1 — 23:58            Ryaltris — dose #2 — 07:58
  23:28  in 30 minutes      (new)       07:28  in 30 minutes      (new)
  23:43  in 15 minutes      ↳ reply     07:43  in 15 minutes      ↳ reply
  23:53  in 5 minutes       ↳ reply     ...
  23:58  time to take it    ↳ reply
  00:13  dose pending       ↳ reply
  00:28  pending for 30 min ↳ reply
  01:58  dose overdue       ↳ reply
```

This is real threading, not a shared subject line: the first message of a dose
carries its own `Message-ID`, and every later one carries `In-Reply-To` (the
message before it) and `References` (the whole chain). That is what lets the
subject change from "in 30 minutes" to "dose overdue" without the conversation
coming apart, and what keeps two doses of the same medication — or two different
medications due at the same minute — in separate threads.

The subject names the medication, which dose of the treatment it is, and where
in the sequence you are:

```text
💊 Ryaltris — Dose #1 — in 30 minutes
🔔 Ryaltris — Dose #1 — time to take it
⚠️ Ryaltris — Dose #1 — dose pending
🔴 Ryaltris — Dose #1 — dose overdue
```

The body says the same in full: the medication, the dose, the scheduled time and
date, how long it has been pending, and its status — all in the language the app
is set to, with dates and times formatted the way the rest of the app formats
them.

**Marking the dose stops the conversation.** The status is checked again in the
moment before each message goes out, not when it was queued, so pressing *Taken*
at 23:50 means the 23:53, 23:58, 00:13, 00:28 and 01:58 messages are never sent.
A dose you skipped never receives the overdue message either.

Only the e-mail channel is threaded. Windows toasts and browser notifications
are unchanged.

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

## 7. Changing the language

- By default the browser's language decides: `es-*` → Spanish, anything else →
  English.
- In *Settings → Language* you can pin **English**, **Español** or
  **Automatic**. The choice is stored in the database and is respected
  everywhere, including notifications.
- Every string — menus, forms, statuses, errors, confirmations, notifications,
  dates, empty states — comes from `app/i18n/en.json` and `app/i18n/es.json`.
  No interface text is written inside the code, and a test enforces that.

## 8. How the scheduler works

`app/notifications/scheduler.py` starts an APScheduler thread inside the same
FastAPI process. **Every 60 seconds** it runs one tick
(`app/notifications/dispatcher.py::run_tick`) that:

1. Moves any treatment whose end date has passed to *Completed*.
2. Tops up the doses of open-ended treatments to the rolling horizon.
3. Queues whichever of the six dose reminders are now due, for every dose still
   in the `scheduled` state — a dose you already marked is simply not a
   candidate, which is how marking one cancels the rest.
4. Queues a reminder for every snooze that has just run out.
5. Turns every unmarked dose more than N minutes late into *Missed* (N is
   configurable, 120 by default) and queues the overdue alert — unless a snooze
   on that dose is still running. A dose recorded as *Before registration* is
   not a candidate for any of this: it is not in the pending state, so steps 3
   to 5 never see it.
6. Finds appointment reminders that are due and unsent → queues those too.
7. Sends the pending Windows toasts.
8. Sends the pending e-mails, each one threaded onto the earlier messages for
   its dose, and each one re-checking that the dose is still unresolved first.
9. Takes the scheduled backup, if one is due.
10. Deletes notifications older than the history setting (90 days by default).

Steps 3–6 all write to the same `notifications` table, and steps 7–8 drain it.
Adding e-mail in v2 did not add a second scheduler, and neither did adding
backups in v3: there is still exactly one background worker to start and stop.
The queue is committed before anything is sent, because a toast or an e-mail
cannot be un-sent — the row that records it has to be durable first.

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

## 9. Date, time and timezone

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

## 10. Database

SQLite, a single file: `data\medtracker.db`, created on first start. WAL mode is
on so the scheduler thread can write while the web thread reads.

Tables:

| Table | Contents |
|---|---|
| `doctors` | name, specialty, phone, notes — stored once and only referenced |
| `medications` | medication, dose, form, dates, frequency, first dose time, status |
| `medication_doses` | every calculated dose, its status (*scheduled*, *taken*, *skipped*, *missed* or *before registration*), when it was due and when that status last changed |
| `appointments` | visit, notes, prescribed treatment, `doctor_id`, `follow_up_of_id` |
| `appointment_reminders` | each appointment's reminders (3 days / 1 day / 3 hours) |
| `appointment_medications` | many-to-many link between appointments and medications |
| `notifications` | persistent queue of generated alerts, with a unique `dedupe_key` per event, the read/unread state of the notification centre, and the `Message-ID` each one was e-mailed under |
| `settings` | single row holding all preferences, including the SMTP configuration, the appearance and the backup schedule |

There is no table for the calendar, the timeline, the search or the progress
bar: all four are readings of the tables above, so nothing is stored twice and
nothing can drift out of step.

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

Backup: the application takes its own (section 10.1), or copy the `data\` folder
yourself. To start over, close the app and delete `data\medtracker.db` (along
with its `.db-wal` / `.db-shm` files).

### 10.1 Backups

*Settings → Backup* controls all of it:

| Setting | What it does |
|---|---|
| Automatic backup | On by default. The background scheduler takes one when it is due. |
| Frequency | Daily or weekly, at the time you choose. |
| Keep | How many **automatic** backups to keep — 3, 7, 14 or 30. |
| Location | Where they go. Empty means `data\backups`. Anything else is write-tested before it is accepted. |

Everything is taken with **SQLite's own online backup API**, never by copying
the file. A plain copy of a database that is in use can catch a half-written
page or miss the WAL and produce a backup that looks fine and restores corrupt.

Four kinds of backup exist, and only the automatic ones are ever deleted by the
retention limit:

| Kind | When it is taken |
|---|---|
| Automatic | On the schedule above. |
| Manual | When you press *Back up now*. |
| Safety copy | Immediately before a restore. |
| Before import | Immediately before an import replaces the data. |
| Before the update | By the migration itself, before the schema is changed. |

**Restoring** is never a one-way door: the current database is copied first, so
the state you were in a moment ago is still on disk under a *Safety copy* entry.
A file that is not a readable database, or one written by a newer version of the
application, is refused rather than restored over good data. A backup from an
*older* version is accepted and brought up to the current schema straight after
being restored, so the copy the update itself took is always usable.

### 10.2 Export and import

*Settings → Export* writes a file into `data\exports` and offers it as a
download:

| Format | What you get |
|---|---|
| **CSV** | One file per dataset, or a `.zip` when you pick several. UTF-8 with a BOM, so Excel on Windows shows `á é í ó ú ñ` correctly. |
| **JSON** | The relational shape of the database, ids intact. This is the format the import reads. |
| **PDF** | A printable medical history: medications, doses, doctors, appointments and the timeline. |

The JSON export never contains your SMTP password, and the export files are
temporary — anything older than a day is cleaned up on the way past.

**Import replaces**, it does not merge. An import means "put this machine into
the state that file describes" — a new PC, or a rollback. Merging would have to
guess whether two medications called *Amoxicillin* are the same treatment, and a
wrong guess silently corrupts a medical history.

The flow is always the same: the file is validated, you are shown what it holds
versus what you have now, and only then, on your confirmation, a *Before import*
backup is taken and the data is replaced. A file that is not a MedTracker
export, is from an unsupported version, has a reference pointing at something it
does not contain, or is missing a field the schema requires, is refused before
anything is deleted.

### Upgrading an existing database

The schema is versioned with SQLite's own `PRAGMA user_version` and migrated at
startup by `app/database/migrations.py`. It is idempotent — running it twice
does nothing the second time — and it never drops a row.

Before the first schema change it writes a full copy of the database to
`data\backups\medtracker-pre-v3-<timestamp>.db`, using SQLite's backup API so
an active WAL is included correctly. If anything ever looks wrong, that file is
the exact state you were in beforehand, it is listed in *Settings → Backup* as
*Before the update*, and restoring it brings it forward to the current schema
automatically.

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

What the v2 → v3 migration does — all of it additive, with no table rebuilt:

| Change | What happens to existing data |
|---|---|
| `medication_doses.snoozed_until` added | Starts empty. `scheduled_at` is not touched: a snooze moves the reminder, never the dose. |
| `notifications.read_at` added | Every notification that already existed is marked as read, because it was already shown to you through Windows, the browser or e-mail. The bell starts at zero instead of at three months of history. |
| `settings` gains appearance, history and backup columns | Appearance starts at *follow the system*, history at 90 days, automatic backups on, daily at 01:00, keeping 7, in `data\backups`. |
| Six indexes added | For the queries v3 introduces: doses and medications by date and status, notifications by read state. Nothing about the data changes. |

What the v3 → v4 migration does — **no schema change at all**, only a
correction to data the earlier versions had no way of describing:

| Change | What happens to existing data |
|---|---|
| Doses that predate their own medication's registration | Reclassified from *Missed* (or a never-swept *Scheduled*) to *Before registration*. This is the backlog that a treatment entered late used to produce. |
| Anything you marked yourself | Untouched. The discriminator is `marked_at`: a status you chose is never rewritten, only ones the application set on its own. |
| Doses after the medication was registered | Untouched, whatever their status. A dose you really did miss stays missed. |

What the v4 → v5 migration does — one nullable column, nothing else:

| Change | What happens to existing data |
|---|---|
| `notifications.email_message_id` added | Starts empty. Reminders already sent have no Message-ID and simply do not take part in a thread; everything queued from now on does. |

The migration counts rows before and after each table rebuild and aborts the
whole transaction if the numbers ever disagree, so a partial migration cannot
be committed; it also runs `PRAGMA foreign_key_check` before committing, and the
reclassification asserts that its own row count adds up before it commits. A
database stamped by a *newer* version is never stamped back down.
`tests/test_migration.py` runs it against a database built with the exact v1
DDL, and `tests/test_v3_regressions.py` against one built with the v2 DDL, and
they check all of the above.

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

## 11. Running the tests

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
| `tests/test_email_threads.py` | one conversation per dose: the seven messages chained by Message-ID / In-Reply-To / References, a second dose and a second medication starting their own threads, marking a dose stopping the rest, the language and the localised dates, a deleted dose never handing its thread to the next one, and a failing mail server not stopping the scheduler |
| `tests/test_migration.py` | the v1 → v2 migration against a database built with the exact v1 DDL: no row lost, statuses intact, doctor extracted, idempotent |
| `tests/test_api.py` | the full HTTP flow and that data survives an application restart |
| `tests/test_calendar.py` | doses, appointments and treatment boundaries land on the right days; the filters; month/week/day ranges; the refusal of a window wider than 62 days |
| `tests/test_today.py` | today's doses and appointments, the summary, what is next, what is overdue, treatments ending soon, and an open-ended treatment that has no end date to subtract |
| `tests/test_progress.py` | day 1, the middle, the last day, before the start, after the end, a one-day treatment, and an open-ended one that has no percentage at all |
| `tests/test_snooze.py` | the spec example (10:00 + 30 min → reminds at 10:30, still scheduled at 10:00, still not taken), the three delays, the reminder firing when the snooze runs out, and marking the dose cancelling it |
| `tests/test_search.py` | matches by medication, comment, doctor, occupation, treatment, note, location and exact date — and that searching changes nothing |
| `tests/test_notification_center.py` | history, unread count, marking one or all as read, the unread filter, paging, per-channel delivery |
| `tests/test_backup.py` | creating, listing, retention (automatic only), the location check, the schedule including a missed one, restoring, the safety copy, and the refusal of a file that is not a database |
| `tests/test_export_import.py` | valid CSV/JSON/PDF output, the BOM Excel needs, the password never exported, a full round trip, replace-not-merge, and every way an import file can be refused |
| `tests/test_timeline.py` | order, scope, the doctor and medication filters, and the follow-up links in both directions |
| `tests/test_before_registration.py` | a treatment entered late: which doses become history and which do not, to the minute; that none of them is reminded about, shown in Today, counted as missed or counted against adherence; that all of them appear in the calendar, the timeline and the medication's own history; and the boundary cases of registering before, during and after the start |
| `tests/test_v3_regressions.py` | the defects found reviewing v3: a snooze surviving the overdue sweep, snoozing a dose that is not due, restoring an older-schema backup, the incomplete-import guard, and the timeline being paged |

## 12. Project layout

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
│   │   ├── medications.py      create/edit/statuses/doses/snooze/progress
│   │   ├── doctors.py          the doctor directory
│   │   ├── appointments.py     appointments, reminders and follow-ups
│   │   ├── settings_service.py preferences
│   │   ├── today.py            the Today screen
│   │   ├── calendar_service.py calendar events, bounded by the visible range
│   │   ├── timeline.py         the medical timeline (a reading, not a table)
│   │   ├── search.py           global search, read-only by construction
│   │   ├── backup.py           backups, retention and restore
│   │   ├── export_service.py   CSV, JSON and PDF export
│   │   ├── import_service.py   JSON import (full replace)
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
├── data/                       medtracker.db, logs, backups/ and exports/ (created automatically)
├── scripts/                    install / start / stop / autostart / tests
├── tests/
├── requirements.txt
└── README.md
```

The frontend is plain HTML, CSS and JavaScript with no framework and no build
step: templates carry the skeleton with `data-i18n` attributes, and the
JavaScript fetches the data from the API and renders it. One stylesheet.

## 13. Known limitations

- **The application has to be running** for reminders to appear. If the machine
  is off or the process is stopped, there are no alerts — which is exactly why
  the auto-start task exists.
- Browser notifications only appear while the app is open in a tab. The reliable
  channel with everything closed is the Windows one. Web Push is not used
  because it would need an external service, which is out of proportion for a
  local application.
- No authentication and no encryption: designed for a single user on their own
  machine.
- No sync between devices. Backups are automatic but local: they land on the
  same machine, so a disk failure takes both with it unless you point the backup
  folder at a drive or a synced folder somewhere else.
- The backup folder is not watched. If it becomes unwritable — an unplugged
  external drive — the scheduled backup is logged as failed and skipped, and you
  only see it in *Settings → Backup* or in the log.
- Frequencies are every 4/6/8/12/24 hours. The structure allows adding others
  (`FREQUENCY_OPTIONS` in `app/config.py` plus a matching translation key), but
  there is no "Mondays and Thursdays" style schedule.
- A daylight-saving change preserves the displayed time, not the exact interval
  between doses (see section 9).
- Spanish and English only.
- Seven reminders per dose is a lot of noise if you leave them all on. They are
  all enabled by default because that is what was asked for; turn the ones you
  do not want off in *Settings → Dose reminders*.
- An e-mail thread is only as good as the receiving client: threading follows
  the standard headers, which Gmail, Outlook and Thunderbird all honour, but a
  client that groups purely by subject line will show the seven messages of a
  dose as seven separate items.
- E-mail is sent synchronously inside the scheduler tick, at most ten messages
  per pass. A slow or unreachable SMTP server therefore delays that one tick by
  up to the 20-second socket timeout; it never blocks the web interface.
- The SMTP password is tied to your Windows account by DPAPI. Moving the folder
  to another machine or user means retyping it — see section 6.
- A doctor with appointments cannot be deleted. Delete or move the appointments
  first; this is deliberate, not a missing feature.
- Pictures are stored in `app/static/uploads`; the database backup does not
  include them, so copy that folder too if you care about them. The same is true
  of an export: it carries the picture's file name, not the picture.
- An import **replaces** everything; it never merges. This is deliberate (see
  section 10.2), but it does mean an import is not a way to combine two machines.
- Editing a treatment's start date backwards does not create the doses for the
  days you just added. The past is deliberately left alone — the same rule that
  protects a dose you already marked — so history is built when the medication
  is registered, not when it is edited.
- Marking a historical dose *Taken* moves it into the adherence count, which is
  usually what you want, but it does mean adherence covers "what I told the app"
  rather than "what the app watched".
- The calendar refuses a range wider than 62 days. Month, week and day views all
  fit comfortably inside that; there is no year view.
- Treatment progress is elapsed calendar time between the dates you entered, and
  nothing else. It is not a measure of whether a treatment is working, and an
  open-ended treatment has no percentage at all.
- Search is a plain substring match over names, comments, treatments, notes,
  locations and phone numbers, plus an exact date. There is no fuzzy matching:
  "amoxicilna" finds nothing.
- Windows alerts come from the Python process, so they appear under the
  "MedTracker" application name without a custom icon in the notification
  centre.

## 14. Troubleshooting

| Symptom | What to check |
|---|---|
| `scripts\start.bat` says the environment is missing | Run `scripts\install.bat` first. |
| "Python was not found" | Reinstall Python with *Add python.exe to PATH* ticked. |
| Port 8000 is already in use | Another instance is still alive: run `scripts\stop.bat`. Or change the port with `set MEDTRACKER_PORT=8010` before starting. |
| No Windows toasts | Check Focus assist, check that the option is on in Settings, and try the test notification button. |
| The browser does not notify | The permission is blocked: fix it in the browser's site settings. |
| Settings says the scheduler is stopped | The page is open but the process died; start it again. |
| You want to see what the scheduler did | `data\logs\medtracker.log`. |
| No backups are appearing | Check that *Automatic backup* is on in Settings, that the time you chose has passed, and that the folder shown there is writable. |
| A restore was refused | The file is not a readable database, or it came from a newer version of the application. A backup from an older version is accepted and upgraded automatically. |
| An import was refused | The message says which check failed. Nothing was deleted — the data is only replaced after the file has passed every check. |
| The dark theme did not stick | The Settings select previews it immediately, but it is only remembered once you press *Save*. |
