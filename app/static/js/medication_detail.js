/* One medication: full data, dose counters, upcoming and past doses. */
(function () {
  'use strict';

  const el = UI.el;
  const root = function () { return document.getElementById('medication-detail'); };

  async function render() {
    const id = root().dataset.medicationId;
    let medication;
    try {
      medication = await API.get('/api/medications/' + id);
    } catch (err) {
      root().textContent = '';
      root().appendChild(UI.emptyState('medication.not_found'));
      return;
    }

    const container = root();
    container.textContent = '';

    /* --- header ------------------------------------------------------- */
    const header = el('div', 'card');
    const title = el('div', 'card__title');
    title.appendChild(UI.thumb(medication, true));
    const info = el('div');
    const h1 = el('h1', null, medication.name);
    info.appendChild(h1);
    info.appendChild(el('div', 'card__meta', UI.doseSummary(medication)));
    info.appendChild(el('div', 'card__meta', F.frequency(medication.frequency_hours)));
    title.appendChild(info);
    header.appendChild(title);

    const meta = el('dl', 'meta-list');
    addMeta(meta, 'medication.status', null, UI.badge(medication.status));
    addMeta(meta, 'medication.treatment', T.t('medication.treatment_range', {
      start: F.dateLong(medication.start_date), end: F.dateLong(medication.end_date),
    }));
    addMeta(meta, 'medication.first_dose_time', F.time('2000-01-01T' + medication.first_dose_time));
    addMeta(meta, 'medication.next_dose', medication.next_dose
      ? F.whenLabel(medication.next_dose.scheduled_at)
      : T.t('medication.no_next_dose'));
    if (medication.comments) addMeta(meta, 'medication.comments', medication.comments);
    if (medication.appointments.length) {
      const links = el('dd');
      medication.appointments.forEach(function (appointment, index) {
        if (index) links.appendChild(document.createTextNode(', '));
        const link = el('a', null, appointment.doctor_name + ' — ' + F.dateShort(appointment.scheduled_at));
        link.href = '/appointments/' + appointment.id;
        links.appendChild(link);
      });
      meta.appendChild(el('dt', null, T.t('medication.prescribed_at')));
      meta.appendChild(links);
    }
    header.appendChild(meta);

    const footer = el('div', 'card__footer');
    C.medicationActions(medication, render).forEach(function (btn) { footer.appendChild(btn); });
    header.appendChild(footer);
    container.appendChild(header);

    /* --- counters ----------------------------------------------------- */
    const counts = el('div', 'count-grid');
    [['dose.count_taken', 'taken'], ['dose.count_skipped', 'skipped'],
     ['dose.count_missed', 'missed'], ['dose.count_scheduled', 'scheduled']].forEach(function (pair) {
      const box = el('div');
      box.appendChild(el('strong', null, medication.counts[pair[1]] || 0));
      box.appendChild(el('span', null, T.t(pair[0])));
      counts.appendChild(box);
    });
    container.appendChild(counts);

    /* --- doses -------------------------------------------------------- */
    const now = new Date();
    const upcoming = medication.doses.filter(function (d) { return F.parse(d.scheduled_at) >= now; });
    const past = medication.doses.filter(function (d) { return F.parse(d.scheduled_at) < now; }).reverse();

    container.appendChild(doseSection('dose.upcoming', upcoming));
    container.appendChild(doseSection('dose.past', past));
  }

  function doseSection(titleKey, doses) {
    const section = el('section', 'section');
    section.appendChild(el('h2', null, T.t(titleKey)));
    const list = el('div', 'stack');
    if (!doses.length) {
      list.appendChild(UI.emptyState('dose.history_empty'));
    } else {
      doses.slice(0, 200).forEach(function (dose) {
        list.appendChild(C.doseRow(dose, render, { hideName: true, showDate: true }));
      });
    }
    section.appendChild(list);
    return section;
  }

  function addMeta(list, labelKey, text, node) {
    list.appendChild(el('dt', null, T.t(labelKey)));
    const dd = el('dd', null, text === null ? undefined : text);
    if (node) dd.appendChild(node);
    list.appendChild(dd);
  }

  UI.page(render);
})();
