/* Appointments list + the add/edit dialog. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { filter: 'all', editing: null, medications: [] };

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
    const link = el('a', null, appointment.doctor_name);
    link.href = '/appointments/' + appointment.id;
    const heading = el('h3');
    heading.appendChild(link);
    box.appendChild(heading);

    box.appendChild(el('div', 'card__meta', F.dateTime(appointment.scheduled_at)));
    if (appointment.location) box.appendChild(el('div', 'card__meta', appointment.location));
    if (appointment.treatment) box.appendChild(el('div', 'card__meta', appointment.treatment));

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
      f.doctor_name.value = appointment.doctor_name;
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
    } else {
      const prefill = prefillFromQuery();
      f.date.value = prefill.date || F.inputDate(new Date());
      f.time.value = prefill.time || '09:00';
      if (prefill.doctor) f.doctor_name.value = prefill.doctor;
      f.reminder_days_3.checked = T.settings.appt_reminder_days_3;
      f.reminder_day_1.checked = T.settings.appt_reminder_day_1;
      f.reminder_hours_3.checked = T.settings.appt_reminder_hours_3;
      renderMedicationChecks([]);
    }

    dialog().showModal();
  }

  /* Supports "create the follow-up appointment" links from a detail page. */
  function prefillFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const at = params.get('at');
    const result = { doctor: params.get('doctor') };
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

    const payload = {
      doctor_name: f.doctor_name.value,
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
    await loadMedications();

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
