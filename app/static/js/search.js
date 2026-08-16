/* Global search. Read-only: results only ever link somewhere. */
(function () {
  'use strict';

  const el = UI.el;
  let timer = null;

  async function run(query) {
    const box = document.getElementById('search-results');
    const hint = document.getElementById('search-hint');
    box.textContent = '';
    // The "type at least two characters" hint is only useful before then.
    if (hint) hint.classList.toggle('hidden', query.trim().length >= 2);
    if (query.trim().length < 2) return;

    let data;
    try {
      data = await API.get('/api/search?q=' + encodeURIComponent(query));
    } catch (err) {
      UI.notify.error(err);
      return;
    }

    if (!data.total) {
      box.appendChild(UI.emptyState('search.no_results'));
      box.querySelector('.empty p').textContent = T.t('search.no_results', { query: data.query });
      return;
    }

    const head = el('p', 'muted', T.t('search.results_for', { query: data.query }) +
      ' — ' + T.t(data.total === 1 ? 'search.total_one' : 'search.total',
                   { count: data.total }));
    box.appendChild(head);

    if (data.doctors.length) {
      box.appendChild(group('doctor.title', data.doctors, function (item) {
        const lines = [];
        if (item.occupation) lines.push(item.occupation);
        if (item.phone) lines.push(item.phone);
        return lines.join(' · ');
      }));
    }
    if (data.appointments.length) {
      box.appendChild(group('appointment.title', data.appointments, function (item) {
        const lines = [F.dateTime(item.scheduled_at)];
        if (item.treatment) lines.push(item.treatment);
        return lines.join(' · ');
      }, function (item) { return item.doctor_name || ''; }));
    }
    if (data.medications.length) {
      box.appendChild(group('medication.title', data.medications, function (item) {
        const lines = [];
        const summary = UI.doseSummary(item);
        if (summary) lines.push(summary);
        lines.push(F.frequency(item.frequency_hours));
        if (item.comments) lines.push(item.comments);
        return lines.join(' · ');
      }));
    }
  }

  function group(titleKey, items, describe, titleOf) {
    const section = el('section', 'section');
    section.appendChild(el('h2', null, T.t(titleKey)));
    const list = el('div', 'stack');
    items.forEach(function (item) {
      const row = el('a', 'card search-hit');
      row.href = item.href;
      row.appendChild(el('strong', null, titleOf ? titleOf(item) : item.name));
      const description = describe(item);
      if (description) row.appendChild(el('div', 'card__meta', description));
      if (item.status) {
        const footer = el('div', 'card__footer');
        footer.appendChild(UI.badge(item.status));
        row.appendChild(footer);
      }
      list.appendChild(row);
    });
    section.appendChild(list);
    return section;
  }

  async function render() {
    const input = document.getElementById('search-input');
    const initial = new URLSearchParams(window.location.search).get('q') || '';
    input.value = initial;

    input.addEventListener('input', function () {
      clearTimeout(timer);
      const value = this.value;
      timer = setTimeout(function () { run(value); }, 220);
    });
    input.focus();
    if (initial) await run(initial);
  }

  UI.page(render);
})();
