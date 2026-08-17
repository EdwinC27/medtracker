/* Keeping every open screen up to date.
 *
 * The computer and the phone are two browsers looking at one database. This
 * listens to the server's change stream and reloads the current screen as soon
 * as anything is written — from here, from the other device, or by the
 * background scheduler.
 *
 * Three things it refuses to do, because each one would be worse than being a
 * few seconds out of date:
 *
 *   - reload while a dialog is open, which would pull the form out from under
 *     somebody in the middle of filling it in;
 *   - reload while the cursor is in a field, for the same reason;
 *   - keep trying for ever. If the stream cannot be held open the page still
 *     has its own timer, so failing quietly is a complete answer.
 */
(function () {
  'use strict';

  const RETRY_MS = 5000;
  const SETTLE_MS = 300;

  let source = null;
  let refresh = null;
  let pending = false;
  let timer = null;
  let retry = null;

  /* Is the person in the middle of something? */
  function busy() {
    if (document.querySelector('dialog[open]')) return true;
    const active = document.activeElement;
    if (!active) return false;
    if (active.isContentEditable) return true;
    return ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(active.tagName) !== -1;
  }

  function run() {
    timer = null;
    if (!refresh) return;
    if (busy()) {                    // try again once they are done
      timer = setTimeout(run, SETTLE_MS * 4);
      return;
    }
    pending = false;
    try {
      const result = refresh();
      if (result && typeof result.catch === 'function') {
        result.catch(function (err) { console.debug('live refresh:', err); });
      }
    } catch (err) {
      console.debug('live refresh:', err);
    }
  }

  function schedule() {
    pending = true;
    if (timer) return;               // several changes at once, one reload
    timer = setTimeout(run, SETTLE_MS);
  }

  function connect() {
    if (source || typeof window.EventSource !== 'function') return;
    try {
      source = new EventSource('/api/events');
    } catch (e) {
      return;                        // the page's own timer still works
    }

    source.addEventListener('changed', function () {
      schedule();
      // A scheduler pass that queued a reminder is a change like any other, so
      // the reminder can arrive in a second instead of waiting out the
      // thirty-second poll. That matters most on the phone, where this alert is
      // the only thing the browser will let us show.
      document.dispatchEvent(new CustomEvent('medtracker:changed'));
    });

    source.addEventListener('locked', function () {
      close();
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.replace('/lock?next=' + next);
    });

    source.onerror = function () {
      // EventSource reconnects by itself, but a server that has gone away
      // leaves it retrying in a tight loop; closing and rearming is calmer.
      close();
      if (!retry) {
        retry = setTimeout(function () { retry = null; connect(); }, RETRY_MS);
      }
    };
  }

  function close() {
    if (source) {
      try { source.close(); } catch (e) { /* already gone */ }
      source = null;
    }
  }

  /* A tab that comes back from the background may have missed everything that
     happened while it was asleep, so it reloads once on the way in. */
  function onVisible() {
    if (document.visibilityState !== 'visible') return;
    connect();
    schedule();
  }

  window.Live = {
    start: function (render) {
      refresh = render;
      connect();
      document.addEventListener('visibilitychange', onVisible);
      window.addEventListener('pagehide', close);
    },
    stop: close,
    // For the tests and the console: has a reload been asked for and deferred?
    isPending: function () { return pending; },
  };
})();
