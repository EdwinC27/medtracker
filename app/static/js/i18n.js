/* Translation layer for the browser.
 *
 * The catalog is fetched once from /api/bootstrap (the same JSON the server
 * uses), so there is exactly one source of truth for every string. No visible
 * text is hard-coded in the JavaScript: everything goes through T.t().
 */
(function () {
  'use strict';

  const state = {
    language: document.body.dataset.language || 'en',
    catalog: {},
    settings: {},
    options: { frequencies: [], units: [], forms: [] },
    languages: [],
    ready: null,
  };

  function lookup(key) {
    let node = state.catalog;
    for (const part of String(key).split('.')) {
      if (node == null || typeof node !== 'object' || !(part in node)) return null;
      node = node[part];
    }
    return node;
  }

  function t(key, params) {
    let value = lookup(key);
    if (value === null || value === undefined) return key;
    if (typeof value !== 'string') return value;
    if (params) {
      for (const name of Object.keys(params)) {
        value = value.split('{' + name + '}').join(String(params[name]));
      }
    }
    return value;
  }

  /* Replace the content of every [data-i18n] element inside `root`. */
  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.dataset.i18n);
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.title = t(el.dataset.i18nTitle);
    });
    scope.querySelectorAll('[data-i18n-aria-label]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
    });
    if (!root) {
      document.title = t('app.name') + ' — ' + t('app.tagline');
      document.documentElement.lang = state.language;
    }
  }

  async function load() {
    const response = await fetch('/api/bootstrap', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error('bootstrap failed');
    const data = await response.json();
    state.language = data.language;
    state.catalog = data.catalog;
    state.settings = data.settings;
    state.options = data.options;
    state.languages = data.languages;
    document.body.dataset.language = data.language;
    apply();
    markActiveNav();
    return data;
  }

  function markActiveNav() {
    const page = document.body.dataset.page;
    document.querySelectorAll('[data-nav]').forEach(function (link) {
      link.classList.toggle('is-active', link.dataset.nav === page);
    });
  }

  /* Every page awaits this before rendering anything. */
  function ready() {
    if (!state.ready) state.ready = load();
    return state.ready;
  }

  window.T = {
    t: t,
    apply: apply,
    ready: ready,
    reload: function () { state.ready = load(); return state.ready; },
    get language() { return state.language; },
    get settings() { return state.settings; },
    get options() { return state.options; },
    get languages() { return state.languages; },
    get catalog() { return state.catalog; },
  };
})();
