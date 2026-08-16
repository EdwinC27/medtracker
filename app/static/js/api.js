/* Thin fetch wrapper.
 *
 * Server errors arrive as {error: "<translation key>", fields: {...}}. They are
 * re-thrown as an ApiError so callers can highlight form fields, and the
 * message the user sees is always translated — never a stack trace.
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

  async function request(method, url, body, options) {
    let response;
    try {
      const init = { method: method, headers: {} };
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

    if (!response.ok) {
      const key = (payload && payload.error) || (response.status === 404 ? 'error.not_found' : 'error.generic');
      throw new ApiError(key, payload && payload.fields, response.status);
    }
    return payload;
  }

  window.API = {
    ApiError: ApiError,
    get: function (url) { return request('GET', url); },
    post: function (url, body) { return request('POST', url, body === undefined ? {} : body); },
    put: function (url, body) { return request('PUT', url, body); },
    del: function (url) { return request('DELETE', url); },
    upload: function (url, formData) { return request('POST', url, formData); },
  };
})();
