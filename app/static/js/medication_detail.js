/* One medication, in full: what it is, how the treatment is progressing by the
   calendar, what is due today, where it was prescribed, and its whole dose
   timeline grouped by day. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { filter: 'all', data: null };
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
    state.data = medication;

    const container = root();
    container.textContent = '';
    container.appendChild(header(medication));
    const progress = progressCard(medication);
    if (progress) container.appendChild(progress);
    container.appendChild(todaySection(medication));
    container.appendChild(countsRow(medication));
    const adherence = complianceCard(medication);
    if (adherence) container.appendChild(adherence);
    container.appendChild(timelineSection(medication));
  }

  /* -------------------------------------------------------------- header */
  function header(medication) {
    const card = el('div', 'card');
    const title = el('div', 'card__title');
    title.appendChild(UI.thumb(medication, true));

    const info = el('div');
    info.appendChild(el('h1', null, medication.name));
    const summary = UI.doseSummary(medication);
    if (summary) info.appendChild(el('div', 'card__meta', summary));
    info.appendChild(el('div', 'card__meta', F.frequency(medication.frequency_hours)));
    title.appendChild(info);
    card.appendChild(title);

    const meta = el('dl', 'meta-list');
    addMeta(meta, 'medication.status', null, UI.badge(medication.status));
    addMeta(meta, 'medication.treatment', medication.end_date
      ? T.t('medication.treatment_range', {
          start: F.dateLong(medication.start_date), end: F.dateLong(medication.end_date),
        })
      : F.dateLong(medication.start_date) + ' → ' + T.t('medication.no_end_date'));
    addMeta(meta, 'medication.first_dose_time', F.time('2000-01-01T' + medication.first_dose_time));
    addMeta(meta, 'medication.next_dose', medication.next_dose
      ? F.whenLabel(medication.next_dose.scheduled_at)
      : T.t('medication.no_next_dose'));
    if (medication.comments) addMeta(meta, 'medication.comments', medication.comments);

    if (medication.appointments.length) {
      meta.appendChild(el('dt', null, T.t('medication.prescribed_info')));
      const cell = el('dd');
      medication.appointments.forEach(function (appointment, index) {
        if (index) cell.appendChild(document.createElement('br'));
        if (appointment.doctor_id) {
          const doctor = el('a', null, appointment.doctor_name || '');
          doctor.href = '/doctors/' + appointment.doctor_id;
          cell.appendChild(doctor);
          cell.appendChild(document.createTextNode(' — '));
        }
        const link = el('a', null, T.t('appointment.singular') + ' · ' +
          F.dateLong(appointment.scheduled_at));
        link.href = '/appointments/' + appointment.id;
        cell.appendChild(link);
      });
      meta.appendChild(cell);
    }
    card.appendChild(meta);

    const footer = el('div', 'card__footer');
    C.medicationActions(medication, render).forEach(function (btn) { footer.appendChild(btn); });
    card.appendChild(footer);
    return card;
  }

  /* ------------------------------------------------------------ progress */
  function progressCard(medication) {
    const progress = medication.progress;
    const card = el('section', 'card');
    card.appendChild(el('h2', null, T.t('medication.progress_title')));

    if (!progress) {
      card.appendChild(el('p', 'card__meta', T.t('medication.no_progress')));
      return card;
    }

    card.appendChild(el('p', 'big-time',
      T.t('medication.day_of', { current: progress.current_day, total: progress.total_days })));

    const bar = el('div', 'progress');
    const fill = el('div', 'progress__bar');
    fill.style.width = progress.percent + '%';
    bar.appendChild(fill);
    card.appendChild(bar);

    let note;
    if (progress.not_started) note = T.t('medication.not_started');
    else if (progress.finished) note = T.t('medication.finished');
    else if (progress.days_remaining === 0) note = T.t('medication.last_day');
    else if (progress.days_remaining === 1) note = T.t('medication.one_day_left');
    else note = T.t('medication.days_left', { count: progress.days_remaining });
    card.appendChild(el('p', 'countdown', note));

    const meta = el('dl', 'meta-list');
    addMeta(meta, 'medication.start_date', F.dateLong(progress.started));
    addMeta(meta, 'medication.end_date', F.dateLong(progress.ends));
    card.appendChild(meta);
    return card;
  }

  /* --------------------------------------------------------------- today */
  function todaySection(medication) {
    const section = el('section', 'section');
    section.appendChild(el('h2', null, T.t('medication.today_doses')));

    const todayKey = F.inputDate(new Date());
    const doses = (medication.doses || []).filter(function (dose) {
      // Same rule as the Today screen: this is a list of things to do, and a
      // dose from before the medication was added is not one of them. It is
      // still in the dose timeline further down.
      return String(dose.scheduled_at).slice(0, 10) === todayKey
        && dose.status !== 'before_registration';
    });

    const list = el('div', 'stack');
    if (!doses.length) {
      list.appendChild(UI.emptyState('today.no_doses'));
    } else {
      doses.forEach(function (dose) {
        list.appendChild(C.doseRow(dose, render, { hideName: true, timeline: true }));
      });
    }
    section.appendChild(list);
    return section;
  }

  function countsRow(medication) {
    const counts = el('div', 'count-grid');
    const boxes = [['dose.count_taken', 'taken'], ['dose.count_skipped', 'skipped'],
                   ['dose.count_missed', 'missed'], ['dose.count_scheduled', 'scheduled']];
    // Only shown when there is something to show: most treatments are entered
    // on the day they start and have no history preceding them.
    if (medication.counts.before_registration) {
      boxes.push(['dose.count_before_registration', 'before_registration']);
    }
    boxes.forEach(function (pair) {
      const box = el('div');
      box.appendChild(el('strong', null, medication.counts[pair[1]] || 0));
      box.appendChild(el('span', null, T.t(pair[0])));
      counts.appendChild(box);
    });
    return counts;
  }

  /* Bookkeeping, not a verdict: how many of the doses the application could
     actually remind about were marked as taken. */
  function complianceCard(medication) {
    const data = medication.compliance;
    if (!data) return null;
    const card = el('section', 'card');
    card.appendChild(el('h2', null, T.t('medication.compliance')));
    card.appendChild(el('p', 'big-time',
      T.t('medication.compliance_value', { taken: data.taken, resolved: data.resolved })));

    const bar = el('div', 'progress');
    const fill = el('div', 'progress__bar');
    fill.style.width = data.percent + '%';
    bar.appendChild(fill);
    card.appendChild(bar);

    if (data.before_registration) {
      // Deliberately without a number: the count of doses *due* before
      // registration and the count of doses still *carrying* that status differ
      // once the user records what happened on one, and two adjacent numbers
      // that disagree read as a bug.
      card.appendChild(el('p', 'hint', T.t('medication.compliance_hint')));
    }
    return card;
  }

  /* ------------------------------------------------------------ timeline */
  function timelineSection(medication) {
    const section = el('section', 'section');
    const head = el('div', 'section__head');
    head.appendChild(el('h2', null, T.t('medication.dose_timeline')));
    section.appendChild(head);

    const filters = el('div', 'filters');
    const chips = [['all', 'medication.all_doses'], ['upcoming', 'medication.upcoming_only'],
                   ['past', 'medication.past_only']];
    if (medication.counts.before_registration) {
      chips.push(['before_registration', 'medication.before_registration_only']);
    }
    chips.forEach(function (pair) {
      const chip = el('button', 'chip' + (state.filter === pair[0] ? ' is-active' : ''), T.t(pair[1]));
      chip.type = 'button';
      chip.addEventListener('click', function () {
        state.filter = pair[0];
        render();
      });
      filters.appendChild(chip);
    });
    section.appendChild(filters);

    // Said once, above the list, instead of on every historical row.
    if (medication.counts.before_registration) {
      section.appendChild(el('p', 'hint', T.t('dose.before_registration_hint')));
    }

    const now = new Date();
    let doses = (medication.doses || []).slice();
    if (state.filter === 'upcoming') {
      doses = doses.filter(function (d) { return F.parse(d.scheduled_at) >= now; });
    } else if (state.filter === 'past') {
      doses = doses.filter(function (d) { return F.parse(d.scheduled_at) < now; }).reverse();
    } else if (state.filter === 'before_registration') {
      doses = doses.filter(function (d) { return d.status === 'before_registration'; });
    }

    const body = el('div', 'stack');
    if (!doses.length) {
      body.appendChild(UI.emptyState('dose.history_empty'));
      section.appendChild(body);
      return section;
    }

    // Grouped by day, newest-first for the past and oldest-first otherwise.
    let currentDay = null;
    doses.slice(0, 400).forEach(function (dose) {
      const day = String(dose.scheduled_at).slice(0, 10);
      if (day !== currentDay) {
        currentDay = day;
        body.appendChild(el('h3', 'timeline-day', F.dateLong(day)));
      }
      body.appendChild(C.doseRow(dose, render, { hideName: true, timeline: true }));
    });
    section.appendChild(body);
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
