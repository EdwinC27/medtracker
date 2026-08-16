/* History — two readings of the same records:
   the medical timeline (appointments, doctors, follow-ups) and the medication
   history. Both are joins over what already exists; nothing is duplicated. */
(function () {
  'use strict';

  const el = UI.el;
  const state = {
    tab: 'timeline', filter: 'all', expanded: {}, timelineOffset: 0,
    timelineKind: 'all',
    order: 'newest', doctorId: '', medicationId: '',
  };

  async function load() {
    const list = document.getElementById('history-list');
    list.textContent = '';
    const data = await API.get('/api/medications?status=' + encodeURIComponent(state.filter));
    if (!data.items.length) {
      list.appendChild(UI.emptyState(state.filter === 'all' ? 'medication.empty' : 'medication.empty_filtered'));
      return;
    }
    for (const medication of data.items) {
      list.appendChild(row(medication));
    }
  }

  function row(medication) {
    const card = el('article', 'card');

    const head = el('div', 'card__title');
    head.appendChild(UI.thumb(medication));
    const info = el('div');
    const link = el('a', null, medication.name);
    link.href = '/medications/' + medication.id;
    const heading = el('h3');
    heading.appendChild(link);
    info.appendChild(heading);
    info.appendChild(el('div', 'card__meta', UI.doseSummary(medication) + ' · ' + F.frequency(medication.frequency_hours)));
    head.appendChild(info);
    card.appendChild(head);

    const meta = el('dl', 'meta-list');
    meta.appendChild(el('dt', null, T.t('medication.start_date')));
    meta.appendChild(el('dd', null, F.dateLong(medication.start_date)));
    meta.appendChild(el('dt', null, T.t('medication.end_date')));
    meta.appendChild(el('dd', null, medication.end_date
      ? F.dateLong(medication.end_date) : T.t('medication.no_end_date')));
    meta.appendChild(el('dt', null, T.t('medication.status')));
    const statusCell = el('dd');
    statusCell.appendChild(UI.badge(medication.status));
    meta.appendChild(statusCell);
    card.appendChild(meta);

    const counts = el('div', 'count-grid');
    const boxes = [['dose.count_taken', 'taken'], ['dose.count_skipped', 'skipped'],
                   ['dose.count_missed', 'missed'], ['dose.count_scheduled', 'scheduled']];
    // Otherwise the card totals would not add up to the rows it lists.
    if (medication.counts.before_registration) {
      boxes.push(['dose.count_before_registration', 'before_registration']);
    }
    boxes.forEach(function (pair) {
      const box = el('div');
      box.appendChild(el('strong', null, medication.counts[pair[1]] || 0));
      box.appendChild(el('span', null, T.t(pair[0])));
      counts.appendChild(box);
    });
    card.appendChild(counts);

    const doseBox = el('div', 'stack');
    doseBox.style.marginTop = '.6rem';
    card.appendChild(doseBox);

    const toggle = el('button', 'btn btn--sm btn--ghost', T.t('dose.show_all'));
    toggle.type = 'button';
    toggle.addEventListener('click', async function () {
      if (doseBox.childElementCount) {
        doseBox.textContent = '';
        toggle.textContent = T.t('dose.show_all');
        return;
      }
      toggle.disabled = true;
      try {
        const full = await API.get('/api/medications/' + medication.id);
        const doses = full.doses.slice().reverse();
        if (!doses.length) {
          doseBox.appendChild(UI.emptyState('dose.history_empty'));
        } else {
          doses.slice(0, 400).forEach(function (dose) {
            doseBox.appendChild(C.doseRow(dose, load, { hideName: true, showDate: true }));
          });
        }
        toggle.textContent = T.t('dose.show_less');
      } catch (err) {
        UI.notify.error(err);
      } finally {
        toggle.disabled = false;
      }
    });

    const footer = el('div', 'card__footer');
    footer.appendChild(toggle);
    card.appendChild(footer);
    return card;
  }

  /* The day a treatment started. Its point in the timeline is context: it shows
     that a treatment was already running, and how much of it happened before
     the application knew about it. */
  function treatmentEntry(entry) {
    const card = el('article', 'card timeline-entry timeline-entry--treatment' +
      (entry.is_past ? '' : ' card--accent'));

    card.appendChild(el('h3', null, '💊 ' + F.dateLong(entry.date)));
    card.appendChild(el('div', 'card__meta', T.t('timeline.treatment_started')));

    const link = el('a', null, entry.name);
    link.href = entry.href;
    const nameLine = el('strong');
    nameLine.appendChild(link);
    card.appendChild(nameLine);

    const meta = el('dl', 'meta-list');
    meta.appendChild(el('dt', null, T.t('medication.treatment')));
    meta.appendChild(el('dd', null, entry.end_date
      ? T.t('medication.treatment_range', {
          start: F.dateLong(entry.start_date), end: F.dateLong(entry.end_date),
        })
      : F.dateLong(entry.start_date) + ' → ' + T.t('medication.no_end_date')));

    if (entry.started_before_registration) {
      meta.appendChild(el('dt', null, T.t('medication.registered_at')));
      meta.appendChild(el('dd', null, F.dateLong(entry.registered_at)));
    }

    if (entry.before_registration.count) {
      meta.appendChild(el('dt', null, T.t('status.before_registration')));
      const cell = el('dd');
      cell.appendChild(document.createTextNode(
        T.t('timeline.before_registration_count', { count: entry.before_registration.count })));
      // The first few, so the shape of the history is visible without opening
      // the medication.
      entry.before_registration.first.forEach(function (when) {
        cell.appendChild(el('div', 'card__meta', F.dateLong(when) + ' · ' + F.time(when)));
      });
      meta.appendChild(cell);
    }
    card.appendChild(meta);

    const footer = el('div', 'card__footer');
    footer.appendChild(UI.badge(entry.status));
    const open = el('a', 'btn btn--sm btn--ghost', T.t('common.view'));
    open.href = entry.href;
    footer.appendChild(open);
    card.appendChild(footer);
    return card;
  }

  /* ----------------------------------------------------------- timeline */
  /* Read one page at a time: the backend never returns a whole history at once. */
  const TIMELINE_PAGE = 100;

  async function loadTimeline(append) {
    const list = document.getElementById('timeline-list');
    if (!append) {
      list.textContent = '';
      state.timelineOffset = 0;
    }
    const params = new URLSearchParams({
      order: state.order,
      limit: TIMELINE_PAGE,
      offset: state.timelineOffset || 0,
      kind: state.timelineKind || 'all',
    });
    if (state.doctorId) params.set('doctor_id', state.doctorId);
    if (state.medicationId) params.set('medication_id', state.medicationId);

    const data = await API.get('/api/timeline?' + params.toString());
    const more = document.getElementById('timeline-more');
    more.classList.add('hidden');

    if (!data.entries.length && !append) {
      list.appendChild(UI.emptyState('timeline.empty', 'timeline.empty_hint'));
      return;
    }
    data.entries.forEach(function (entry) { list.appendChild(timelineEntry(entry)); });
    state.timelineOffset = (state.timelineOffset || 0) + data.entries.length;
    more.classList.toggle('hidden', !data.has_more);
  }

  function timelineEntry(entry) {
    if (entry.type === 'treatment') return treatmentEntry(entry);

    const card = el('article', 'card timeline-entry' + (entry.is_past ? '' : ' card--accent'));

    card.appendChild(el('h3', null, '🩺 ' + F.dateLong(entry.date)));
    const when = el('div', 'card__meta', F.time(entry.datetime));
    card.appendChild(when);

    const doctorLine = el('div', 'card__meta');
    if (entry.doctor.id) {
      const link = el('a', null, entry.doctor.name || '');
      link.href = '/doctors/' + entry.doctor.id;
      doctorLine.appendChild(link);
    } else {
      doctorLine.textContent = entry.doctor.name || '';
    }
    if (entry.doctor.occupation) {
      doctorLine.appendChild(document.createTextNode(' — ' + entry.doctor.occupation));
    }
    card.appendChild(doctorLine);

    if (entry.follow_up_of) {
      const line = el('div', 'card__meta');
      line.appendChild(document.createTextNode(T.t('appointment.follow_up_of') + ': '));
      const link = el('a', null, F.dateLong(entry.follow_up_of.date));
      link.href = '/appointments/' + entry.follow_up_of.id;
      line.appendChild(link);
      card.appendChild(line);
    }

    const meta = el('dl', 'meta-list');
    if (entry.treatment) {
      meta.appendChild(el('dt', null, T.t('appointment.treatment')));
      meta.appendChild(el('dd', null, entry.treatment));
    }
    if (entry.medications.length) {
      meta.appendChild(el('dt', null, T.t('appointment.medications')));
      const cell = el('dd');
      entry.medications.forEach(function (medication, index) {
        if (index) cell.appendChild(document.createTextNode(', '));
        const link = el('a', null, medication.name);
        link.href = '/medications/' + medication.id;
        cell.appendChild(link);
      });
      meta.appendChild(cell);
    }
    meta.appendChild(el('dt', null, T.t('appointment.notes')));
    meta.appendChild(el('dd', null, entry.notes || T.t('timeline.no_notes')));
    card.appendChild(meta);

    const footer = el('div', 'card__footer');
    const open = el('a', 'btn btn--sm btn--ghost', T.t('common.open'));
    open.href = '/appointments/' + entry.id;
    footer.appendChild(open);
    (entry.follow_ups || []).forEach(function (item) {
      const link = el('a', 'btn btn--sm btn--ghost',
        T.t('appointment.follow_up_appointment') + ' · ' + F.dateShort(item.date));
      link.href = '/appointments/' + item.id;
      footer.appendChild(link);
    });
    card.appendChild(footer);
    return card;
  }

  function fillSelect(select, items, allLabel, key) {
    select.textContent = '';
    const all = el('option', null, T.t(allLabel));
    all.value = '';
    select.appendChild(all);
    items.forEach(function (item) {
      const option = el('option', null, item.name);
      option.value = item.id;
      select.appendChild(option);
    });
    select.addEventListener('change', function () {
      state[key] = this.value;
      loadTimeline();
    });
  }

  function showTab(tab) {
    state.tab = tab;
    document.getElementById('tab-timeline').classList.toggle('hidden', tab !== 'timeline');
    document.getElementById('tab-medications').classList.toggle('hidden', tab !== 'medications');
    document.querySelectorAll('#history-tabs .chip').forEach(function (chip) {
      chip.classList.toggle('is-active', chip.dataset.tab === tab);
    });
    if (tab === 'timeline') loadTimeline(); else load();
  }

  async function render() {
    document.getElementById('history-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-filter]');
      if (!chip) return;
      state.filter = chip.dataset.filter;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      load();
    });

    document.getElementById('history-tabs').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-tab]');
      if (chip) showTab(chip.dataset.tab);
    });

    document.getElementById('timeline-more').addEventListener('click', function () {
      loadTimeline(true);
    });

    document.getElementById('timeline-kind').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-kind]');
      if (!chip) return;
      state.timelineKind = chip.dataset.kind;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      loadTimeline();
    });

    document.getElementById('timeline-order').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-order]');
      if (!chip) return;
      state.order = chip.dataset.order;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      loadTimeline();
    });

    try {
      const [docs, meds] = await Promise.all([
        API.get('/api/doctors'), API.get('/api/medications?status=all'),
      ]);
      fillSelect(document.getElementById('timeline-doctor'), docs.items,
        'calendar.all_doctors', 'doctorId');
      fillSelect(document.getElementById('timeline-medication'), meds.items,
        'calendar.all_medications', 'medicationId');
    } catch (e) { /* filters are optional */ }

    const tab = new URLSearchParams(window.location.search).get('tab');
    showTab(tab === 'medications' ? 'medications' : 'timeline');
  }

  UI.page(render);
})();
