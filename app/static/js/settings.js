/* Settings page. */
(function () {
  'use strict';

  const el = UI.el;
  let current = null;

  const form = function () { return document.getElementById('settings-form'); };

  async function load() {
    current = await API.get('/api/settings');
    const f = form();

    const select = document.getElementById('set-language');
    select.textContent = '';
    const auto = el('option', null, T.t('settings.language_auto'));
    auto.value = '';
    select.appendChild(auto);
    (current.available_languages || []).forEach(function (language) {
      const option = el('option', null, language.name);
      option.value = language.code;
      select.appendChild(option);
    });
    select.value = current.language || '';

    f.default_first_dose_time.value = current.default_first_dose_time;
    f.ending_soon_days.value = current.ending_soon_days;
    f.missed_after_minutes.value = current.missed_after_minutes;

    [
      'windows_notifications', 'browser_notifications', 'email_notifications',
      'medication_reminders', 'appointment_reminders',
      'appt_reminder_days_3', 'appt_reminder_day_1', 'appt_reminder_hours_3',
      'dose_before_30', 'dose_before_15', 'dose_before_5', 'dose_at_time',
      'dose_after_15', 'dose_after_30', 'dose_overdue',
    ].forEach(function (name) { f[name].checked = Boolean(current[name]); });

    // --- email ---
    f.email_recipient.value = current.email_recipient || '';
    f.email_sender.value = current.email_sender || '';
    f.smtp_host.value = current.smtp_host || '';
    f.smtp_port.value = current.smtp_port || 587;
    f.smtp_username.value = current.smtp_username || '';
    f.smtp_security.value = current.smtp_security || 'starttls';
    f.smtp_password.value = '';
    document.getElementById('smtp-password-state').textContent =
      current.smtp_password_set ? T.t('settings.smtp_password_stored') : '';
    document.getElementById('secret-backend-note').textContent =
      T.t(current.secret_backend === 'dpapi' ? 'settings.secret_dpapi' : 'settings.secret_file');

    updateGraceWarning();
    document.getElementById('database-path').textContent = current.database_path;
    document.getElementById('app-version').textContent = current.version;

    await loadStatus();
    showPermission();
  }

  /* A dose becomes overdue after `missed_after_minutes`, and an overdue dose
     stops reminding. If that delay is shorter than the latest "after" reminder
     the user switched on, that reminder can never fire — say so rather than
     letting it fail silently. */
  function updateGraceWarning() {
    const f = form();
    const box = document.getElementById('grace-warning');
    const latest = [['dose_after_30', 30], ['dose_after_15', 15]]
      .filter(function (pair) { return f[pair[0]].checked; })
      .map(function (pair) { return pair[1]; })[0];
    const grace = Number(f.missed_after_minutes.value || 0);

    if (latest && grace && grace <= latest) {
      box.textContent = T.t('settings.missed_before_offsets', { minutes: latest });
    } else {
      box.textContent = '';
    }
  }

  async function loadStatus() {
    try {
      const status = await API.get('/api/system/status');
      const box = document.getElementById('scheduler-status');
      box.textContent = status.scheduler.running
        ? T.t('settings.scheduler_running', { time: status.scheduler.last_run ? F.time(status.scheduler.last_run) : '—' })
        : T.t('settings.scheduler_stopped');

      const availability = document.getElementById('windows-availability');
      availability.textContent = status.windows_notifications_available
        ? ''
        : T.t('settings.windows_unavailable', { reason: status.windows_unavailable_reason || '—' });
    } catch (e) { /* status is informational only */ }
  }

  function showPermission() {
    const box = document.getElementById('permission-status');
    const permission = Notifications.permission();
    if (permission === 'unsupported') box.textContent = T.t('settings.permission_unsupported');
    else if (permission === 'granted') box.textContent = T.t('settings.permission_granted');
    else if (permission === 'denied') box.textContent = T.t('settings.permission_denied');
    else box.textContent = '';
  }

  async function submit(event) {
    event.preventDefault();
    const f = form();
    UI.clearErrors(f);

    const newTime = f.default_first_dose_time.value;
    if (newTime !== current.default_first_dose_time && current.active_medication_count > 0) {
      const ok = await UI.confirm(
        'confirm.first_dose_change_title', 'confirm.first_dose_change_body',
        { count: current.active_medication_count, time: F.time('2000-01-01T' + newTime) },
        'common.confirm', false
      );
      if (!ok) return;
    }

    const payload = {
      language: f.language.value || null,
      default_first_dose_time: newTime,
      ending_soon_days: f.ending_soon_days.value,
      missed_after_minutes: f.missed_after_minutes.value,
    };
    [
      'windows_notifications', 'browser_notifications', 'email_notifications',
      'medication_reminders', 'appointment_reminders',
      'appt_reminder_days_3', 'appt_reminder_day_1', 'appt_reminder_hours_3',
      'dose_before_30', 'dose_before_15', 'dose_before_5', 'dose_at_time',
      'dose_after_15', 'dose_after_30', 'dose_overdue',
    ].forEach(function (name) { payload[name] = f[name].checked; });

    payload.email_recipient = f.email_recipient.value;
    payload.email_sender = f.email_sender.value;
    payload.smtp_host = f.smtp_host.value;
    payload.smtp_port = f.smtp_port.value;
    payload.smtp_username = f.smtp_username.value;
    payload.smtp_security = f.smtp_security.value;
    // Only send the password when the user actually typed something, so an
    // untouched field keeps whatever is already stored.
    if (f.smtp_password.value) payload.smtp_password = f.smtp_password.value;

    try {
      const saved = await API.put('/api/settings', payload);
      UI.notify.success('message.settings_saved');
      if (saved.recalculated_doses) {
        UI.notify.info('message.doses_recalculated', { count: saved.recalculated_doses });
      }
      await T.reload();      // language may have changed
      await load();
    } catch (err) {
      if (err instanceof API.ApiError && Object.keys(err.fields || {}).length) {
        UI.showErrors(f, err.fields);
        UI.notify.error('error.validation');
      } else {
        UI.notify.error(err);
      }
    }
  }

  async function render() {
    form().addEventListener('submit', submit);

    ['missed_after_minutes', 'dose_after_15', 'dose_after_30'].forEach(function (name) {
      form()[name].addEventListener('change', updateGraceWarning);
      form()[name].addEventListener('input', updateGraceWarning);
    });

    document.getElementById('request-permission').addEventListener('click', async function () {
      await Notifications.requestPermission();
      showPermission();
    });

    document.getElementById('test-notification').addEventListener('click', async function () {
      try {
        const result = await API.post('/api/notifications/test');
        if (Notifications.permission() === 'granted') {
          new Notification(result.title, { body: result.body });
        }
        if (result.windows_sent) UI.notify.success('settings.test_sent');
        else UI.notify.warning('error.windows_notifications_unavailable');
      } catch (err) { UI.notify.error(err); }
    });

    document.getElementById('test-email').addEventListener('click', async function () {
      const box = document.getElementById('email-test-result');
      const button = this;
      button.disabled = true;
      box.textContent = T.t('common.saving');
      try {
        const result = await API.post('/api/notifications/test-email');
        if (result.sent) {
          box.textContent = T.t('settings.test_email_sent', { recipient: result.recipient });
          UI.notify.success('settings.test_email_sent', { recipient: result.recipient });
        } else {
          // The raw SMTP error is what makes a bad host or a rejected password
          // diagnosable, so it is shown next to the translated headline.
          box.textContent = T.t(result.reason || 'settings.test_email_failed') +
            (result.error ? ' — ' + result.error : '');
          UI.notify.error('settings.test_email_failed');
        }
      } catch (err) {
        box.textContent = '';
        UI.notify.error(err);
      } finally {
        button.disabled = false;
      }
    });

    await load();
  }

  UI.page(render);
})();
