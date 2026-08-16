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

    document.getElementById('set-theme').value = current.theme || 'system';
    f.notification_history_days.value = current.notification_history_days;
    f.backup_frequency.value = current.backup_frequency || 'daily';
    f.backup_time.value = current.backup_time || '01:00';
    f.backup_location.value = current.backup_location || '';

    const keep = document.getElementById('set-backup-keep');
    keep.textContent = '';
    (current.backup_keep_options || [3, 7, 14, 30]).forEach(function (value) {
      const option = el('option', null, String(value));
      option.value = value;
      keep.appendChild(option);
    });
    keep.value = String(current.backup_keep);

    f.default_first_dose_time.value = current.default_first_dose_time;
    f.ending_soon_days.value = current.ending_soon_days;
    f.missed_after_minutes.value = current.missed_after_minutes;

    [
      'windows_notifications', 'browser_notifications', 'email_notifications',
      'medication_reminders', 'appointment_reminders',
      'appt_reminder_days_3', 'appt_reminder_day_1', 'appt_reminder_hours_3',
      'dose_before_30', 'dose_before_15', 'dose_before_5', 'dose_at_time',
      'dose_after_15', 'dose_after_30', 'dose_overdue',
      'backup_enabled', 'start_with_windows',
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

    await Promise.all([loadStatus(), loadBackups()]);
    showPermission();
  }

  /* ------------------------------------------------------------- backups */
  const BACKUP_KIND = {
    auto: 'settings.backup_kind_auto', manual: 'settings.backup_kind_manual',
    safety: 'settings.backup_kind_safety', preimport: 'settings.backup_kind_preimport',
    premigration: 'settings.backup_kind_premigration',
  };

  async function loadBackups() {
    const list = document.getElementById('backup-list');
    list.textContent = '';
    let data;
    try {
      data = await API.get('/api/backups');
    } catch (err) { return; }

    document.getElementById('last-backup').textContent = data.last_backup_at
      ? F.dateTime(data.last_backup_at) : T.t('settings.no_backup_yet');
    document.getElementById('set-backup-location').placeholder = data.location;

    if (!data.backups.length) {
      list.appendChild(UI.emptyState('settings.no_backups'));
      return;
    }
    data.backups.forEach(function (backup) {
      const row = el('div', 'dose-row dose-row--scheduled');
      row.appendChild(el('div', 'dose-row__time', F.dateShort(backup.created_at)));
      const main = el('div', 'dose-row__main');
      main.appendChild(el('strong', null, T.t(BACKUP_KIND[backup.kind] || 'settings.backup_kind_manual')));
      main.appendChild(el('div', 'card__meta',
        F.dateTime(backup.created_at) + ' · ' + Math.round(backup.size / 1024) + ' KB'));
      row.appendChild(main);

      const actions = el('div', 'dose-row__actions');
      const restore = el('button', 'btn btn--sm btn--ghost', T.t('settings.restore_backup'));
      restore.type = 'button';
      restore.addEventListener('click', function () { doRestore(backup); });
      actions.appendChild(restore);

      const remove = el('button', 'btn btn--sm btn--ghost', T.t('common.delete'));
      remove.type = 'button';
      remove.addEventListener('click', async function () {
        const ok = await UI.confirm('confirm.delete_backup_title', 'confirm.delete_backup_body',
          { date: F.dateTime(backup.created_at) }, 'confirm.delete_confirm_word');
        if (!ok) return;
        try {
          await API.del('/api/backups/' + encodeURIComponent(backup.name));
          UI.notify.success('message.backup_deleted');
          loadBackups();
        } catch (err) { UI.notify.error(err); }
      });
      actions.appendChild(remove);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  async function doRestore(backup) {
    const ok = await UI.confirm('confirm.restore_title', 'confirm.restore_body',
      { date: F.dateTime(backup.created_at) }, 'settings.restore_backup');
    if (!ok) return;
    try {
      await API.post('/api/backups/restore', { name: backup.name });
      UI.notify.success('message.backup_restored');
      UI.notify.info('message.settings_reload');
      setTimeout(function () { window.location.reload(); }, 1500);
    } catch (err) { UI.notify.error(err); }
  }

  /* ------------------------------------------------------ export/import */
  function chosenDatasets() {
    return Array.prototype.slice
      .call(document.querySelectorAll('[data-dataset]:checked'))
      .map(function (input) { return input.dataset.dataset; });
  }

  async function runExport() {
    const button = document.getElementById('export-run');
    const link = document.getElementById('export-download');
    button.disabled = true;
    link.classList.add('hidden');
    try {
      const result = await API.post('/api/export', {
        format: document.getElementById('set-export-format').value,
        datasets: chosenDatasets(),
      });
      link.href = result.download_url;
      link.setAttribute('download', result.file);
      link.textContent = T.t('export.download') + ' · ' + result.file;
      link.classList.remove('hidden');
      UI.notify.success('message.export_ready');
    } catch (err) {
      UI.notify.error(err);
    } finally {
      button.disabled = false;
    }
  }

  async function runImport() {
    const input = document.getElementById('import-file');
    const box = document.getElementById('import-preview');
    box.textContent = '';
    if (!input.files || !input.files.length) {
      UI.notify.error('validation.import_not_json');
      return;
    }

    const form = new FormData();
    form.append('file', input.files[0]);

    let preview;
    try {
      preview = (await API.upload('/api/import/preview', form)).preview;
    } catch (err) {
      UI.notify.error(err);
      return;
    }

    const summary = el('div', 'card');
    summary.appendChild(el('h3', null, T.t('import.preview_title')));
    summary.appendChild(el('div', 'card__meta',
      T.t('import.exported_at', { date: F.dateTime(preview.exported_at) })));
    const list = el('dl', 'meta-list');
    ['medications', 'doctors', 'appointments', 'medication_doses'].forEach(function (key) {
      list.appendChild(el('dt', null, key));
      list.appendChild(el('dd', null, preview.incoming[key] + '  ←  ' + preview.current[key]));
    });
    summary.appendChild(list);
    summary.appendChild(el('p', 'field__error', T.t('import.replace_warning')));
    box.appendChild(summary);

    const ok = await UI.confirm('confirm.import_title', 'confirm.import_body', {
      medications: preview.current.medications,
      doctors: preview.current.doctors,
      appointments: preview.current.appointments,
    }, 'import.confirm');
    if (!ok) return;

    const payload = new FormData();
    payload.append('file', input.files[0]);
    const withSettings = document.getElementById('import-settings').checked;
    try {
      await API.upload('/api/import?include_settings=' + (withSettings ? 'true' : 'false'), payload);
      UI.notify.success('message.import_done');
      UI.notify.info('message.settings_reload');
      setTimeout(function () { window.location.reload(); }, 1500);
    } catch (err) { UI.notify.error(err); }
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
      theme: document.getElementById('set-theme').value,
      notification_history_days: f.notification_history_days.value,
      backup_frequency: f.backup_frequency.value,
      backup_time: f.backup_time.value,
      backup_keep: f.backup_keep.value,
      backup_location: f.backup_location.value,
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
      'backup_enabled', 'start_with_windows',
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
      UI.applyTheme(saved.theme);
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

    document.getElementById('set-theme').addEventListener('change', function () {
      // Preview only: the choice is committed when the form is saved, so it
      // must not be mirrored into localStorage before then.
      UI.previewTheme(this.value);
    });

    document.getElementById('backup-now').addEventListener('click', async function () {
      this.disabled = true;
      try {
        await API.post('/api/backups');
        UI.notify.success('message.backup_created');
        await loadBackups();
      } catch (err) { UI.notify.error(err); }
      finally { this.disabled = false; }
    });

    document.getElementById('export-run').addEventListener('click', runExport);
    document.getElementById('import-run').addEventListener('click', runImport);

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
