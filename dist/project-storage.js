// V1 matches the server's SHA-256 input: three little-endian uint32 header
// fields (version, coordinate count, index count), float64 coordinates, then uint32 indices.
const DB_NAME = 'brick-it-library';
const PROJECTS = 'projects';
const MESHES = 'meshAssets';
const REFERENCES = 'meshHashes';
const PART_FIELDS = ['parts', 'originalParts'];
const FALLBACK_KEY = 'brick.it.library.v1';
let dbPromise, backend = 'indexeddb';

export class LibraryStorageError extends Error {
  constructor(message, cause) { super(message, { cause }); this.name = 'LibraryStorageError'; }
}

function encodeMesh({ positions, indices }) {
  const buffer = new ArrayBuffer(12 + 8 * positions.length + 4 * indices.length);
  const view = new DataView(buffer);
  view.setUint32(0, 1, true);
  view.setUint32(4, positions.length, true);
  view.setUint32(8, indices.length, true);
  let offset = 12;
  for (const value of positions) { view.setFloat64(offset, value, true); offset += 8; }
  for (const index of indices) { view.setUint32(offset, index, true); offset += 4; }
  return buffer;
}

function decodeMesh(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 12) throw new LibraryStorageError('本地作品缺少零件网格。');
  const view = new DataView(buffer);
  const coordinates = view.getUint32(4, true), faces = view.getUint32(8, true);
  if (view.getUint32(0, true) !== 1 || coordinates < 9 || faces < 3 || coordinates % 3 || faces % 3 ||
      coordinates > 9000000 || faces > 9000000 || buffer.byteLength !== 12 + 8 * coordinates + 4 * faces) {
    throw new LibraryStorageError('本地作品的零件网格格式无效。');
  }
  const positions = Array.from({ length: coordinates }, (_, i) => view.getFloat64(12 + 8 * i, true));
  const indices = Array.from({ length: faces }, (_, i) => view.getUint32(12 + 8 * coordinates + 4 * i, true));
  return { positions, indices };
}

async function packProject(project) {
  const assets = new Map();
  const crypto = globalThis.crypto?.subtle;
  if (!crypto) return { record: project, assets };
  const record = { ...project };
  for (const field of PART_FIELDS) {
    if (!project[field]) continue;
    record[field] = await Promise.all(project[field].map(async part => {
      const { mesh, ...metadata } = part;
      const { positions, indices, ...meshMetadata } = mesh;
      const payload = encodeMesh(mesh);
      const digest = await crypto.digest('SHA-256', payload);
      const meshSha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
      assets.set(meshSha256, payload);
      if (Object.keys(meshMetadata).length) metadata.mesh = meshMetadata;
      return { ...metadata, meshSha256 };
    }));
  }
  if (assets.size) record.meshHashes = [...assets.keys()].sort();
  return { record, assets };
}

function hydrateProjects(records, getPayload) {
  const decoded = new Map();
  return records.map(record => {
    if (!record.meshHashes) return record;
    const { meshHashes, ...project } = record;
    for (const field of PART_FIELDS) {
      if (!project[field]) continue;
      project[field] = project[field].map(({ meshSha256, mesh, ...part }) => {
        if (!meshSha256) return mesh ? { ...part, mesh } : part;
        if (!decoded.has(meshSha256)) decoded.set(meshSha256, decodeMesh(getPayload(meshSha256)));
        return { ...part, mesh: { ...mesh, ...decoded.get(meshSha256) } };
      });
    }
    return project;
  });
}

function openDatabase() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 2);
    let settled = false;
    const timer = setTimeout(() => fail(new Error('IndexedDB open timeout')), 3500);
    function fail(error) { settled = true; clearTimeout(timer); reject(error); }
    request.onupgradeneeded = () => {
      if (settled) { request.transaction.abort(); return; }
      const db = request.result;
      const projects = db.objectStoreNames.contains(PROJECTS)
        ? request.transaction.objectStore(PROJECTS) : db.createObjectStore(PROJECTS, { keyPath: 'id' });
      projects.createIndex(REFERENCES, REFERENCES, { multiEntry: true });
      db.createObjectStore(MESHES, { keyPath: 'sha256' });
    };
    request.onsuccess = () => {
      const db = request.result;
      if (settled) { db.close(); return; }
      clearTimeout(timer);
      db.onversionchange = db.onclose = () => { db.close(); dbPromise = undefined; };
      resolve(db);
    };
    request.onerror = () => fail(request.error);
  }).catch(error => { dbPromise = undefined; throw error; });
  return dbPromise;
}

async function database() {
  const db = typeof indexedDB !== 'undefined' ? await openDatabase().catch(() => null) : null;
  backend = db ? 'indexeddb' : 'localstorage';
  return db;
}

function transaction(db, mode, run) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction([PROJECTS, MESHES], mode);
    let error, result;
    const guard = action => {
      try { action(); } catch (cause) { error = cause; tx.abort(); }
    };
    const onSuccess = (request, callback) => { request.onsuccess = () => guard(() => callback(request.result)); };
    tx.oncomplete = () => resolve(result);
    tx.onabort = () => reject(new LibraryStorageError('无法完成本地作品读写，请检查浏览器存储空间。', error || tx.error));
    guard(() => run(tx.objectStore(PROJECTS), tx.objectStore(MESHES), onSuccess, value => { result = value; }));
  });
}

function readIndexed(db, id) {
  return transaction(db, 'readonly', (projects, meshes, onSuccess, finish) => {
    onSuccess(id === undefined ? projects.getAll() : projects.get(id), value => {
      const records = id === undefined ? value : value ? [value] : [];
      const payloads = new Map();
      const hashes = new Set(records.flatMap(record => record.meshHashes || []));
      for (const hash of hashes) onSuccess(meshes.get(hash), asset => payloads.set(hash, asset?.payload));
      finish({ records, payloads });
    });
  });
}

// Compare the observed record before an asynchronous migration so another tab's
// save or deletion cannot be overwritten, even if both records share a timestamp.
function sameRecord(left, right, seen = new WeakMap()) {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object' ||
      Object.getPrototypeOf(left) !== Object.getPrototypeOf(right)) return false;
  if (left instanceof Date) return Object.is(left.getTime(), right.getTime());
  if (!Array.isArray(left) && Object.getPrototypeOf(left) !== Object.prototype) return false;
  if (seen.has(left)) return seen.get(left) === right;
  seen.set(left, right);
  const keys = Object.keys(left);
  return (!Array.isArray(left) || left.length === right.length) && keys.length === Object.keys(right).length &&
    keys.every(key => Object.hasOwn(right, key) && sameRecord(left[key], right[key], seen));
}

function writeIndexed(db, id, entry, shouldWrite = () => true) {
  return transaction(db, 'readwrite', (projects, meshes, onSuccess, finish) => {
    onSuccess(projects.get(id), previous => {
      if (!shouldWrite(previous)) { finish(false); return; }
      if (entry) {
        for (const [sha256, payload] of entry.assets) {
          onSuccess(meshes.getKey(sha256), key => { if (key === undefined) meshes.put({ sha256, payload }); });
        }
        projects.put(entry.record);
      } else {
        projects.delete(id);
      }
      const retained = new Set(entry?.record.meshHashes);
      for (const hash of previous?.meshHashes || []) {
        if (!retained.has(hash)) onSuccess(projects.index(REFERENCES).count(hash), count => { if (!count) meshes.delete(hash); });
      }
      finish(true);
    });
  });
}

function readFallback(optional = false) {
  try {
    const raw = localStorage.getItem(FALLBACK_KEY);
    if (!raw) return { projects: [], meshes: {} };
    const saved = JSON.parse(raw);
    if (![1, 2].includes(saved?.version) || !Array.isArray(saved.projects) || (saved.version === 2 && !saved.meshes)) {
      throw new Error('Invalid local library');
    }
    return { projects: saved.projects, meshes: saved.meshes || {} };
  } catch (error) {
    if (optional) return null;
    throw new LibraryStorageError('无法读取本地作品，请检查浏览器存储设置。', error);
  }
}

function encodeBase64(payload) {
  const bytes = new Uint8Array(payload);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return btoa(binary);
}

function fallbackPayload(saved, hash) {
  const value = saved.meshes[hash];
  return typeof value === 'string' ? Uint8Array.from(atob(value), char => char.charCodeAt(0)).buffer : undefined;
}

function writeFallback(saved, id, entry) {
  const projects = saved.projects.filter(record => record.id !== id);
  if (entry) projects.push(entry.record);
  const meshes = {};
  for (const hash of new Set(projects.flatMap(record => record.meshHashes || []))) {
    if (typeof saved.meshes[hash] === 'string') {
      meshes[hash] = saved.meshes[hash];
      continue;
    }
    const payload = entry?.assets.get(hash);
    if (!payload) throw new LibraryStorageError('本地作品缺少零件网格。');
    meshes[hash] = encodeBase64(payload);
  }
  try {
    localStorage.setItem(FALLBACK_KEY, JSON.stringify({ version: 2, projects, meshes }));
  } catch (error) {
    throw new LibraryStorageError('作品未能保存：本地存储空间不足或已被禁用。', error);
  }
}

function newerFallback(fallback, indexed) {
  return fallback && (!indexed || String(fallback.updatedAt || fallback.createdAt || '') > String(indexed.updatedAt || indexed.createdAt || ''));
}

function removeFallbackCopy(record) {
  if (!record) return;
  try {
    const saved = readFallback();
    if (sameRecord(record, saved.projects.find(item => item.id === record.id))) writeFallback(saved, record.id, null);
  } catch { /* The committed IndexedDB snapshot stays usable if fallback cleanup is blocked. */ }
}

export const projectStorage = {
  get backend() { return backend; },
  async list() {
    const db = await database();
    const fallback = readFallback(!!db);
    const indexed = db ? await readIndexed(db) : { records: [] };
    const local = new Map(indexed.records.map(record => [record.id, record]));
    const extra = (fallback?.projects || []).filter(record => newerFallback(record, local.get(record.id)));
    for (const record of extra) local.delete(record.id);
    return [...hydrateProjects([...local.values()], hash => indexed.payloads.get(hash)),
      ...hydrateProjects(extra, hash => fallbackPayload(fallback, hash))];
  },
  async read(id) {
    const db = await database();
    const indexed = db ? await readIndexed(db, id) : { records: [] };
    const local = indexed.records[0];
    const fallback = readFallback(!!db);
    const alternate = fallback?.projects.find(record => record.id === id);
    const useFallback = newerFallback(alternate, local);
    const record = useFallback ? alternate : local;
    if (!record) return null;
    const [project] = hydrateProjects([record], hash => useFallback ? fallbackPayload(fallback, hash) : indexed.payloads.get(hash));
    return { db, local, record, project, useFallback };
  },
  async migrate(snapshot, project, hasRecoveredBaseline) {
    const { db, local, record, useFallback } = snapshot;
    const needsPacking = !record.meshHashes && PART_FIELDS.some(field => project[field]?.length);
    if (!hasRecoveredBaseline && !needsPacking && !(db && useFallback)) return;
    const entry = await packProject(project);
    if (!hasRecoveredBaseline && !entry.assets.size && !(db && useFallback)) return;
    if (db) {
      const applied = await writeIndexed(db, project.id, entry, previous => sameRecord(previous, local));
      if (applied && useFallback) removeFallbackCopy(record);
    } else {
      const saved = readFallback();
      if (sameRecord(record, saved.projects.find(item => item.id === project.id))) writeFallback(saved, project.id, entry);
    }
  },
  async save(project) {
    const db = await database();
    const entry = await packProject(project);
    if (db) {
      const previous = readFallback(true)?.projects.find(record => record.id === project.id);
      await writeIndexed(db, project.id, entry);
      removeFallbackCopy(previous);
    } else {
      writeFallback(readFallback(), project.id, entry);
    }
  },
  async remove(id) {
    const db = await database();
    const fallback = readFallback(!!db);
    if (fallback?.projects.some(record => record.id === id)) writeFallback(fallback, id, null);
    if (db) await writeIndexed(db, id, null);
  },
};
