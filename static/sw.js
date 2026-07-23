/* KurdRoom service worker — network first, offline fallback, self-updating.
   BUMP the version below whenever you want to force every device to drop
   its old cache on the next visit. */
const CACHE = "kurdroom-v12";
const OFFLINE_URL = "/offline";

self.addEventListener("install", (e) => {
  // precache the offline fallback page so it's available with no connection
  e.waitUntil(
    caches.open(CACHE).then((c) => c.add(OFFLINE_URL)).catch(() => {})
  );
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
      .catch(async () => {
        const cached = await caches.match(e.request);
        if (cached) return cached;                       // offline → cached copy
        // full-page navigation with nothing cached → show the offline page
        if (e.request.mode === "navigate") {
          const off = await caches.match(OFFLINE_URL);
          if (off) return off;
        }
        return Response.error();
      })
  );
});

/* ---------- push notifications (message, deadline, exam, friends…) ---------- */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch (err) {}
  e.waitUntil(self.registration.showNotification(d.title || "KurdRoom", {
    body: d.body || "",
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    tag: d.tag || "kurdroom",
    renotify: true,
    vibrate: [90, 40, 90],
    data: { url: d.url || "/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const wins = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) {
        try { await w.navigate(url); } catch (err) {}
        return w.focus();
      }
    }
    return clients.openWindow(url);
  })());
});
