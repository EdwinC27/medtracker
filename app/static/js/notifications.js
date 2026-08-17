/* Browser notifications.
 *
 * The page asks the server every 30 s for reminders that have not been shown in
 * a browser yet, displays them, and tells the server they were delivered. The
 * queue lives in SQLite, so anything that fired while the browser was closed is
 * shown the next time the app is opened.
 *
 * Two ways of showing them, and which one is used is not a preference:
 *
 *   - An operating-system notification, where the browser allows one.
 *   - Otherwise the on-screen alert in `screenalert.js` — a banner that stays,
 *     with a sound and a vibration. That is the phone's case: a page served
 *     over plain http:// on the local network is not a secure context, and
 *     browsers refuse to grant notification permission there at all.
 *
 * The *reliable* channel while the browser is closed is the Windows toast sent
 * by the background scheduler, and e-mail; this file is the in-page complement,
 * not the mechanism the reminders depend on.
 */
(function () {
  'use strict';

  const POLL_MS = 30000;
  let timer = null;
  let started = false;
  let worker = null;         // the service worker registration, when there is one

  /* An Android phone cannot be given a notification through `new Notification`
     — the constructor throws there and points at a service worker instead. So
     one is registered when the browser allows it, which it only does on a
     secure page. That is what the HTTPS setting is for. */
  async function registerWorker() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      worker = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      await navigator.serviceWorker.ready;
      return worker;
    } catch (e) {
      console.debug('service worker:', e);
      worker = null;
      return null;
    }
  }

  function supported() { return 'Notification' in window; }

  function permission() { return supported() ? Notification.permission : 'unsupported'; }

  async function requestPermission() {
    if (!supported()) return 'unsupported';
    if (Notification.permission === 'granted' || Notification.permission === 'denied') {
      return Notification.permission;
    }
    try {
      const answer = await Notification.requestPermission();
      // Register only after the user has said yes: a worker on a page nobody
      // has agreed to be notified by has nothing to do.
      if (answer === 'granted') await registerWorker();
      return answer;
    } catch (e) {
      return Notification.permission;
    }
  }

  function url(item) {
    return item.type === 'appointment' ? '/appointments' : '/';
  }

  /* Through the service worker: the only path that reaches the notification
     shade on Android, and the one that survives the tab being in the
     background. */
  async function showViaWorker(item) {
    if (!worker || !worker.showNotification) return false;
    try {
      await worker.showNotification(item.title, {
        body: item.body,
        tag: 'medtracker-' + item.id,
        icon: '/static/img/icon.svg',
        badge: '/static/img/icon.svg',
        requireInteraction: true,
        vibrate: [250, 120, 250],
        data: { url: url(item) },
      });
      return true;
    } catch (e) {
      console.debug('showNotification:', e);
      return false;
    }
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
        window.location.href = url(item);
        note.close();
      };
      return true;
    } catch (e) {
      /* Android throws outright here — the constructor is not usable at all and
         wants a service worker, which needs a secure context we do not have.
         Fall through to something the page can do by itself. */
      return false;
    }
  }

  async function announce(item) {
    // In order of how well each one actually reaches a person who is not
    // looking at the screen. Each step falls through to the next only when it
    // genuinely could not deliver.
    if (supported() && !ScreenAlert.needed()) {
      if (await showViaWorker(item)) return;
      if (show(item)) return;
    }
    ScreenAlert.show(item);
  }

  async function poll() {
    try {
      const data = await API.get('/api/notifications/pending', { poll: true });
      const items = (data && data.items) || [];
      if (!items.length) return;

      items.forEach(announce);
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
    if (Notification.permission === 'granted') registerWorker();
    poll();
    timer = setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
    // The timer is the floor, not the mechanism: when the server says something
    // changed, ask straight away. A dose reminder should not wait out a poll.
    document.addEventListener('medtracker:changed', function () { poll(); });
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
    started = false;
  }

  window.Notifications = {
    start: start,
    registerWorker: registerWorker,
    hasWorker: function () { return worker !== null; },
    stop: stop,
    poll: poll,
    announce: announce,
    supported: supported,
    permission: permission,
    requestPermission: requestPermission,
  };
})();
