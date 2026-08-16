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

  function doseSummary(item) {
    return T.t('medication.dose_summary', {
      dose: F.dose(item.dose_amount, item.dose_unit),
      quantity: F.quantity(item.quantity, item.form),
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
  }

  /* Page bootstrap: wait for translations, then run the page's render(). */
  function page(render) {
    document.addEventListener('DOMContentLoaded', function () {
      setupChrome();
      T.ready()
        .then(function () {
          Notifications.start();
          return render();
        })
        .catch(function (err) {
          console.error(err);
          notify.error(err instanceof Error ? err : 'error.load_failed');
        });
    });
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
  };
})();
