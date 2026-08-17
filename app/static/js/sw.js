/* The service worker — the only way an Android phone can put a reminder in its
 * notification shade.
 *
 * Deliberately tiny. It caches nothing, intercepts no requests and holds no
 * state: making the application work offline is a different job with different
 * risks, and a service worker that starts serving stale medical data from a
 * cache is the last thing this application should do. All it does is exist, so
 * that the page can call `registration.showNotification()` through it, and
 * handle the tap that follows.
 *
 * It is served from the site root rather than from `/static/`, because a worker
 * can only ever act for pages inside its own path — from `/static/sw.js` it
 * would be able to do nothing for `/` or `/medications`.
 */
'use strict';

self.addEventListener('install', function (event) {
  // Take over immediately rather than waiting for every tab to be closed: the
  // point of registering is the notification the user is waiting for now.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('notificationclick', function (event) {
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.notification.close();

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (windows) {
        // Prefer a tab that is already open — the user has one, that is how the
        // reminder reached them — and only open a new one if there is none.
        for (const client of windows) {
          if ('focus' in client) {
            client.navigate(target).catch(function () { /* older browsers */ });
            return client.focus();
          }
        }
        if (self.clients.openWindow) return self.clients.openWindow(target);
        return undefined;
      })
  );
});
