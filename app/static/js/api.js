/* Thin fetch wrapper.
 *
 * Server errors arrive as {error: "<translation key>", fields: {...}}. They are
 * re-thrown as an ApiError so callers can highlight form fields, and the
 * message the user sees is always translated — never a stack trace.
 *
 * Two headers go out on every call:
 *
 *   X-Requested-With  — a browser will not send a custom header to another
 *                       origin without asking that origin first, so this is
 *                       what lets the server tell "the application" apart from
 *                       "some other website using the same browser". See
 *                       app/routes/origin.py.
 *   X-Medtracker-Poll — set only by the background pollers, so the server can
 *                       tell traffic apart from a person actually being there.
 *
 * And one response is special everywhere: 423 means the application locked
 * while the page was open. Whatever the caller was doing, the answer is the
 * lock screen.
 */
(function () {
  'use strict';

  class ApiError extends Error {
    constructor(key, fields, status) {
      super(key);
      this.key = key || 'error.generic';
      this.fields = fields || {};
      this.status = status;
    }
    get text() { return T.t(this.key); }
  }

  /* Which screen this is.
   *
   * A random string kept in this browser's own storage. The server uses it to
   * remember which reminders this device has already shown — without it, the
   * first device to ask took the reminder and the phone silently stopped being
   * reminded of anything whenever the computer was awake. It identifies a
   * browser, never a person, and is used for nothing else. */
  const CLIENT_KEY = 'medtracker-client-id';
  let clientId = null;

  function client() {
    if (clientId) return clientId;
    try {
      clientId = localStorage.getItem(CLIENT_KEY);
      if (!clientId) {
        const bytes = new Uint8Array(16);
        (window.crypto || {}).getRandomValues
          ? window.crypto.getRandomValues(bytes)
          : bytes.forEach(function (_, i) { bytes[i] = Math.floor(Math.random() * 256); });
        clientId = Array.from(bytes, function (b) {
          return b.toString(16).padStart(2, '0');
        }).join('');
        localStorage.setItem(CLIENT_KEY, clientId);
      }
    } catch (e) {
      // Private mode, or storage refused. Falling back to one id per page load
      // is worse than a stable one but still better than none: this tab will
      // at least not take the phone's reminders.
      clientId = clientId || String(Math.random()).slice(2) + String(Math.random()).slice(2);
    }
    return clientId;
  }

  let goingToLock = false;

  function goToLock() {
    if (goingToLock) return;                 // several calls can fail at once
    // Already here. Reloading the lock screen would wipe whatever it is
    // showing — including "Incorrect PIN" — every time something on it gets a
    // 423, which is most of what it asks for.
    if (window.location.pathname === '/lock') return;
    goingToLock = true;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.replace('/lock?next=' + next);
  }

  async function request(method, url, body, options) {
    let response;
    try {
      const init = {
        method: method,
        headers: { 'X-Requested-With': 'MedTracker', 'X-Medtracker-Client': client() },
      };
      if (options && options.poll) init.headers['X-Medtracker-Poll'] = '1';
      if (body instanceof FormData) {
        init.body = body;
      } else if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      response = await fetch(url, init);
    } catch (err) {
      throw new ApiError('error.network', {}, 0);
    }

    let payload = null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
      try { payload = await response.json(); } catch (e) { payload = null; }
    }

    if (response.status === 423) {
      // The application locked underneath this page. Leave, before the medical
      // data already on screen sits there for whoever walks up next.
      goToLock();
      throw new ApiError('error.locked', {}, 423);
    }

    if (!response.ok) {
      const key = (payload && payload.error) || (response.status === 404 ? 'error.not_found' : 'error.generic');
      throw new ApiError(key, payload && payload.fields, response.status);
    }
    return payload;
  }

  window.API = {
    ApiError: ApiError,
    clientId: client,
    get: function (url, options) { return request('GET', url, undefined, options); },
    post: function (url, body, options) {
      return request('POST', url, body === undefined ? {} : body, options);
    },
    put: function (url, body) { return request('PUT', url, body); },
    del: function (url) { return request('DELETE', url); },
    upload: function (url, formData) { return request('POST', url, formData); },
  };
})();
