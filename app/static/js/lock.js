/* The lock screen.
 *
 * The only page served while the application is locked, and the only one that
 * can unlock it. It shows nothing about the user's treatments — just a PIN box.
 */
(function () {
  'use strict';

  const el = UI.el;

  function form() { return document.getElementById('lock-form'); }
  function input() { return document.getElementById('lock-pin'); }

  /* Where to go after unlocking.
   *
   * `?next=` comes from the URL, so it is not to be trusted: "//evil.example"
   * starts with a slash and is a perfectly good link to somewhere else
   * entirely. Resolving it and checking the origin is the only honest test.
   */
  function safeNext() {
    const raw = new URLSearchParams(window.location.search).get('next');
    if (!raw) return '/';
    try {
      const url = new URL(raw, window.location.origin);
      if (url.origin !== window.location.origin) return '/';
      if (url.pathname === '/lock') return '/';
      return url.pathname + url.search;
    } catch (e) {
      return '/';
    }
  }

  /* A wrong PIN eventually buys the guesser a wait; count it down rather than
     leaving the button dead with no explanation. */
  let countdown = null;

  function holdFor(seconds) {
    const button = document.getElementById('lock-submit');
    const hint = document.getElementById('lock-hint');
    clearInterval(countdown);
    let left = seconds;

    function tick() {
      if (left <= 0) {
        clearInterval(countdown);
        button.disabled = false;
        input().disabled = false;
        hint.textContent = T.t('lock.hint');
        return;
      }
      button.disabled = true;
      input().disabled = true;
      hint.textContent = T.t('lock.locked_out', { seconds: left });
      left -= 1;
    }

    tick();
    countdown = setInterval(tick, 1000);
  }

  async function submit(event) {
    event.preventDefault();
    UI.clearErrors(form());
    const button = document.getElementById('lock-submit');
    button.disabled = true;

    try {
      await API.post('/api/lock/unlock', { pin: input().value });
      // Straight in, and to wherever they were trying to go if we know it.
      window.location.href = safeNext();
    } catch (err) {
      input().value = '';
      if (err instanceof API.ApiError && err.fields && err.fields.pin) {
        UI.showErrors(form(), err.fields);
        if (err.fields.pin === 'validation.pin_locked_out') {
          const state = await API.get('/api/lock/state').catch(function () { return null; });
          holdFor((state && state.retry_in_seconds) || 30);
          return;
        }
      } else {
        UI.notify.error(err);
      }
      button.disabled = false;
      input().focus();
    }
  }

  async function render() {
    form().addEventListener('submit', submit);
    input().focus();

    // If the lock was turned off in another window, do not strand the user here.
    try {
      const state = await API.get('/api/lock/state');
      if (!state.locked) { window.location.href = '/'; return; }
      if (state.retry_in_seconds) holdFor(state.retry_in_seconds);
    } catch (err) { /* the lock screen still works without this */ }
  }

  UI.page(render);
})();
