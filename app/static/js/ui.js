/* Shared UI helpers: toasts, confirm dialog, form errors, small builders. */
(function () {
  'use strict';

  /* ---------------------------------------------------------------- toasts */
  function toast(message, kind, timeout) {
    const stack = document.getElementById('toast-stack');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = 'toast toast--' + (kind || 'info');
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(function () { el.remove(); }, timeout || 4000);
  }

  const notify = {
    success: function (key, params) { toast(T.t(key, params), 'success'); },
    error: function (keyOrError) {
      const key = keyOrError instanceof Error ? (keyOrError.key || 'error.generic') : keyOrError;
      toast(T.t(key), 'error', 6000);
    },
    info: function (key, params) { toast(T.t(key, params), 'info'); },
    warning: function (key, params) { toast(T.t(key, params), 'warning'); },
    raw: toast,
  };

  /* --------------------------------------------------------------- confirm */
  function confirmDialog(titleKey, bodyKey, params, acceptKey, danger) {
    return new Promise(function (resolve) {
      const dialog = document.getElementById('confirm-dialog');
      if (!dialog || typeof dialog.showModal !== 'function') {
        resolve(window.confirm(T.t(bodyKey, params)));
        return;
      }
      document.getElementById('confirm-title').textContent = T.t(titleKey, params);
      document.getElementById('confirm-body').textContent = T.t(bodyKey, params);
      const accept = document.getElementById('confirm-accept');
      accept.textContent = T.t(acceptKey || 'common.confirm');
      accept.className = 'btn ' + (danger === false ? 'btn--primary' : 'btn--danger');

      function onClose() {
        dialog.removeEventListener('close', onClose);
        resolve(dialog.returnValue === 'confirm');
      }
      dialog.addEventListener('close', onClose);
      dialog.returnValue = '';
      dialog.showModal();
    });
  }

  /* ----------------------------------------------------------- form errors */
  function clearErrors(form) {
    form.querySelectorAll('[data-error-for]').forEach(function (el) { el.textContent = ''; });
    form.querySelectorAll('.field--invalid').forEach(function (el) { el.classList.remove('field--invalid'); });
  }

  function showErrors(form, fields) {
    clearErrors(form);
    Object.keys(fields || {}).forEach(function (name) {
      const holder = form.querySelector('[data-error-for="' + name + '"]');
      if (holder) {
        holder.textContent = T.t(fields[name]);
        const field = holder.closest('.field');
        if (field) field.classList.add('field--invalid');
      }
    });
  }

  /* -------------------------------------------------------------- builders */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function badge(status) {
    return el('span', 'badge badge--' + status, T.t('status.' + status));
  }

  function emptyState(messageKey, hintKey) {
    const box = el('div', 'empty');
    box.appendChild(el('p', null, T.t(messageKey)));
    if (hintKey) box.appendChild(el('p', 'muted', T.t(hintKey)));
    return box;
  }

  function thumb(medication, large) {
    if (medication.image_url) {
      const img = el('img', 'med-thumb' + (large ? ' med-thumb--lg' : ''));
      img.src = medication.image_url;
      img.alt = medication.name || '';
      return img;
    }
    const initial = (medication.name || medication.medication_name || '?').trim().charAt(0).toUpperCase();
    return el('div', 'med-thumb' + (large ? ' med-thumb--lg' : ''), initial);
  }

  /* "500 mg — 1 capsule". Dose and quantity are both optional since v2, so
     whichever half is missing is simply left out. */
  function doseSummary(item) {
    const parts = [];
    if (item.dose_amount) parts.push(F.dose(item.dose_amount, item.dose_unit));
    if (item.quantity) parts.push(F.quantity(item.quantity, item.form));
    if (!parts.length) return '';
    if (parts.length === 1) return parts[0];
    return T.t('medication.dose_summary', { dose: parts[0], quantity: parts[1] });
  }

  /* Appearance. The server owns the preference; localStorage is only a mirror
     so the inline script in base.html can apply it before the first paint. */
  function normalizeTheme(theme) {
    return ['light', 'dark', 'system'].indexOf(theme) === -1 ? 'system' : theme;
  }

  /* Show a theme without committing to it: used while the Settings select is
     being played with, so an unsaved choice cannot outlive the page. */
  function previewTheme(theme) {
    document.documentElement.setAttribute('data-theme', normalizeTheme(theme));
  }

  function applyTheme(theme) {
    const value = normalizeTheme(theme);
    previewTheme(value);
    try { localStorage.setItem('medtracker-theme', value); } catch (e) { /* private mode */ }
  }

  /* The unread badge on the bell, refreshed after anything that could change it. */
  async function refreshBell() {
    const badge = document.getElementById('bell-count');
    if (!badge) return;
    try {
      const data = await API.get('/api/notifications/unread-count', { poll: true });
      const count = Number(data.unread || 0);
      badge.textContent = count > 99 ? '99+' : String(count);
      badge.classList.toggle('hidden', count === 0);
    } catch (e) { /* the badge is decoration; never break a page over it */ }
  }

  function setupSearch() {
    const form = document.getElementById('topsearch');
    if (!form) return;
    form.addEventListener('submit', function (event) {
      event.preventDefault();
      const value = document.getElementById('topsearch-input').value.trim();
      if (value) window.location.href = '/search?q=' + encodeURIComponent(value);
    });
  }

  function setupChrome() {
    const toggle = document.getElementById('nav-toggle');
    const nav = document.getElementById('main-nav');
    if (toggle && nav) {
      toggle.addEventListener('click', function () {
        const open = nav.classList.toggle('is-open');
        toggle.setAttribute('aria-expanded', String(open));
      });
    }
    document.addEventListener('click', function (event) {
      const closer = event.target.closest('[data-close-dialog]');
      if (closer) {
        const dialog = closer.closest('dialog');
        if (dialog) dialog.close('cancel');
      }
    });
    setupSearch();
  }

  /* Telling the server that a person is actually here.
   *
   * Auto-lock cannot measure idleness from requests: the page polls itself
   * every thirty seconds whether anyone is in the room or not, so an open tab
   * would keep the application unlocked all day. Real input is reported
   * instead — a click, a key, a turn of the wheel — throttled hard, because
   * this is meant to be a heartbeat and nothing more.
   */
  const ACTIVITY_EVERY_MS = 60000;
  let lastActivity = 0;

  function reportActivity() {
    const now = Date.now();
    if (now - lastActivity < ACTIVITY_EVERY_MS) return;
    lastActivity = now;
    API.post('/api/lock/activity').catch(function () { /* never user-visible */ });
  }

  function setupActivity() {
    // Not on the lock screen. Typing a PIN is input, and reporting it would
    // only ever earn a 423 — there is no session to keep alive yet.
    if (document.body.classList.contains('locked')) return;
    // Attached whatever the settings say, rather than only when the lock is
    // already on. `T.settings` is a snapshot taken when the page loaded, and
    // the page where somebody turns the lock on is the settings page they are
    // still sitting on — reading a stale snapshot there would mean the very
    // first auto-lock threw them out mid-click. The ping is throttled to once
    // a minute and the server ignores it when there is no lock, so attaching
    // it always costs nothing worth counting.
    ['pointerdown', 'keydown', 'wheel', 'mousemove'].forEach(function (name) {
      document.addEventListener(name, reportActivity, { passive: true });
    });
  }

  /* Page bootstrap: wait for translations, then run the page's render().
   *
   * Every step around `render()` is optional scenery — the browser notification
   * poller, the unread badge — so none of them is allowed to stop the page from
   * appearing. And if `render()` itself fails, the user gets an explanation and
   * a Retry button rather than a blank screen.
   */
  function page(render) {
    document.addEventListener('DOMContentLoaded', function () {
      try { setupChrome(); } catch (e) { console.error(e); }

      T.ready()
        .then(function () {
          applyTheme((T.settings && T.settings.theme) || 'system');
          // The lock screen loads neither of these, and a page that is still
          // locked would only get a 423 from them.
          if (window.Notifications && !document.body.classList.contains('locked')) {
            try { Notifications.start(); } catch (e) { console.error(e); }
          }
          try { setupActivity(); } catch (e) { console.error(e); }
          refreshBell();
          document.addEventListener('medtracker:notified', refreshBell);
          return render();
        })
        .catch(function (err) {
          console.error(err);
          showPageError(err, function () { window.location.reload(); });
        });
    });
  }

  /* The error boundary for a whole screen: say what failed and offer a way out.
   * A locked application is not an error - it is a redirect. */
  function showPageError(err, retry) {
    if (err instanceof API.ApiError && err.status === 423) {
      window.location.href = '/lock?next=' + encodeURIComponent(window.location.pathname);
      return;
    }
    const main = document.getElementById('main') || document.body;
    main.textContent = '';
    const box = el('div', 'empty');
    box.appendChild(el('h2', null, T.t('error.page_title')));
    box.appendChild(el('p', 'muted',
      err instanceof API.ApiError ? T.t(err.key) : T.t('error.load_failed')));
    if (retry) {
      const button = el('button', 'btn btn--primary', T.t('common.retry'));
      button.type = 'button';
      button.addEventListener('click', retry);
      box.appendChild(button);
    }
    main.appendChild(box);
    notify.error(err instanceof Error ? err : 'error.load_failed');
  }

  /* The same idea for one section of a page: replace just that part. */
  function sectionError(container, err, retry) {
    if (err instanceof API.ApiError && err.status === 423) {
      window.location.href = '/lock?next=' + encodeURIComponent(window.location.pathname);
      return container;
    }
    container.textContent = '';
    const box = el('div', 'empty');
    box.appendChild(el('p', null,
      err instanceof API.ApiError ? T.t(err.key) : T.t('error.load_failed')));
    if (retry) {
      const button = el('button', 'btn btn--sm btn--ghost', T.t('common.retry'));
      button.type = 'button';
      button.addEventListener('click', retry);
      box.appendChild(button);
    }
    container.appendChild(box);
    return container;
  }

  window.UI = {
    notify: notify,
    confirm: confirmDialog,
    clearErrors: clearErrors,
    showErrors: showErrors,
    el: el,
    badge: badge,
    emptyState: emptyState,
    thumb: thumb,
    doseSummary: doseSummary,
    page: page,
    showPageError: showPageError,
    sectionError: sectionError,
    applyTheme: applyTheme,
    previewTheme: previewTheme,
    refreshBell: refreshBell,
  };
})();
