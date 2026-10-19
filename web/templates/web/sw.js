/* Murimi Farm ERP service worker: keeps the app itself on the phone so it opens with no network.
   Records and data are handled by app.js (it saves pages it has seen and queues work done offline). */
const VERSION = 'murimi-erp-{{ v }}';
const SHELL = ['/', '/static/web/app.js?v={{ v }}', '/static/web/app.css?v={{ v }}', '/static/web/manifest.webmanifest',
  '/static/web/icons/icon-192.png', '/static/web/icons/icon-512.png', '/static/web/icons/favicon-32.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith('murimi-erp-') && k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.origin === location.origin && (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin'))) return;
  if (e.request.mode === 'navigate') {
    // The page: try the network (newest version), fall back to the saved copy.
    e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(VERSION).then((c) => c.put('/', copy)); return r; })
      .catch(() => caches.match('/')));
    return;
  }
  // Scripts, styles, icons and fonts: saved copy first, refreshed in the background.
  e.respondWith(caches.match(e.request).then((hit) => {
    const net = fetch(e.request).then((r) => {
      if (r.ok || r.type === 'opaque') { const copy = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copy)); }
      return r;
    }).catch(() => hit);
    return hit || net;
  }));
});
