/* One appointment: details, reminders and the medications it prescribed. */
(function () {
  'use strict';

  const el = UI.el;

  async function render() {
    const container = document.getElementById('appointment-detail');
    const id = container.dataset.appointmentId;

    let appointment;
    try {
      appointment = await API.get('/api/appointments/' + id);
    } catch (err) {
      container.textContent = '';
      container.appendChild(UI.emptyState('appointment.not_found'));
      return;
    }

    container.textContent = '';

    const card = el('div', 'card');
    card.appendChild(el('h1', null, appointment.doctor_name));
    card.appendChild(el('p', 'big-time', F.dateTime(appointment.scheduled_at)));

    const meta = el('dl', 'meta-list');
    if (appointment.location) addMeta(meta, 'appointment.location', appointment.location);
    if (appointment.treatment) addMeta(meta, 'appointment.treatment', appointment.treatment);
    if (appointment.notes) addMeta(meta, 'appointment.notes', appointment.notes);
    if (appointment.next_appointment_at) {
      addMeta(meta, 'appointment.next_appointment', F.dateTime(appointment.next_appointment_at));
    }
    card.appendChild(meta);

    if (appointment.next_appointment_at) {
      const create = el('a', 'btn btn--sm btn--ghost', T.t('actions.create_follow_up'));
      create.href = '/appointments?new=1&at=' + encodeURIComponent(appointment.next_appointment_at) +
        '&doctor=' + encodeURIComponent(appointment.doctor_name);
      const footer = el('div', 'card__footer');
      footer.appendChild(create);
      card.appendChild(footer);
    }
    container.appendChild(card);

    /* --- reminders ---------------------------------------------------- */
    const reminders = el('section', 'section');
    reminders.appendChild(el('h2', null, T.t('appointment.reminders')));
    const list = el('div', 'stack');
    if (!appointment.reminders || !appointment.reminders.length) {
      list.appendChild(UI.emptyState('reminder.none_enabled'));
    } else {
      appointment.reminders.forEach(function (reminder) {
        const row = el('div', 'dose-row dose-row--scheduled');
        row.appendChild(el('div', 'dose-row__time', T.t('reminder.' + reminder.kind)));
        row.appendChild(el('div', 'dose-row__main',
          T.t('reminder.scheduled_for', { datetime: F.dateTime(reminder.remind_at) })));
        row.appendChild(el('span', 'badge ' + (reminder.sent_at ? 'badge--taken' : 'badge--scheduled'),
          T.t(reminder.sent_at ? 'reminder.sent' : 'reminder.pending')));
        list.appendChild(row);
      });
    }
    reminders.appendChild(list);
    container.appendChild(reminders);

    /* --- medications -------------------------------------------------- */
    const meds = el('section', 'section');
    meds.appendChild(el('h2', null, T.t('appointment.medications')));
    const medList = el('div', 'grid grid--cards');
    if (!appointment.medications.length) {
      medList.appendChild(UI.emptyState('appointment.medications_empty'));
    } else {
      appointment.medications.forEach(function (medication) {
        const box = el('article', 'card');
        const link = el('a', null, medication.name);
        link.href = '/medications/' + medication.id;
        const heading = el('h3');
        heading.appendChild(link);
        box.appendChild(heading);
        box.appendChild(el('div', 'card__meta', UI.doseSummary(medication)));
        const footer = el('div', 'card__footer');
        footer.appendChild(UI.badge(medication.status));
        box.appendChild(footer);
        medList.appendChild(box);
      });
    }
    meds.appendChild(medList);
    container.appendChild(meds);
  }

  function addMeta(list, labelKey, text) {
    list.appendChild(el('dt', null, T.t(labelKey)));
    list.appendChild(el('dd', null, text));
  }

  UI.page(render);
})();
