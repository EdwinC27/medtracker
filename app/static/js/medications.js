/* Medications list + the add/edit dialog. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { filter: 'all', editing: null, imagePath: undefined, appointments: [] };

  const dialog = function () { return document.getElementById('medication-dialog'); };
  const form = function () { return document.getElementById('medication-form'); };

  /* ------------------------------------------------------------- listing */
  async function load() {
    const list = document.getElementById('medication-list');
    list.textContent = '';
    const data = await API.get('/api/medications?status=' + encodeURIComponent(state.filter));
    if (!data.items.length) {
      list.appendChild(
        state.filter === 'all'
          ? UI.emptyState('medication.empty', 'medication.empty_hint')
          : UI.emptyState('medication.empty_filtered')
      );
      return;
    }
    data.items.forEach(function (medication) {
      list.appendChild(C.medicationCard(medication, {
        actions: function (item) {
          const buttons = [editButton(item)];
          return buttons.concat(C.medicationActions(item, load));
        },
      }));
    });
  }

  function editButton(medication) {
    const btn = el('button', 'btn btn--sm btn--ghost', T.t('common.edit'));
    btn.type = 'button';
    btn.addEventListener('click', function () { openDialog(medication); });
    return btn;
  }

  /* -------------------------------------------------------------- dialog */
  function fillSelect(select, values, labelFn, selected) {
    select.textContent = '';
    values.forEach(function (value) {
      const option = el('option', null, labelFn(value));
      option.value = value;
      if (String(value) === String(selected)) option.selected = true;
      select.appendChild(option);
    });
  }

  async function loadAppointments() {
    try {
      const data = await API.get('/api/appointments?scope=all');
      state.appointments = data.items;
    } catch (e) { state.appointments = []; }
  }

  function openDialog(medication) {
    state.editing = medication || null;
    state.imagePath = undefined;
    const f = form();
    UI.clearErrors(f);
    f.reset();

    document.getElementById('medication-dialog-title').textContent =
      T.t(medication ? 'medication.edit' : 'medication.new');

    fillSelect(document.getElementById('med-unit'), T.options.units,
      function (u) { return T.t('unit.' + u); }, medication ? medication.dose_unit : 'mg');
    fillSelect(document.getElementById('med-form'), T.options.forms,
      function (v) { return T.t('form.' + v); }, medication ? medication.form : 'tablet');
    fillSelect(document.getElementById('med-frequency'), T.options.frequencies,
      function (h) { return F.frequency(h); }, medication ? medication.frequency_hours : 8);

    renderAppointmentChecks(
      medication && medication.appointments
        ? medication.appointments.map(function (a) { return a.id; })
        : []
    );

    const today = new Date();
    if (medication) {
      f.name.value = medication.name;
      f.dose_amount.value = medication.dose_amount;
      f.quantity.value = medication.quantity;
      f.comments.value = medication.comments || '';
      f.start_date.value = medication.start_date;
      f.end_date.value = medication.end_date;
      f.first_dose_time.value = medication.first_dose_time;
      setPreviewImage(medication.image_url);
    } else {
      f.start_date.value = F.inputDate(today);
      const end = new Date(today.getTime() + 6 * 86400000);
      f.end_date.value = F.inputDate(end);
      f.first_dose_time.value = T.settings.default_first_dose_time;
      setPreviewImage(null);
    }

    updatePreview();
    dialog().showModal();
  }

  /* Checkbox list: a medication can be linked to more than one appointment, so
     editing it must not silently drop the links it already has. */
  function renderAppointmentChecks(selectedIds) {
    const box = document.getElementById('med-appointments');
    box.textContent = '';
    if (!state.appointments.length) {
      box.appendChild(el('p', 'muted', T.t('medication.prescribed_at_none')));
      return;
    }
    state.appointments.forEach(function (appointment) {
      const label = el('label', 'check');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = appointment.id;
      input.checked = selectedIds.indexOf(appointment.id) !== -1;
      label.appendChild(input);
      label.appendChild(el('span', null,
        appointment.doctor_name + ' — ' + F.dateShort(appointment.scheduled_at)));
      box.appendChild(label);
    });
  }

  function setPreviewImage(url) {
    const box = document.getElementById('med-image-preview');
    const removeBtn = document.getElementById('med-image-remove');
    box.textContent = '';
    if (url) {
      const img = document.createElement('img');
      img.src = url;
      img.alt = '';
      box.appendChild(img);
      removeBtn.classList.remove('hidden');
    } else {
      box.textContent = '🖼';
      removeBtn.classList.add('hidden');
    }
  }

  function updatePreview() {
    const f = form();
    const hours = Number(f.frequency_hours.value || 8);
    const first = f.first_dose_time.value || '10:00';
    const parts = [];
    let [h, m] = first.split(':').map(Number);
    const shown = Math.min(Math.round(24 / hours), 6);
    for (let i = 0; i < shown; i++) {
      const date = new Date(2000, 0, 1, h, m);
      date.setHours(date.getHours() + hours * i);
      parts.push(F.time(date));
    }
    document.getElementById('med-schedule-preview').textContent =
      F.frequency(hours) + ' · ' + parts.join(' · ') + (shown * hours < 24 ? ' …' : '');
  }

  async function uploadImageIfNeeded() {
    const input = document.getElementById('med-image');
    if (!input.files || !input.files.length) return state.imagePath;
    const data = new FormData();
    data.append('file', input.files[0]);
    const result = await API.upload('/api/uploads/image', data);
    return result.image_path;
  }

  async function submit(event) {
    event.preventDefault();
    const f = form();
    UI.clearErrors(f);
    const submitButton = f.querySelector('button[type=submit]');
    submitButton.disabled = true;

    try {
      const imagePath = await uploadImageIfNeeded();
      const payload = {
        name: f.name.value,
        dose_amount: f.dose_amount.value,
        dose_unit: f.dose_unit.value,
        quantity: f.quantity.value,
        form: f.form.value,
        comments: f.comments.value,
        start_date: f.start_date.value,
        end_date: f.end_date.value,
        frequency_hours: f.frequency_hours.value,
        first_dose_time: f.first_dose_time.value,
        appointment_ids: Array.prototype.slice
          .call(document.querySelectorAll('#med-appointments input:checked'))
          .map(function (input) { return input.value; }),
      };
      if (imagePath !== undefined) payload.image_path = imagePath;

      if (state.editing) {
        await API.put('/api/medications/' + state.editing.id, payload);
        UI.notify.success('message.medication_updated');
      } else {
        await API.post('/api/medications', payload);
        UI.notify.success('message.medication_created');
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

  /* --------------------------------------------------------------- setup */
  async function render() {
    await loadAppointments();

    document.getElementById('status-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-filter]');
      if (!chip) return;
      state.filter = chip.dataset.filter;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      load();
    });

    document.getElementById('add-medication').addEventListener('click', function () { openDialog(null); });
    form().addEventListener('submit', submit);
    ['change', 'input'].forEach(function (evt) {
      document.getElementById('med-frequency').addEventListener(evt, updatePreview);
      document.getElementById('med-first-dose').addEventListener(evt, updatePreview);
    });
    document.getElementById('med-image').addEventListener('change', function () {
      if (this.files && this.files[0]) setPreviewImage(URL.createObjectURL(this.files[0]));
    });
    document.getElementById('med-image-remove').addEventListener('click', function () {
      document.getElementById('med-image').value = '';
      state.imagePath = null;
      setPreviewImage(null);
    });

    await load();

    if (new URLSearchParams(window.location.search).get('new')) openDialog(null);
  }

  UI.page(render);
})();
