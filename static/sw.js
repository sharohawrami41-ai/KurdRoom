/* KurdRoom service worker — v7.
   BUMP the version below whenever you want to force every device to drop
   its old cache on the next visit.

   Caching strategy (this is what makes the app feel fast):
     • fonts, icons, sounds  -> cache FIRST, they never change
     • pages and API calls   -> network first, cached copy as the offline net
*/
const CACHE = "kurdroom-v40";
const OFFLINE_URL = "/offline";

/* files worth having before they are ever asked for */
const PRECACHE = [
  OFFLINE_URL,
  "/static/icon-192.png",
  "/static/sounds/chime.wav",
];

const STATIC_RE = /\.(?:otf|ttf|woff2?|png|jpe?g|webp|gif|svg|ico|wav|mp3|mp4)$/i;
/* never cache these — they must always be live */
const NO_CACHE_RE = /^\/(api\/|dl|push\/)|\/(chat_poll|poll)$/;

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => Promise.allSettled(PRECACHE.map((u) => c.add(u))))
      .catch(() => {})
  );
  self.skipWaiting();               // new version activates immediately
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch (_) {}
    }
    await clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;      // leave third parties alone
  if (NO_CACHE_RE.test(url.pathname)) return;

  // ---- static assets: serve from cache instantly, refresh quietly behind it
  if (STATIC_RE.test(url.pathname)) {
    e.respondWith((async () => {
      const hit = await caches.match(req);
      const net = fetch(req).then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return resp;
      }).catch(() => hit);
      return hit || net;
    })());
    return;
  }

  // ---- pages: always try the network, keep a copy for offline
  e.respondWith((async () => {
    try {
      const pre = e.preloadResponse ? await e.preloadResponse : null;
      const resp = pre || await fetch(req);
      if (resp && resp.ok && resp.type === "basic") {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return resp;
    } catch (err) {
      const cached = await caches.match(req);
      if (cached) return cached;
      if (req.mode === "navigate") {
        const off = await caches.match(OFFLINE_URL);
        if (off) return off;
      }
      return Response.error();
    }
  })());
});

/* ---------- push notifications (message, deadline, exam, friends…) ----------
   Web push cannot pick a custom ringtone by itself, so we do the next best
   thing: a distinctive vibration signature, an alert that re-fires when a new
   one of the same kind arrives, quick actions, and — when the app happens to
   be open — the account's chosen tone plays inside the page. */
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch (err) {}

  const loud = !!d.loud;
  const vibrate = d.vibrate || (loud ? [70, 45, 70, 45, 140] : [55, 60, 55]);

  e.waitUntil((async () => {
    // if a window is open, let the page play the tone itself
    const wins = await clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      try { w.postMessage({ type: "kr-alert", sound: d.sound || "chime", loud }); }
      catch (_) {}
    }
    await self.registration.showNotification(d.title || "KurdRoom", {
      body: d.body || "",
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      tag: d.tag || "kurdroom",
      renotify: true,
      requireInteraction: loud,
      silent: false,
      vibrate,
      timestamp: Date.now(),
      data: { url: d.url || "/", sound: d.sound || "chime" },
      actions: loud
        ? [{ action: "open", title: "Open" }, { action: "close", title: "Later" }]
        : [{ action: "open", title: "Open" }],
    });
  })());
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  if (e.action === "close") return;
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

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "kr-skip-waiting") self.skipWaiting();
});
