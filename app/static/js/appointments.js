/* Appointments list + the add/edit dialog. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { filter: 'all', editing: null, medications: [], doctors: [] };

  const dialog = function () { return document.getElementById('appointment-dialog'); };
  const form = function () { return document.getElementById('appointment-form'); };

  async function load() {
    const list = document.getElementById('appointment-list');
    list.textContent = '';
    const data = await API.get('/api/appointments?scope=' + encodeURIComponent(state.filter));
    if (!data.items.length) {
      list.appendChild(
        state.filter === 'all'
          ? UI.emptyState('appointment.empty', 'appointment.empty_hint')
          : UI.emptyState('appointment.empty_filtered')
      );
      return;
    }
    data.items.forEach(function (appointment) { list.appendChild(card(appointment)); });
  }

  function card(appointment) {
    const box = el('article', 'card');
    const link = el('a', null, appointment.doctor_name || '');
    link.href = '/appointments/' + appointment.id;
    const heading = el('h3');
    heading.appendChild(link);
    box.appendChild(heading);

    box.appendChild(el('div', 'card__meta', F.dateTime(appointment.scheduled_at)));
    if (appointment.doctor_occupation) {
      box.appendChild(el('div', 'card__meta', appointment.doctor_occupation));
    }
    if (appointment.location) box.appendChild(el('div', 'card__meta', appointment.location));
    if (appointment.treatment) box.appendChild(el('div', 'card__meta', appointment.treatment));
    if (appointment.follow_up_of) {
      box.appendChild(el('div', 'card__meta', T.t('appointment.follow_up_of') + ': ' +
        F.dateLong(appointment.follow_up_of.scheduled_at)));
    }

    if (appointment.medications.length) {
      const meds = el('div', 'card__meta', T.t('appointment.medications') + ': ' +
        appointment.medications.map(function (m) { return m.name; }).join(', '));
      box.appendChild(meds);
    }

    const footer = el('div', 'card__footer');
    footer.appendChild(UI.badge(appointment.is_past ? 'past' : 'upcoming'));

    const edit = el('button', 'btn btn--sm btn--ghost', T.t('common.edit'));
    edit.type = 'button';
    edit.addEventListener('click', function () { openDialog(appointment); });
    footer.appendChild(edit);

    const remove = el('button', 'btn btn--sm btn--ghost', T.t('common.delete'));
    remove.type = 'button';
    remove.addEventListener('click', async function () {
      const ok = await UI.confirm('confirm.delete_appointment_title', 'confirm.delete_appointment_body',
        { doctor: appointment.doctor_name }, 'confirm.delete_confirm_word');
      if (!ok) return;
      try {
        await API.del('/api/appointments/' + appointment.id);
        UI.notify.success('message.appointment_deleted');
        load();
      } catch (err) { UI.notify.error(err); }
    });
    footer.appendChild(remove);
    box.appendChild(footer);
    return box;
  }

  async function loadMedications() {
    try {
      const data = await API.get('/api/medications?status=all');
      state.medications = data.items;
    } catch (e) { state.medications = []; }
  }

  async function loadDoctors() {
    try {
      const data = await API.get('/api/doctors');
      state.doctors = data.items;
    } catch (e) { state.doctors = []; }
  }

  function renderDoctorSelect(selectedId) {
    const select = document.getElementById('appt-doctor');
    const warning = document.getElementById('appt-no-doctors');
    select.textContent = '';

    const placeholder = el('option', null, T.t('doctor.select'));
    placeholder.value = '';
    select.appendChild(placeholder);

    state.doctors.forEach(function (doctor) {
      const label = doctor.occupation ? doctor.name + ' — ' + doctor.occupation : doctor.name;
      const option = el('option', null, label);
      option.value = doctor.id;
      select.appendChild(option);
    });
    select.value = selectedId ? String(selectedId) : '';
    warning.classList.toggle('hidden', state.doctors.length > 0);
  }

  /* The list of appointments that may be chosen as "follow-up of ..." is
     recomputed from the server whenever the new appointment's date changes,
     so a visit that has not happened yet can never appear in it. */
  async function refreshFollowUpOptions(selectedId) {
    const select = document.getElementById('appt-follow-up');
    const f = form();
    const when = f.date.value && f.time.value ? f.date.value + 'T' + f.time.value : '';
    select.textContent = '';
    if (!when) return;

    let items = [];
    try {
      const query = '/api/appointments/follow-up-options?before=' + encodeURIComponent(when) +
        (state.editing ? '&exclude=' + state.editing.id : '');
      items = (await API.get(query)).items;
    } catch (e) { items = []; }

    if (!items.length) {
      const none = el('option', null, T.t('appointment.follow_up_none'));
      none.value = '';
      select.appendChild(none);
      return;
    }
    const placeholder = el('option', null, T.t('appointment.follow_up_select'));
    placeholder.value = '';
    select.appendChild(placeholder);
    items.forEach(function (item) {
      const option = el('option', null,
        (item.doctor_name || '') + ' — ' + F.dateLong(item.scheduled_at));
      option.value = item.id;
      select.appendChild(option);
    });
    if (selectedId) select.value = String(selectedId);
  }

  function setFollowUpMode(isFollowUp) {
    const box = document.getElementById('appt-follow-up-box');
    box.classList.toggle('hidden', !isFollowUp);
    form().querySelectorAll('input[name=is_follow_up]').forEach(function (radio) {
      radio.checked = radio.value === (isFollowUp ? 'yes' : 'no');
    });
  }

  function renderMedicationChecks(selectedIds) {
    const box = document.getElementById('appt-medications');
    box.textContent = '';
    if (!state.medications.length) {
      box.appendChild(el('p', 'muted', T.t('medication.empty')));
      return;
    }
    state.medications.forEach(function (medication) {
      const label = el('label', 'check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = medication.id;
      input.name = 'medication_ids';
      input.checked = selectedIds.indexOf(medication.id) !== -1;
      label.appendChild(input);
      label.appendChild(el('span', null, medication.name));
      box.appendChild(label);
    });
  }

  function openDialog(appointment) {
    state.editing = appointment || null;
    const f = form();
    UI.clearErrors(f);
    f.reset();

    document.getElementById('appointment-dialog-title').textContent =
      T.t(appointment ? 'appointment.edit' : 'appointment.new');

    if (appointment) {
      const when = F.parse(appointment.scheduled_at);
      renderDoctorSelect(appointment.doctor_id);
      f.date.value = F.inputDate(when);
      f.time.value = String(when.getHours()).padStart(2, '0') + ':' + String(when.getMinutes()).padStart(2, '0');
      f.location.value = appointment.location || '';
      f.treatment.value = appointment.treatment || '';
      f.notes.value = appointment.notes || '';
      f.next_appointment_at.value = appointment.next_appointment_at ? F.inputDateTime(appointment.next_appointment_at) : '';
      f.reminder_days_3.checked = appointment.reminder_days_3;
      f.reminder_day_1.checked = appointment.reminder_day_1;
      f.reminder_hours_3.checked = appointment.reminder_hours_3;
      renderMedicationChecks(appointment.medications.map(function (m) { return m.id; }));
      setFollowUpMode(Boolean(appointment.follow_up_of));
      refreshFollowUpOptions(appointment.follow_up_of ? appointment.follow_up_of.id : null);
    } else {
      const prefill = prefillFromQuery();
      renderDoctorSelect(prefill.doctorId);
      f.date.value = prefill.date || F.inputDate(new Date());
      f.time.value = prefill.time || '09:00';
      f.reminder_days_3.checked = T.settings.appt_reminder_days_3;
      f.reminder_day_1.checked = T.settings.appt_reminder_day_1;
      f.reminder_hours_3.checked = T.settings.appt_reminder_hours_3;
      renderMedicationChecks([]);
      setFollowUpMode(false);
      refreshFollowUpOptions(null);
    }

    dialog().showModal();
  }

  /* Supports "create the follow-up appointment" links from a detail page. */
  function prefillFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const at = params.get('at');
    const result = { doctorId: params.get('doctor_id') };
    if (at) {
      const parsed = F.parse(at);
      if (parsed) {
        result.date = F.inputDate(parsed);
        result.time = String(parsed.getHours()).padStart(2, '0') + ':' + String(parsed.getMinutes()).padStart(2, '0');
      }
    }
    return result;
  }

  async function submit(event) {
    event.preventDefault();
    const f = form();
    UI.clearErrors(f);
    const submitButton = f.querySelector('button[type=submit]');
    submitButton.disabled = true;

    const selected = Array.prototype.slice
      .call(document.querySelectorAll('#appt-medications input:checked'))
      .map(function (input) { return input.value; });

    const isFollowUp = f.querySelector('input[name=is_follow_up]:checked').value === 'yes';
    const payload = {
      doctor_id: f.doctor_id.value || null,
      follow_up_of_id: isFollowUp ? (f.follow_up_of_id.value || null) : null,
      scheduled_at: f.date.value && f.time.value ? f.date.value + 'T' + f.time.value : '',
      location: f.location.value,
      treatment: f.treatment.value,
      notes: f.notes.value,
      next_appointment_at: f.next_appointment_at.value,
      reminder_days_3: f.reminder_days_3.checked,
      reminder_day_1: f.reminder_day_1.checked,
      reminder_hours_3: f.reminder_hours_3.checked,
      medication_ids: selected,
    };

    try {
      if (state.editing) {
        await API.put('/api/appointments/' + state.editing.id, payload);
        UI.notify.success('message.appointment_updated');
      } else {
        await API.post('/api/appointments', payload);
        UI.notify.success('message.appointment_created');
      }
      dialog().close('saved');
      await load();
    } catch (err) {
      if (err instanceof API.ApiError && Object.keys(err.fields || {}).length) {
        UI.showErrors(f, err.fields);
        UI.notify.error('error.validation');
      } else {
        UI.notify.error(err);
      }
    } finally {
      submitButton.disabled = false;
    }
  }

  async function render() {
    await Promise.all([loadMedications(), loadDoctors()]);

    form().querySelectorAll('input[name=is_follow_up]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        setFollowUpMode(this.value === 'yes');
        if (this.value === 'yes') refreshFollowUpOptions(null);
      });
    });
    ['appt-date', 'appt-time'].forEach(function (id) {
      document.getElementById(id).addEventListener('change', function () {
        refreshFollowUpOptions(document.getElementById('appt-follow-up').value || null);
      });
    });

    document.getElementById('appointment-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-filter]');
      if (!chip) return;
      state.filter = chip.dataset.filter;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      load();
    });

    document.getElementById('add-appointment').addEventListener('click', function () { openDialog(null); });
    form().addEventListener('submit', submit);

    await load();

    const params = new URLSearchParams(window.location.search);
    if (params.get('new')) openDialog(null);
  }

  UI.page(render);
})();
