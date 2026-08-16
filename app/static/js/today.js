/* Today — the home screen.
   Answers "what do I need to do today?": the next dose, today's doses in
   chronological order with their status, today's appointments, treatments
   ending soon and everything active. Refreshes every minute so the countdown
   and the statuses stay honest without a page reload. */
(function () {
  'use strict';

  const el = UI.el;
  let refreshTimer = null;

  async function render() {
    const data = await API.get('/api/today');
    document.getElementById('today-date').textContent = F.dateLong(new Date());

    renderNextDose(data);
    renderNextAppointment(data.next_appointment);
    renderEndingSoon(data.ending_soon);
    renderTodaysDoses(data);
    renderTodaysAppointments(data.todays_appointments || []);
    renderActive(data.active_medications);

    if (!refreshTimer) {
      refreshTimer = setInterval(render, 60000);
      // Registered once, not on every render, so listeners cannot pile up.
      document.addEventListener('medtracker:notified', function () { render(); });
    }
  }

  function renderNextDose(data) {
    const box = document.getElementById('next-dose-body');
    box.textContent = '';

    const overdue = data.overdue_doses || [];
    if (overdue.length) {
      const warn = el('p', 'badge badge--missed', T.t('dashboard.overdue_doses') + ': ' + overdue.length);
      box.appendChild(warn);
    }

    const dose = data.next_dose;
    if (!dose) {
      box.appendChild(el('p', 'muted', T.t('dashboard.next_dose_empty')));
      return;
    }
    const head = el('div', 'card__title');
    head.appendChild(UI.thumb(dose));
    const info = el('div');
    info.appendChild(el('h3', null, dose.medication_name));
    info.appendChild(el('div', 'card__meta', UI.doseSummary(dose)));
    head.appendChild(info);
    box.appendChild(head);

    box.appendChild(el('p', 'big-time', F.whenLabel(dose.scheduled_at)));
    box.appendChild(el('p', 'countdown', F.countdown(dose.scheduled_at)));
  }

  function renderNextAppointment(appointment) {
    const box = document.getElementById('next-appointment-body');
    box.textContent = '';
    if (!appointment) {
      box.appendChild(el('p', 'muted', T.t('dashboard.next_appointment_empty')));
      return;
    }
    const link = el('a', null, appointment.doctor_name);
    link.href = '/appointments/' + appointment.id;
    const heading = el('h3');
    heading.appendChild(link);
    box.appendChild(heading);
    box.appendChild(el('p', 'big-time', F.dateTime(appointment.scheduled_at)));

    const days = F.daysUntil(appointment.scheduled_at);
    let label;
    if (days <= 0) label = T.t('dashboard.appointment_today');
    else if (days === 1) label = T.t('dashboard.appointment_in_one_day');
    else label = T.t('dashboard.appointment_in_days', { count: days });
    box.appendChild(el('p', 'countdown', label));

    if (appointment.treatment) box.appendChild(el('p', 'card__meta', appointment.treatment));
  }

  function renderEndingSoon(items) {
    const section = document.getElementById('ending-soon-section');
    const list = document.getElementById('ending-soon-list');
    list.textContent = '';
    if (!items.length) { section.classList.add('hidden'); return; }
    section.classList.remove('hidden');

    items.forEach(function (medication) {
      const card = el('article', 'card card--warning');
      const head = el('div', 'card__title');
      head.appendChild(UI.thumb(medication));
      const info = el('div');
      info.appendChild(el('h3', null, medication.name));
      const days = medication.days_remaining;
      let label;
      if (days === null) label = T.t('medication.open_ended');
      else if (days <= 0) label = T.t('dashboard.ends_today');
      else if (days === 1) label = T.t('dashboard.ends_in_one_day');
      else label = T.t('dashboard.ends_in_days', { count: days });
      info.appendChild(el('div', 'card__meta', label));
      head.appendChild(info);
      card.appendChild(head);
      list.appendChild(card);
    });
  }

  function renderTodaysDoses(data) {
    const list = document.getElementById('todays-doses');
    const summary = document.getElementById('todays-summary');
    list.textContent = '';
    summary.textContent = T.t('today.taken_of', {
      taken: data.todays_summary.taken, total: data.todays_summary.total,
    });

    if (!data.todays_doses.length) {
      list.appendChild(UI.emptyState('today.no_doses'));
      return;
    }
    // Chronological, with the quick actions right on the row.
    data.todays_doses.forEach(function (dose) {
      list.appendChild(C.doseRow(dose, render, { timeline: true }));
    });
  }

  function renderTodaysAppointments(items) {
    const list = document.getElementById('todays-appointments');
    list.textContent = '';
    if (!items.length) {
      list.appendChild(UI.emptyState('today.no_appointments'));
      return;
    }
    items.forEach(function (appointment) {
      const row = el('div', 'dose-row dose-row--scheduled');
      row.appendChild(el('div', 'dose-row__time', F.time(appointment.scheduled_at)));
      const main = el('div', 'dose-row__main');
      main.appendChild(el('strong', null, appointment.doctor_name || ''));
      if (appointment.doctor_occupation) {
        main.appendChild(el('div', 'card__meta', appointment.doctor_occupation));
      }
      if (appointment.treatment) main.appendChild(el('div', 'card__meta', appointment.treatment));
      row.appendChild(main);
      const actions = el('div', 'dose-row__actions');
      const view = el('a', 'btn btn--sm btn--ghost', T.t('common.view'));
      view.href = '/appointments/' + appointment.id;
      actions.appendChild(view);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  function renderActive(items) {
    const list = document.getElementById('active-medications');
    list.textContent = '';
    if (!items.length) {
      list.appendChild(UI.emptyState('dashboard.active_medications_empty', 'medication.empty_hint'));
      return;
    }
    items.forEach(function (medication) {
      list.appendChild(C.medicationCard(medication, {}));
    });
  }

  UI.page(render);
})();
