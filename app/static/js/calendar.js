/* Calendar — month, week and day.
 *
 * The grid is built by hand; no calendar library. Every navigation asks the
 * backend for the visible range only, so a year of doses is never loaded.
 */
(function () {
  'use strict';

  const el = UI.el;
  const state = {
    view: 'month',
    anchor: new Date(),
    scope: 'all',
    medicationId: '',
    doctorId: '',
    medications: [],
    doctors: [],
  };

  const ICON = { dose: '💊', appointment: '🩺', treatment: '📅' };

  function isoDate(date) {
    return F.inputDate(date);
  }

  function shift(direction) {
    const d = new Date(state.anchor);
    if (state.view === 'month') {
      // Move from the 1st, never from the current day number: stepping from
      // August 31st would otherwise land on October 1st and skip September.
      d.setDate(1);
      d.setMonth(d.getMonth() + direction);
    } else if (state.view === 'week') {
      d.setDate(d.getDate() + 7 * direction);
    } else {
      d.setDate(d.getDate() + direction);
    }
    state.anchor = d;
  }

  /* ------------------------------------------------------------- loading */
  async function load() {
    const body = document.getElementById('calendar-body');
    const params = new URLSearchParams({
      view: state.view,
      anchor: isoDate(state.anchor),
      scope: state.scope,
    });
    if (state.medicationId) params.set('medication_id', state.medicationId);
    if (state.doctorId) params.set('doctor_id', state.doctorId);

    let data;
    try {
      data = await API.get('/api/calendar?' + params.toString());
    } catch (err) {
      body.textContent = '';
      body.appendChild(UI.emptyState('error.load_failed'));
      UI.notify.error(err);
      return;
    }

    document.getElementById('calendar-range').textContent = rangeLabel(data);
    body.textContent = '';
    const byDay = groupByDay(data.events);
    if (state.view === 'day') body.appendChild(dayView(data, byDay));
    else if (state.view === 'week') body.appendChild(weekView(data, byDay));
    else body.appendChild(monthView(data, byDay));
  }

  function rangeLabel(data) {
    const anchor = F.parse(data.anchor);
    if (state.view === 'day') return F.dateLong(anchor);
    if (state.view === 'week') {
      return T.t('calendar.week_of', { date: F.dateLong(F.parse(data.start)) });
    }
    const months = (T.catalog.format && T.catalog.format.months) || [];
    return (months[anchor.getMonth()] || '') + ' ' + anchor.getFullYear();
  }

  function groupByDay(events) {
    const map = {};
    events.forEach(function (event) {
      (map[event.date] = map[event.date] || []).push(event);
    });
    return map;
  }

  /* -------------------------------------------------------------- pieces */
  function eventChip(event) {
    const link = el('a', 'cal-event cal-event--' + event.type +
      (event.status ? ' cal-event--' + event.status : ''));
    link.href = event.href;

    const label = el('span', 'cal-event__label');
    label.textContent = ICON[event.type] + ' ' + event.title;
    link.appendChild(label);

    if (event.time) link.appendChild(el('span', 'cal-event__time', event.time));
    if (event.type === 'treatment') {
      // A short word here: the long form would squeeze the medication name out
      // of a calendar cell entirely. The full sentence stays in the tooltip.
      const full = T.t(event.boundary === 'start'
        ? 'calendar.treatment_starts' : 'calendar.treatment_ends');
      link.appendChild(el('span', 'cal-event__time',
        T.t(event.boundary === 'start'
          ? 'calendar.treatment_start_short' : 'calendar.treatment_end_short')));
      link.title = event.title + ' — ' + full;
    }
    return link;
  }

  function eventRow(event) {
    const row = el('div', 'dose-row dose-row--' +
      (event.type === 'dose' ? (event.status || 'scheduled') : 'scheduled'));
    row.appendChild(el('div', 'dose-row__time', event.time || '—'));
    const main = el('div', 'dose-row__main');
    main.appendChild(el('strong', null, ICON[event.type] + ' ' + event.title));
    if (event.type === 'treatment') {
      main.appendChild(el('div', 'card__meta',
        T.t(event.boundary === 'start' ? 'calendar.treatment_starts' : 'calendar.treatment_ends')));
    }
    if (event.subtitle) main.appendChild(el('div', 'card__meta', event.subtitle));
    row.appendChild(main);
    if (event.type === 'dose' && event.status) row.appendChild(UI.badge(event.status));

    const actions = el('div', 'dose-row__actions');
    const open = el('a', 'btn btn--sm btn--ghost', T.t('common.open'));
    open.href = event.href;
    actions.appendChild(open);
    row.appendChild(actions);
    return row;
  }

  /* --------------------------------------------------------------- views */
  function monthView(data, byDay) {
    const wrapper = el('div', 'cal');
    const weekdays = (T.catalog.format && T.catalog.format.weekdays) || [];
    const head = el('div', 'cal__head');
    weekdays.forEach(function (name) {
      head.appendChild(el('div', 'cal__weekday', name.slice(0, 3)));
    });
    wrapper.appendChild(head);

    const grid = el('div', 'cal__grid');
    const start = F.parse(data.start);
    const end = F.parse(data.end);
    const anchorMonth = F.parse(data.anchor).getMonth();
    const todayKey = isoDate(new Date());

    for (let day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) {
      const key = isoDate(day);
      const cell = el('div', 'cal__cell' +
        (day.getMonth() === anchorMonth ? '' : ' cal__cell--outside') +
        (key === todayKey ? ' cal__cell--today' : ''));

      const number = el('button', 'cal__daynum', String(day.getDate()));
      number.type = 'button';
      const dayCopy = new Date(day);
      number.addEventListener('click', function () {
        state.anchor = dayCopy;
        state.view = 'day';
        syncChips();
        load();
      });
      cell.appendChild(number);

      const events = byDay[key] || [];
      events.slice(0, 3).forEach(function (event) { cell.appendChild(eventChip(event)); });
      if (events.length > 3) {
        const more = el('button', 'cal__more', T.t('calendar.more', { count: events.length - 3 }));
        more.type = 'button';
        more.addEventListener('click', function () {
          state.anchor = dayCopy;
          state.view = 'day';
          syncChips();
          load();
        });
        cell.appendChild(more);
      }
      grid.appendChild(cell);
    }
    wrapper.appendChild(grid);

    if (!data.events.length) wrapper.appendChild(UI.emptyState('calendar.empty'));
    return wrapper;
  }

  function weekView(data, byDay) {
    const wrapper = el('div', 'stack');
    const start = F.parse(data.start);
    const end = F.parse(data.end);
    const weekdays = (T.catalog.format && T.catalog.format.weekdays) || [];
    const todayKey = isoDate(new Date());

    for (let day = new Date(start); day <= end; day.setDate(day.getDate() + 1)) {
      const key = isoDate(day);
      const card = el('article', 'card' + (key === todayKey ? ' card--accent' : ''));
      const index = (day.getDay() + 6) % 7;
      card.appendChild(el('h3', null, (weekdays[index] || '') + ' — ' + F.dateLong(day)));
      const events = byDay[key] || [];
      if (!events.length) {
        card.appendChild(el('p', 'card__meta', T.t('calendar.empty_day')));
      } else {
        const list = el('div', 'stack');
        events.forEach(function (event) { list.appendChild(eventRow(event)); });
        card.appendChild(list);
      }
      wrapper.appendChild(card);
    }
    return wrapper;
  }

  function dayView(data, byDay) {
    const wrapper = el('div', 'stack');
    const events = byDay[data.anchor] || [];
    if (!events.length) {
      wrapper.appendChild(UI.emptyState('calendar.empty_day'));
      return wrapper;
    }
    events.forEach(function (event) { wrapper.appendChild(eventRow(event)); });
    return wrapper;
  }

  /* --------------------------------------------------------------- setup */
  function syncChips() {
    document.querySelectorAll('#calendar-views .chip').forEach(function (chip) {
      chip.classList.toggle('is-active', chip.dataset.view === state.view);
    });
    document.querySelectorAll('#calendar-filters .chip').forEach(function (chip) {
      chip.classList.toggle('is-active', chip.dataset.scope === state.scope);
    });
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
      load();
    });
  }

  async function render() {
    try {
      const [meds, docs] = await Promise.all([
        API.get('/api/medications?status=all'),
        API.get('/api/doctors'),
      ]);
      state.medications = meds.items;
      state.doctors = docs.items;
    } catch (e) { /* filters are optional */ }

    fillSelect(document.getElementById('cal-medication'), state.medications,
      'calendar.all_medications', 'medicationId');
    fillSelect(document.getElementById('cal-doctor'), state.doctors,
      'calendar.all_doctors', 'doctorId');

    document.getElementById('calendar-views').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-view]');
      if (!chip) return;
      state.view = chip.dataset.view;
      syncChips();
      load();
    });
    document.getElementById('calendar-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-scope]');
      if (!chip) return;
      state.scope = chip.dataset.scope;
      syncChips();
      load();
    });
    document.getElementById('cal-prev').addEventListener('click', function () { shift(-1); load(); });
    document.getElementById('cal-next').addEventListener('click', function () { shift(1); load(); });
    document.getElementById('cal-today').addEventListener('click', function () {
      state.anchor = new Date();
      load();
    });

    const params = new URLSearchParams(window.location.search);
    if (params.get('view')) state.view = params.get('view');
    if (params.get('date')) state.anchor = F.parse(params.get('date')) || new Date();
    syncChips();
    await load();
  }

  UI.page(render);
})();
