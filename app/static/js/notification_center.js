/* Notification centre.
 *
 * The application's own record of every reminder it generated, kept separately
 * from whether Windows, the browser or e-mail managed to deliver it.
 */
(function () {
  'use strict';

  const el = UI.el;
  const PAGE = 30;
  const state = { unreadOnly: false, offset: 0, total: 0 };

  const ICON = { dose: '💊', appointment: '🩺' };

  async function load(append) {
    const list = document.getElementById('notification-list');
    if (!append) {
      state.offset = 0;
      list.textContent = '';
    }

    const params = new URLSearchParams({
      limit: String(PAGE),
      offset: String(state.offset),
      unread_only: state.unreadOnly ? 'true' : 'false',
    });
    const data = await API.get('/api/notifications/history?' + params.toString());
    state.total = data.total;

    document.getElementById('notification-summary').textContent =
      T.t('notification_center.unread', { count: data.unread });

    if (!data.items.length && !append) {
      list.appendChild(UI.emptyState('notification_center.empty', 'notification_center.empty_hint'));
    }
    data.items.forEach(function (item) { list.appendChild(row(item)); });

    state.offset += data.items.length;
    const more = document.getElementById('load-more');
    more.classList.toggle('hidden', data.items.length < PAGE);
    UI.refreshBell();
  }

  function row(item) {
    const card = el('article', 'card notification' + (item.read ? '' : ' notification--unread'));

    const head = el('div', 'card__title');
    head.appendChild(el('span', 'notification__icon', ICON[item.type] || '🔔'));
    const info = el('div');
    info.appendChild(el('strong', null, item.title));
    info.appendChild(el('div', 'card__meta', F.dateTime(item.fire_at)));
    head.appendChild(info);
    card.appendChild(head);

    // The body may contain newlines; keep them.
    const body = el('p', 'notification__body');
    body.textContent = item.body;
    card.appendChild(body);

    const footer = el('div', 'card__footer');
    [['windows', 'notification_center.channel_windows'],
     ['browser', 'notification_center.channel_browser'],
     ['email', 'notification_center.channel_email']].forEach(function (pair) {
      const when = item.delivery[pair[0]];
      if (!when) return;
      footer.appendChild(el('span', 'badge badge--completed',
        T.t(pair[1]) + ' · ' + F.time(when)));
    });
    if (item.delivery.error) {
      footer.appendChild(el('span', 'badge badge--missed', T.t('notification_center.not_delivered')));
    }
    if (!item.read) {
      const mark = el('button', 'btn btn--sm btn--ghost', T.t('notification_center.mark_read'));
      mark.type = 'button';
      mark.addEventListener('click', async function () {
        await API.post('/api/notifications/read', { ids: [item.id] });
        load(false);
      });
      footer.appendChild(mark);
    }
    card.appendChild(footer);
    return card;
  }

  async function render() {
    document.getElementById('notification-filters').addEventListener('click', function (event) {
      const chip = event.target.closest('[data-unread]');
      if (!chip) return;
      state.unreadOnly = chip.dataset.unread === '1';
      this.querySelectorAll('.chip').forEach(function (c) {
        c.classList.toggle('is-active', c === chip);
      });
      load(false);
    });

    document.getElementById('mark-all-read').addEventListener('click', async function () {
      try {
        await API.post('/api/notifications/read', {});
        UI.notify.success('notification_center.all_read');
        load(false);
      } catch (err) { UI.notify.error(err); }
    });

    document.getElementById('load-more').addEventListener('click', function () { load(true); });

    await load(false);
  }

  UI.page(render);
})();
