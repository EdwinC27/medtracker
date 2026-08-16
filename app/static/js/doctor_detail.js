/* One doctor: contact details and the full list of their appointments. */
(function () {
  'use strict';

  const el = UI.el;

  async function render() {
    const container = document.getElementById('doctor-detail');
    const id = container.dataset.doctorId;

    let doctor;
    try {
      doctor = await API.get('/api/doctors/' + id);
    } catch (err) {
      container.textContent = '';
      container.appendChild(UI.emptyState('doctor.not_found'));
      return;
    }

    container.textContent = '';

    const card = el('div', 'card');
    card.appendChild(el('h1', null, doctor.name));
    if (doctor.occupation) card.appendChild(el('p', 'card__meta', doctor.occupation));

    const meta = el('dl', 'meta-list');
    if (doctor.phone) {
      meta.appendChild(el('dt', null, T.t('doctor.phone')));
      const dd = el('dd');
      const tel = el('a', null, doctor.phone);
      tel.href = 'tel:' + doctor.phone.replace(/\s+/g, '');
      dd.appendChild(tel);
      meta.appendChild(dd);
    }
    if (doctor.notes) {
      meta.appendChild(el('dt', null, T.t('doctor.notes')));
      meta.appendChild(el('dd', null, doctor.notes));
    }
    card.appendChild(meta);

    const footer = el('div', 'card__footer');
    const add = el('a', 'btn btn--sm btn--primary', T.t('appointment.add'));
    add.href = '/appointments?new=1&doctor_id=' + doctor.id;
    footer.appendChild(add);
    card.appendChild(footer);
    container.appendChild(card);

    /* --- their appointments ------------------------------------------- */
    const section = el('section', 'section');
    section.appendChild(el('h2', null, T.t('doctor.appointments')));
    const list = el('div', 'stack');

    if (!doctor.appointments || !doctor.appointments.length) {
      list.appendChild(UI.emptyState('doctor.no_appointments'));
    } else {
      doctor.appointments.forEach(function (appointment) {
        const row = el('div', 'dose-row ' +
          (appointment.is_past ? 'dose-row--skipped' : 'dose-row--scheduled'));

        const when = el('div', 'dose-row__time');
        when.style.minWidth = '9rem';
        when.textContent = F.dateLong(appointment.scheduled_at);
        row.appendChild(when);

        const main = el('div', 'dose-row__main');
        main.appendChild(el('strong', null, F.time(appointment.scheduled_at)));
        if (appointment.treatment) main.appendChild(el('div', 'card__meta', appointment.treatment));
        if (appointment.medications && appointment.medications.length) {
          main.appendChild(el('div', 'card__meta',
            appointment.medications.map(function (m) { return m.name; }).join(', ')));
        }
        row.appendChild(main);

        row.appendChild(UI.badge(appointment.is_past ? 'past' : 'upcoming'));

        const actions = el('div', 'dose-row__actions');
        const view = el('a', 'btn btn--sm btn--ghost', T.t('common.view'));
        view.href = '/appointments/' + appointment.id;
        actions.appendChild(view);
        row.appendChild(actions);

        list.appendChild(row);
      });
    }
    section.appendChild(list);
    container.appendChild(section);
  }

  UI.page(render);
})();
