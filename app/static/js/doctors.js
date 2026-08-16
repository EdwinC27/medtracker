/* Doctors list + the add/edit dialog. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { editing: null, search: '' };

  const dialog = function () { return document.getElementById('doctor-dialog'); };
  const form = function () { return document.getElementById('doctor-form'); };

  async function load() {
    const list = document.getElementById('doctor-list');
    list.textContent = '';
    const query = state.search ? '?search=' + encodeURIComponent(state.search) : '';
    const data = await API.get('/api/doctors' + query);
    if (!data.items.length) {
      list.appendChild(
        state.search
          ? UI.emptyState('doctor.empty_filtered')
          : UI.emptyState('doctor.empty', 'doctor.empty_hint')
      );
      return;
    }
    data.items.forEach(function (doctor) { list.appendChild(card(doctor)); });
  }

  function card(doctor) {
    const box = el('article', 'card');

    const link = el('a', null, doctor.name);
    link.href = '/doctors/' + doctor.id;
    const heading = el('h3');
    heading.appendChild(link);
    box.appendChild(heading);

    if (doctor.occupation) box.appendChild(el('div', 'card__meta', doctor.occupation));
    if (doctor.phone) {
      const phone = el('div', 'card__meta');
      const tel = el('a', null, doctor.phone);
      tel.href = 'tel:' + doctor.phone.replace(/\s+/g, '');
      phone.appendChild(tel);
      box.appendChild(phone);
    }
    box.appendChild(el('div', 'card__meta', doctor.appointment_count === 1
      ? T.t('doctor.appointment_count_one')
      : T.t('doctor.appointment_count', { count: doctor.appointment_count })));

    const footer = el('div', 'card__footer');

    const edit = el('button', 'btn btn--sm btn--ghost', T.t('common.edit'));
    edit.type = 'button';
    edit.addEventListener('click', function () { openDialog(doctor); });
    footer.appendChild(edit);

    const remove = el('button', 'btn btn--sm btn--ghost', T.t('common.delete'));
    remove.type = 'button';
    remove.addEventListener('click', function () { confirmDelete(doctor); });
    footer.appendChild(remove);

    box.appendChild(footer);
    return box;
  }

  async function confirmDelete(doctor) {
    const ok = await UI.confirm('confirm.delete_doctor_title', 'confirm.delete_doctor_body',
      { name: doctor.name }, 'confirm.delete_confirm_word');
    if (!ok) return;
    try {
      await API.del('/api/doctors/' + doctor.id);
      UI.notify.success('message.doctor_deleted');
      load();
    } catch (err) {
      UI.notify.error(err);
    }
  }

  function openDialog(doctor) {
    state.editing = doctor || null;
    const f = form();
    UI.clearErrors(f);
    f.reset();
    document.getElementById('doctor-dialog-title').textContent =
      T.t(doctor ? 'doctor.edit' : 'doctor.new');
    if (doctor) {
      f.name.value = doctor.name;
      f.occupation.value = doctor.occupation || '';
      f.phone.value = doctor.phone || '';
      f.notes.value = doctor.notes || '';
    }
    dialog().showModal();
  }

  async function submit(event) {
    event.preventDefault();
    const f = form();
    UI.clearErrors(f);
    const button = f.querySelector('button[type=submit]');
    button.disabled = true;

    const payload = {
      name: f.name.value,
      occupation: f.occupation.value,
      phone: f.phone.value,
      notes: f.notes.value,
    };

    try {
      if (state.editing) {
        await API.put('/api/doctors/' + state.editing.id, payload);
        UI.notify.success('message.doctor_updated');
      } else {
        await API.post('/api/doctors', payload);
        UI.notify.success('message.doctor_created');
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
      button.disabled = false;
    }
  }

  async function render() {
    document.getElementById('add-doctor').addEventListener('click', function () { openDialog(null); });
    form().addEventListener('submit', submit);

    let timer = null;
    document.getElementById('doctor-search').addEventListener('input', function () {
      state.search = this.value.trim();
      clearTimeout(timer);
      timer = setTimeout(load, 200);
    });

    await load();
    if (new URLSearchParams(window.location.search).get('new')) openDialog(null);
  }

  UI.page(render);
})();
