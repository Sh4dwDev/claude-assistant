// Service worker: makes the app installable, loads the shell offline, and shows push notifications.
const CACHE = "shell-v1";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/icons/icon.svg", "/static/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// App shell: network first (always fresh), cache as fallback. API calls are never cached.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data.json(); } catch { data = { title: "Assistant", body: e.data?.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "Assistant", {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = e.notification.data?.url || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if ("focus" in c) return c.focus();
    return self.clients.openWindow(target);
  }));
});
