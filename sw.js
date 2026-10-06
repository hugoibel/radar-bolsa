/* Service worker de Radar Bolsa.
   La app (HTML, CSS, JS, iconos) se sirve desde caché para que abra al instante
   y sin conexión. Los datos van primero a la red (siempre lo más fresco) y solo
   si no hay conexión se usa la última copia guardada. */
const VERSION = 'radar-bolsa-v6';
const APP = [
  './', './index.html', './css/app.css', './js/app.js', './manifest.webmanifest',
  './img/icon-180.png', './img/icon-192.png', './img/icon-512.png',
];

self.addEventListener('install', ev => {
  ev.waitUntil(caches.open(VERSION).then(c => c.addAll(APP)).then(() => self.skipWaiting()).catch(() => {}));
});

self.addEventListener('activate', ev => {
  ev.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', ev => {
  if (ev.request.method !== 'GET') return;
  const url = new URL(ev.request.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.includes('/data/')) {
    ev.respondWith(
      fetch(ev.request).then(r => {
        if (r.ok) { const copia = r.clone(); caches.open(VERSION).then(c => c.put(ev.request, copia)); }
        return r;
      }).catch(() => caches.match(ev.request, { ignoreSearch: true }))
    );
    return;
  }

  ev.respondWith(
    caches.match(ev.request).then(guardado => {
      const red = fetch(ev.request).then(r => {
        if (r && r.ok) { const copia = r.clone(); caches.open(VERSION).then(c => c.put(ev.request, copia)); }
        return r;
      }).catch(() => guardado);
      return guardado || red;
    })
  );
});
