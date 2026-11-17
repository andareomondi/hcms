// HCMS service worker.
// Caches only the static app shell (CSS/JS/icons). It never intercepts or caches
// HTML pages, API responses, or anything under the app's own URLs, so no patient
// data is stored in the browser.

const CACHE_NAME = "hcms-shell-v1";

const SHELL_ASSETS = [
  "/static/js/tailwind.js",
  "/static/js/htmx.min.js",
  "/static/js/alpine.min.js",
  "/static/js/chart.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin GET requests for static files.
  if (event.request.method !== "GET" || url.origin !== self.location.origin)
    return;
  if (!url.pathname.startsWith("/static/")) return;

  event.respondWith(
    caches
      .match(event.request)
      .then((cached) => cached || fetch(event.request)),
  );
});
