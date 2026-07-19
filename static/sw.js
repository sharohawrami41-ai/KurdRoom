/* KurdRoom service worker — network first, offline fallback, self-updating.
   BUMP the version below whenever you want to force every device to drop
   its old cache on the next visit. */
const CACHE = "kurdroom-v2";

self.addEventListener("install", () => {
  self.skipWaiting();               // new version activates immediately
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    // delete every cache from older versions
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    await clients.claim();          // take control of all open pages now
  })());
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return resp;                // always prefer the fresh network copy
      })
      .catch(() => caches.match(e.request))   // offline → cached copy
  );
});
