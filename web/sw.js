// Offline support: the app shell is cached; API reads are network-first, and the
// last good answer is shown (marked with x-sabi-cache) when the phone is offline.
const SHELL = "sabi-shell-v2";
const DATA = "sabi-data-v1";
const SHELL_FILES = ["./", "index.html", "app.css", "app.js", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => ![SHELL, DATA].includes(k)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    e.respondWith((async () => {
      try {
        const res = await fetch(e.request);
        if (res.ok) (await caches.open(DATA)).put(e.request, res.clone());
        return res;
      } catch {
        const cached = await (await caches.open(DATA)).match(e.request);
        if (!cached) throw new Error("offline and nothing saved");
        const headers = new Headers(cached.headers);
        headers.set("x-sabi-cache", "1");
        return new Response(await cached.blob(), { status: cached.status, headers });
      }
    })());
    return;
  }

  // App shell: network first so updates arrive at once; the cached copy only when offline.
  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      if (res.ok) (await caches.open(SHELL)).put(e.request, res.clone());
      return res;
    } catch {
      const hit = await caches.match(e.request);
      if (hit) return hit;
      throw new Error("offline");
    }
  })());
});
