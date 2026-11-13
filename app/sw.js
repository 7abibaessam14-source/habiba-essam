// AmperEC service worker: keeps the app's own files so it opens fast (live data always comes from the network),
// and shows the phone notifications sent by the Supabase Edge Function "send-push".
const CACHE = "amperec-v3";
const FILES = ["./", "index.html", "style.css", "app.js", "config.js", "manifest.webmanifest",
               "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
// network first for the app's own files (so updates show up), cache only when offline; never touch Supabase calls
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});

self.addEventListener("push", (e) => {
  let msg = { title: "AmperEC", body: "New alert" };
  try { msg = { ...msg, ...e.data.json() }; } catch (x) { if (e.data) msg.body = e.data.text(); }
  e.waitUntil(self.registration.showNotification(msg.title, {
    body: msg.body,
    icon: "icons/icon-192.png",
    badge: "icons/icon-192.png",
    tag: msg.tag || "amperec",
    data: { url: "./?tab=alerts" },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) return c.focus(); }
    return self.clients.openWindow(e.notification.data.url);
  }));
});
