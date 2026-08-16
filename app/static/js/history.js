/* History: every medication with its treatment window, status and dose log. */
(function () {
  'use strict';

  const el = UI.el;
  const state = { filter: 'all', expanded: {} };

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
    [['dose.count_taken', 'taken'], ['dose.count_skipped', 'skipped'],
     ['dose.count_missed', 'missed'], ['dose.count_scheduled', 'scheduled']].forEach(function (pair) {
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

  async function render() {
    document.getElementById('history-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-filter]');
      if (!chip) return;
      state.filter = chip.dataset.filter;
      this.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('is-active', c === chip); });
      load();
    });
    await load();
  }

  UI.page(render);
})();
