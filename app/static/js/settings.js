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
      'windows_notifications', 'browser_notifications', 'medication_reminders',
      'appointment_reminders', 'appt_reminder_days_3', 'appt_reminder_day_1', 'appt_reminder_hours_3',
    ].forEach(function (name) { f[name].checked = Boolean(current[name]); });

    document.getElementById('database-path').textContent = current.database_path;
    document.getElementById('app-version').textContent = current.version;

    await loadStatus();
    showPermission();
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
      'windows_notifications', 'browser_notifications', 'medication_reminders',
      'appointment_reminders', 'appt_reminder_days_3', 'appt_reminder_day_1', 'appt_reminder_hours_3',
    ].forEach(function (name) { payload[name] = f[name].checked; });

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

    await load();
  }

  UI.page(render);
})();
