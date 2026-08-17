/* Settings, the v4 additions: the three tabs, Security and System Status.
 *
 * Kept in its own file so `settings.js` — which already handles the general
 * form, e-mail, backups and export/import — did not have to be reorganised
 * around the tabs. Both scripts run on the same page and touch different parts
 * of it.
 */
(function () {
  'use strict';

  const el = UI.el;
  const TABS = ['general', 'security', 'status'];

  /* ----------------------------------------------------------------- tabs */
  function showTab(name) {
    const tab = TABS.indexOf(name) === -1 ? 'general' : name;
    TABS.forEach(function (key) {
      const panel = document.getElementById('tab-' + key);
      if (panel) panel.classList.toggle('hidden', key !== tab);
    });
    document.querySelectorAll('#settings-tabs .chip').forEach(function (chip) {
      chip.classList.toggle('is-active', chip.dataset.tab === tab);
    });
    // Keep the address bar honest, so the tray's "System status" link and a
    // reload both land on the same place.
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', tab);
      window.history.replaceState({}, '', url);
    } catch (e) { /* not worth failing over */ }

    if (tab === 'status') loadStatus();
    if (tab === 'security') loadLock();
  }

  /* -------------------------------------------------------------- security */
  const AUTO_LOCK_LABEL = {
    0: function () { return T.t('security.auto_lock_never'); },
    60: function () { return T.t('security.auto_lock_hour'); },
  };

  function autoLockLabel(minutes) {
    if (AUTO_LOCK_LABEL[minutes]) return AUTO_LOCK_LABEL[minutes]();
    return T.t('security.auto_lock_minutes', { minutes: minutes });
  }

  async function loadLock() {
    let state;
    try {
      state = await API.get('/api/lock/state');
    } catch (err) {
      UI.sectionError(document.getElementById('tab-security'), err, loadLock);
      return;
    }

    const badge = document.getElementById('lock-state-badge');
    badge.textContent = T.t(state.enabled ? 'security.status_on' : 'security.status_off');
    badge.className = 'badge ' + (state.enabled ? 'badge--active' : 'badge--completed');

    document.getElementById('lock-enable-form').classList.toggle('hidden', state.enabled);
    document.getElementById('lock-on-panel').classList.toggle('hidden', !state.enabled);
    document.getElementById('lock-pin-rule').textContent =
      T.t('security.pin_rule', { min: state.pin_min_length, max: state.pin_max_length });

    const select = document.getElementById('lock-auto');
    select.textContent = '';
    (state.auto_lock_options || [0]).forEach(function (minutes) {
      const option = el('option', null, autoLockLabel(minutes));
      option.value = minutes;
      select.appendChild(option);
    });
    select.value = String(state.auto_lock_minutes || 0);
  }

  function bindLockForm(id, url, onDone) {
    const form = document.getElementById(id);
    if (!form) return;
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      UI.clearErrors(form);
      const button = form.querySelector('button[type=submit]');
      button.disabled = true;
      const payload = {};
      form.querySelectorAll('input').forEach(function (input) {
        payload[input.name] = input.value;
      });
      try {
        const result = await API.post(url, payload);
        form.reset();
        if (result.message) UI.notify.success(result.message);
        await loadLock();
        if (onDone) onDone(result);
      } catch (err) {
        if (err instanceof API.ApiError && Object.keys(err.fields || {}).length) {
          UI.showErrors(form, err.fields);
          UI.notify.error('error.validation');
        } else {
          UI.notify.error(err);
        }
      } finally {
        button.disabled = false;
      }
    });
  }

  /* ---------------------------------------------------------------- status */
  const LEVEL_MARK = { ok: '🟢', warning: '🟡', error: '🔴', disabled: '⚪' };

  function fact(list, labelKey, value) {
    if (value === null || value === undefined || value === '') return;
    list.appendChild(el('dt', null, T.t(labelKey)));
    list.appendChild(el('dd', null, String(value)));
  }

  function uptime(seconds) {
    if (!seconds && seconds !== 0) return null;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return T.t('status.uptime_value', { hours: hours, minutes: minutes });
  }

  function when(value) {
    return value ? F.dateTime(value) : null;
  }

  // A recorded failure is stored as a translation key whenever the application
  // recognised the failure, and as raw text only when it did not. Both end up
  // on the same line, so decide here which one this is.
  function reason(value) {
    if (!value) return null;
    return /^(error|validation|message|status)\.[a-z0-9_]+$/.test(value) ? T.t(value) : value;
  }

  function componentCard(item) {
    const card = el('article', 'card status-card status-card--' + item.level);

    const head = el('div', 'status-card__head');
    head.appendChild(el('span', 'status-card__mark', LEVEL_MARK[item.level] || '⚪'));
    head.appendChild(el('h3', null, T.t('status.' + item.key)));
    card.appendChild(head);

    if (item.detail_key) {
      card.appendChild(el('p', 'status-card__detail', T.t(item.detail_key)));
    }

    const facts = el('dl', 'meta-list');
    switch (item.key) {
      case 'application':
        fact(facts, 'status.version', item.version);
        fact(facts, 'status.uptime', uptime(item.uptime_seconds));
        break;
      case 'database':
        fact(facts, 'status.schema_version', item.schema_version);
        fact(facts, 'status.location', item.path);
        break;
      case 'scheduler':
        fact(facts, 'status.last_run', when(item.last_run));
        fact(facts, 'status.next_run', when(item.next_run));
        if (item.next_dose) {
          fact(facts, 'status.next_dose',
            F.dateTime(item.next_dose.scheduled_at) + ' — ' + (item.next_dose.medication || ''));
        }
        fact(facts, 'status.reason', reason(item.last_error));
        break;
      case 'windows_notifications':
        fact(facts, 'status.reason', reason(item.reason));
        break;
      case 'email_notifications':
        fact(facts, 'status.server', item.host ? item.host + ':' + item.port : null);
        fact(facts, 'status.recipient', item.recipient);
        break;
      case 'backup':
        fact(facts, 'status.frequency', item.frequency);
        fact(facts, 'status.last_backup', when(item.last_backup_at));
        fact(facts, 'status.next_backup', when(item.next_backup_at));
        fact(facts, 'status.location', item.location);
        fact(facts, 'status.reason', reason(item.last_error));
        break;
      case 'network':
        fact(facts, 'status.network_listening', item.listening_on);
        fact(facts, 'status.network_https',
          T.t(item.https ? 'status.network_https_on' : 'status.network_https_off'));
        if ((item.addresses || []).length) {
          // The whole point of this card: the address to type into the phone,
          // read off the screen instead of guessed from `ipconfig`.
          facts.appendChild(el('dt', null, T.t('status.network_addresses')));
          const holder = el('dd');
          item.addresses.forEach(function (url) {
            const link = el('a', 'mono', url);
            link.href = url;
            link.style.display = 'block';
            holder.appendChild(link);
          });
          facts.appendChild(holder);
        }
        break;
      case 'startup':
        fact(facts, 'status.reason', reason(item.error));
        break;
      default:
        break;
    }
    if (facts.childElementCount) card.appendChild(facts);
    return card;
  }

  async function loadStatus() {
    const body = document.getElementById('status-body');
    let data;
    try {
      data = await API.get('/api/system/status');
    } catch (err) {
      UI.sectionError(body, err, loadStatus);
      return;
    }

    body.textContent = '';

    const summary = el('div', 'card status-summary status-card--' + data.overall);
    summary.appendChild(el('span', 'status-card__mark', LEVEL_MARK[data.overall] || '⚪'));
    const summaryText = el('div');
    summaryText.appendChild(el('strong', null, T.t('status.overall_' + data.overall)));
    summaryText.appendChild(el('div', 'card__meta',
      T.t('status.generated_at', { time: F.time(data.generated_at) })));
    summary.appendChild(summaryText);
    body.appendChild(summary);

    body.appendChild(el('p', 'hint', T.t('status.read_only')));

    const grid = el('div', 'grid grid--cards');
    (data.components || []).forEach(function (item) {
      grid.appendChild(componentCard(item));
    });
    body.appendChild(grid);
  }

  /* ----------------------------------------------------------------- setup */
  function render() {
    const tabs = document.getElementById('settings-tabs');
    if (!tabs) return;

    tabs.addEventListener('click', function (event) {
      const chip = event.target.closest('[data-tab]');
      if (chip) showTab(chip.dataset.tab);
    });

    bindLockForm('lock-enable-form', '/api/lock/enable');
    bindLockForm('lock-change-form', '/api/lock/change');
    bindLockForm('lock-disable-form', '/api/lock/disable');

    const auto = document.getElementById('lock-auto');
    if (auto) {
      auto.addEventListener('change', async function () {
        try {
          await API.post('/api/lock/auto', { auto_lock_minutes: Number(this.value) });
          UI.notify.success('message.settings_saved');
        } catch (err) { UI.notify.error(err); }
      });
    }

    const lockNow = document.getElementById('lock-now');
    if (lockNow) {
      lockNow.addEventListener('click', async function () {
        try {
          await API.post('/api/lock/lock');
          window.location.href = '/lock';
        } catch (err) { UI.notify.error(err); }
      });
    }

    showTab(new URLSearchParams(window.location.search).get('tab') || 'general');
  }

  // settings.js owns UI.page() on this screen, so this one waits for the
  // translations the same way and then wires up its own parts.
  document.addEventListener('DOMContentLoaded', function () {
    T.ready().then(render).catch(function (err) { console.error(err); });
  });
})();
