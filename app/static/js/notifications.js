/* Browser notifications.
 *
 * The page asks the server every 30 s for reminders that have not been shown in
 * a browser yet, displays them with the Notification API, and tells the server
 * they were delivered. The queue lives in SQLite, so anything that fired while
 * the browser was closed is shown the next time the app is opened.
 *
 * The *reliable* channel while the browser is closed is the Windows toast sent
 * by the background scheduler — this file is the in-page complement, not the
 * mechanism the reminders depend on.
 */
(function () {
  'use strict';

  const POLL_MS = 30000;
  let timer = null;
  let started = false;

  function supported() { return 'Notification' in window; }

  function permission() { return supported() ? Notification.permission : 'unsupported'; }

  async function requestPermission() {
    if (!supported()) return 'unsupported';
    if (Notification.permission === 'granted' || Notification.permission === 'denied') {
      return Notification.permission;
    }
    try { return await Notification.requestPermission(); }
    catch (e) { return Notification.permission; }
  }

  function show(item) {
    try {
      const note = new Notification(item.title, {
        body: item.body,
        tag: 'medtracker-' + item.id,
        icon: '/static/img/icon.svg',
        requireInteraction: false,
      });
      note.onclick = function () {
        window.focus();
        window.location.href = item.type === 'appointment' ? '/appointments' : '/';
        note.close();
      };
    } catch (e) {
      /* Some browsers throw when the page is not visible; the toast in the app
         (below) still tells the user. */
      UI.notify.raw(item.title + ' — ' + item.body, 'info', 8000);
    }
  }

  async function poll() {
    try {
      const data = await API.get('/api/notifications/pending', { poll: true });
      const items = (data && data.items) || [];
      if (!items.length) return;

      const canShow = supported() && Notification.permission === 'granted';
      items.forEach(function (item) {
        if (canShow) show(item);
        else UI.notify.raw(item.title + ' — ' + item.body, 'info', 9000);
      });
      await API.post('/api/notifications/delivered',
        { ids: items.map(function (i) { return i.id; }) }, { poll: true });
      document.dispatchEvent(new CustomEvent('medtracker:notified'));
    } catch (e) {
      /* Offline or the server stopped — stay quiet, the next tick retries. */
    }
  }

  function start() {
    if (started) return;
    started = true;
    if (T.settings && T.settings.browser_notifications === false) return;
    poll();
    timer = setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    started = false;
  }

  window.Notifications = {
    start: start,
    stop: stop,
    poll: poll,
    supported: supported,
    permission: permission,
    requestPermission: requestPermission,
  };
})();
