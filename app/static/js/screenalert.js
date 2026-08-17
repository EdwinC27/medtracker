/* The reminder you can actually see, on a device that will not show one.
 *
 * A phone reaches this application at `http://192.168.x.x:8000`, and a plain
 * http:// origin is not a "secure context". Browsers switch a lot off there,
 * and among the things they switch off are exactly the ones a reminder needs:
 * `Notification.requestPermission()` is refused, and there is no service worker
 * to show one through. Measured, not assumed — see the report; the phone simply
 * cannot be given an operating-system notification by this application over the
 * local network.
 *
 * What *is* left on a page that is open: the screen, the speaker and the
 * vibration motor. So this is a reminder built out of those three. It is not a
 * consolation prize dressed up as a notification — it is loud, it stays until
 * it is dismissed, and it says plainly that it only works while the page is
 * open, which is the honest limit of what the browser allows here.
 *
 * The sound is synthesised rather than played from a file: no asset to ship, no
 * media element for an autoplay policy to block, and it still works with the
 * application installed on a machine with no internet at all.
 */
(function () {
  'use strict';

  const el = UI.el;

  let audio = null;          // created on the first real user gesture
  let primed = false;

  /* ------------------------------------------------------------------ sound */
  /* Browsers refuse to make noise until the user has interacted with the page,
     so the audio context is opened on the first touch and kept. Without this
     the very first reminder of a session would be silent — which is the one
     that matters most. */
  function primeAudio() {
    if (primed) return;
    primed = true;
    try {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return;
      audio = new Ctor();
      if (audio.state === 'suspended') audio.resume();
    } catch (e) {
      audio = null;
    }
  }

  function beep(when, frequency) {
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = frequency;
    // A short envelope instead of a square-edged blip, which clicks.
    gain.gain.setValueAtTime(0.0001, when);
    gain.gain.exponentialRampToValueAtTime(0.35, when + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + 0.28);
    oscillator.connect(gain);
    gain.connect(audio.destination);
    oscillator.start(when);
    oscillator.stop(when + 0.3);
  }

  function chime() {
    if (!audio) return false;
    try {
      if (audio.state === 'suspended') audio.resume();
      const now = audio.currentTime;
      beep(now, 880);
      beep(now + 0.35, 1174.66);
      return true;
    } catch (e) {
      return false;
    }
  }

  function buzz() {
    try {
      if (navigator.vibrate) return navigator.vibrate([250, 120, 250]);
    } catch (e) { /* not every device has one, and none of them owe us this */ }
    return false;
  }

  /* ----------------------------------------------------------------- banner */
  function stack() {
    let box = document.getElementById('alert-stack');
    if (!box) {
      box = el('div', 'alert-stack');
      box.id = 'alert-stack';
      box.setAttribute('role', 'alert');
      box.setAttribute('aria-live', 'assertive');
      document.body.appendChild(box);
    }
    return box;
  }

  function show(item) {
    const box = stack();

    // The same reminder twice — two tabs, or a reconnect — is one reminder.
    const id = 'alert-' + item.id;
    if (document.getElementById(id)) return;

    const card = el('div', 'alert');
    card.id = id;

    const body = el('div', 'alert__body');
    body.appendChild(el('strong', 'alert__title', item.title || ''));
    body.appendChild(el('p', 'alert__text', item.body || ''));
    card.appendChild(body);

    const actions = el('div', 'alert__actions');
    const open = el('a', 'btn btn--primary btn--sm', T.t('alert.open'));
    open.href = item.type === 'appointment' ? '/appointments' : '/';
    actions.appendChild(open);

    const close = el('button', 'btn btn--ghost btn--sm', T.t('alert.dismiss'));
    close.type = 'button';
    close.addEventListener('click', function () { card.remove(); });
    actions.appendChild(close);
    card.appendChild(actions);

    box.appendChild(card);
    // Deliberately not on a timer. A dose reminder that removed itself after a
    // few seconds is the behaviour that made the phone feel like it never
    // notified anything.

    chime();
    buzz();
    return card;
  }

  function clear() {
    const box = document.getElementById('alert-stack');
    if (box) box.textContent = '';
  }

  /* Whether the browser will show a real notification, or whether this is the
     only way the user is going to see anything. */
  function needed() {
    if (!('Notification' in window)) return true;
    if (!window.isSecureContext) return true;      // a phone on http://
    return Notification.permission !== 'granted';
  }

  window.ScreenAlert = {
    show: show,
    clear: clear,
    needed: needed,
    primeAudio: primeAudio,
    canSound: function () { return audio !== null; },
  };

  // One listener, removed as soon as it has done its job.
  ['pointerdown', 'keydown', 'touchstart'].forEach(function (name) {
    document.addEventListener(name, primeAudio, { once: true, passive: true });
  });
})();
