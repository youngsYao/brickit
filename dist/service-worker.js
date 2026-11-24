const CACHE_PREFIX = 'brick-it-shell-';
const CACHE_NAME = `${CACHE_PREFIX}1`;
const scope = new URL(self.registration.scope);
const shell = new URL('index.html', scope).href;
const assetUrls = [
  'index.html', 'style.css', 'ai-settings.css?v=2', 'app.js?v=settings-2', 'ai-settings.js?v=2',
  'ui-art.js', 'icons.js', 'generation.js', 'data.js', 'works.js', 'project-storage.js',
  'parts-catalog.js', 'model.js', 'ldraw-preview.js', 'examples/songen.js', 'examples/generated.js',
  'assets/fonts/outfit-latin-wght-normal.woff2',
  'assets/vendor/three.module.js', 'assets/vendor/three.core.js', 'assets/vendor/RoomEnvironment.js',
  'assets/vendor/RoundedBoxGeometry.js', 'assets/vendor/BufferGeometryUtils.js',
  'assets/vendor/LDrawLoader.js', 'assets/vendor/LDrawConditionalLineMaterial.js',
].map(path => new URL(path, scope).href);
const corePaths = new Set(assetUrls.map(url => new URL(url).pathname));

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(assetUrls.map(url => new Request(url, { cache: 'reload' })))));
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(name => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME).map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  const path = url.pathname.slice(scope.pathname.length);
  const navigation = request.mode === 'navigate';
  if (navigation ? path !== '' && path !== 'index.html' :
    !corePaths.has(url.pathname) && !path.startsWith('assets/') && !path.startsWith('examples/')) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const key = navigation ? shell : request;
    try {
      const response = await fetch(request);
      if (response.ok) await cache.put(key, response.clone()).catch(() => {});
      return response;
    } catch (error) {
      const saved = await cache.match(key);
      if (saved) return saved;
      throw error;
    }
  })());
});
