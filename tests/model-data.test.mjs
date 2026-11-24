import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb';
import {
  CATALOG, UNIT, canPlace, cloneProject, createExampleProject,
  createPiece, findPart, getCatalog, inventoryFor, orderPieces, registerProjectParts, resolveOriginalPieces,
} from '../dist/data.js';

const meshPart = (id = 'ldraw-3001') => ({
  id, ldrawId: id.replace('ldraw-', ''), name: 'Brick 2 x 4',
  category: 'brick', w: 2, d: 4, h: .56, shape: 'mesh', studs: false,
  colors: ['#b91620'],
  mesh: {
    positions: [-.58,-.28,-1.16, .58,-.28,-1.16, 0,-.28,1.16, 0,.28,0],
    indices: [0,2,1, 0,1,3, 1,2,3, 2,0,3],
  },
  metadata: { source: 'bricklayer', meshType: 'official-inset' },
});
const officialProject = (id = 'generated-model') => {
  const part = meshPart();
  const piece = {
    id: 'official-piece-1', partId: part.id, ldrawId: '3001', colorCode: 4,
    w: 2, d: 4, h: .56, shape: 'mesh', color: '#b91620',
    x: 1.25, y: 2, z: -.75, rx: .31, ry: -.47, rz: .29, layer: 3,
    sourcePlacement: { ldrawPosition: [20, -24, 40], rotation: [1,0,0, 0,1,0, 0,0,1] },
  };
  return {
    id, title: '官方网格作品', source: 'brickwright', parts: [part],
    pieces: [piece], originalPieces: [cloneProject(piece)],
    metadata: { generator: 'brickwright', commit: 'reviewed-version' },
    validation: { status: 'passed', checks: { collisions: 0, floating: 0 } },
  };
};

test('official catalog registration preserves exact vertices and uses official identities', () => {
  const project = officialProject();
  const original = cloneProject(project);
  const registered = registerProjectParts(project);
  assert.deepEqual(project, original, 'registration must not rewrite the saved source');
  assert.deepEqual(findPart('ldraw-3001').mesh, project.parts[0].mesh);
  assert.equal(findPart('ldraw-3001').ldrawId, '3001');
  assert.ok(Object.isFrozen(registered[0].mesh.positions));
  assert.ok(getCatalog().some(part => part.id === 'ldraw-3001'));
  assert.equal(CATALOG.some(part => part.id === 'ldraw-3001'), false, 'sample catalog stays separate');
  const created = createPiece('ldraw-3001', '#b91620', { x: 1, y: 2, z: 3, rx: .4, ry: -.5, rz: .6, metadata: { edit: 'added' } });
  assert.equal(created.ldrawId, '3001');
  assert.equal(created.shape, 'mesh');
  assert.deepEqual([created.rx,created.ry,created.rz], [.4,-.5,.6]);
  assert.deepEqual(created.metadata, { edit: 'added' });
});

test('malformed meshes and missing referenced meshes are rejected atomically', () => {
  const invalids = [
    part => { part.mesh.positions[0] = Infinity; },
    part => { part.mesh.positions = [0, 0, 0]; },
    part => { part.mesh.indices = [0, 1, 500]; },
    part => { part.mesh.indices = [0, 1, 1.5]; },
    part => { part.mesh.indices = [0, 1]; },
    part => { part.w = -2; },
    part => { part.h = .1; },
    part => { part.mesh.positions = part.mesh.positions.map((value,index) => index % 3 === 0 ? value + 1 : value); },
  ];
  for (const corrupt of invalids) {
    const part = meshPart('ldraw-malformed'); corrupt(part);
    assert.throws(() => registerProjectParts({ parts: [meshPart('ldraw-atomic-candidate'), part] }), TypeError);
    assert.equal(findPart('ldraw-atomic-candidate'), undefined);
    assert.equal(findPart('ldraw-malformed'), undefined);
  }
  assert.throws(() => registerProjectParts({ ...officialProject(), parts: [] }), /网格/);
  assert.throws(() => registerProjectParts({ ...officialProject(), parts: [meshPart(), meshPart()] }), /重复/);
  assert.throws(() => registerProjectParts({ ...officialProject(), pieces: [{ ...officialProject().pieces[0], rx: NaN }] }), /旋转/);
});

test('inventory keeps official IDs, names and LDraw color codes separate from sample parts', () => {
  const project = officialProject(); registerProjectParts(project);
  const second = { ...project.pieces[0], id: 'official-2', rx: 1, ry: 2, rz: 3 };
  const rows = inventoryFor([...project.pieces, second, createPiece('brick-2x4', '#b91620')]);
  const official = rows.find(row => row.partId === 'ldraw-3001');
  assert.equal(official.name, 'Brick 2 x 4');
  assert.equal(official.ldrawId, '3001');
  assert.equal(official.colorCode, 4);
  assert.equal(official.count, 2);
  assert.equal(rows.find(row => row.partId === 'brick-2x4').count, 1);
  assert.equal(official.sampleHeight, false);
  const unregistered = inventoryFor([{ ...project.pieces[0], partId: 'ldraw-unknown', ldrawId: 'unknown' }]);
  assert.equal(unregistered[0].partId, 'ldraw-unknown');
  assert.equal(unregistered[0].ldrawId, 'unknown');
});

test('bottom-up ordering never changes full poses or original placement metadata', () => {
  const project = officialProject(); registerProjectParts(project);
  const input = [project.pieces[0], { ...cloneProject(project.pieces[0]), id: 'lower', y: .5 }];
  const before = cloneProject(input), ordered = orderPieces(input);
  assert.deepEqual(input, before);
  assert.equal(ordered[0].id, 'lower');
  assert.deepEqual(ordered.find(piece => piece.id === 'official-piece-1'), project.pieces[0]);
  assert.notEqual(ordered[1], input[0]);
});

test('editing overlap and ground checks account for X rotation', () => {
  const long = { id: 'long', x: 0, y: 1.2, z: 0, w: 1, d: 4, h: .2, rx: 0, ry: 0, rz: 0 };
  const above = { id: 'above', x: 0, y: 1.7, z: 0, w: 1, d: 1, h: .56 };
  assert.equal(canPlace([above], long).ok, true);
  assert.equal(canPlace([above], { ...long, rx: Math.PI / 2 }).ok, false, 'rotation brings the long part into the part above');
  assert.equal(canPlace([], { ...long, y: .3, rx: Math.PI / 2 }).ok, false, 'rotation crosses the ground');
  assert.equal(canPlace([], { ...long, rx: Infinity }).ok, false);
  assert.equal(UNIT, .58);
});

test('retired apple saved works retain their restoration data and playback order', async () => {
  const pieces = await resolveOriginalPieces({ id:'saved-apple', rootId:'apple-example', source:'user', draft:true, pieces:[] });
  assert.equal(pieces.length, 115);
  assert.equal(createHash('sha256').update(JSON.stringify(pieces)).digest('hex'), 'de4ea63f5c36a3e0c8261c49058849ab47b992cd637dca1d4a61518db2208363');
  const legacyOrder = pieces.map((piece,index) => ({ piece,index,bottom: piece.y - piece.h / 2 }))
    .sort((a,b) => a.bottom-b.bottom || a.piece.y-b.piece.y || a.piece.z-b.piece.z || a.piece.x-b.piece.x || a.index-b.index)
    .map(({ piece }) => piece);
  assert.deepEqual(orderPieces(pieces), legacyOrder);
  const ownBaseline = [{ ...pieces[0], color:'#ffffff' }];
  assert.deepEqual(await resolveOriginalPieces({ id:'saved-apple', rootId:'apple-example', originalPieces:ownBaseline, pieces:[] }), ownBaseline);
  assert.doesNotThrow(() => registerProjectParts({ ...createExampleProject(), originalPieces: null }));
});

function resultOf(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

test('IndexedDB upgrades inline projects and reuses meshes while preserving poses, baselines and inventory', async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'indexedDB');
  Object.defineProperty(globalThis, 'indexedDB', { configurable: true, value: new IDBFactory() });
  let db;
  try {
    const project = officialProject();
    project.parts[0].mesh.positions[6] = -0;
    project.parts[0].mesh.detail = { label: 'legacy' };
    const draft = officialProject('old-draft');
    draft.parentId = project.id;
    draft.rootId = project.id;
    draft.draft = true;
    delete draft.originalPieces;
    draft.parts[0].name = 'Edited part label';
    draft.parts[0].mesh.positions[6] = .2;
    draft.pieces[0].color = '#ffff00';
    draft.pieces[0].colorCode = 14;
    const open = indexedDB.open('brick-it-library', 1);
    open.onupgradeneeded = () => {
      const projects = open.result.createObjectStore('projects', { keyPath: 'id' });
      projects.put(project);
      projects.put(draft);
    };
    (await resultOf(open)).close();
    const data = await import('../dist/data.js?indexeddb-round-trip');
    await data.library.list();
    db = await resultOf(indexedDB.open('brick-it-library'));
    const read = (store, operation) => resultOf(operation(db.transaction(store).objectStore(store)));
    const rawProject = id => read('projects', store => store.get(id));
    const assets = () => read('meshAssets', store => store.getAll());
    assert.equal(db.version, 2);
    assert.deepEqual((await rawProject(project.id)).parts, project.parts, 'listing leaves v1 records inline');

    const oldPut = IDBObjectStore.prototype.put;
    try {
      IDBObjectStore.prototype.put = function (...args) {
        if (this.name === 'meshAssets') throw new DOMException('Test quota error', 'QuotaExceededError');
        return oldPut.apply(this, args);
      };
      assert.deepEqual((await data.library.get(project.id)).parts, project.parts);
      assert.deepEqual((await rawProject(project.id)).parts, project.parts, 'failed migration leaves the original record intact');
      assert.equal((await assets()).length, 0);
    } finally {
      IDBObjectStore.prototype.put = oldPut;
    }

    const migrated = await data.library.get(draft.id);
    assert.deepEqual(migrated.originalPieces, project.originalPieces);
    assert.deepEqual(migrated.originalParts, project.parts);
    assert.deepEqual(migrated.parts, draft.parts);
    assert.deepEqual(data.inventoryFor(migrated.pieces, migrated.parts), data.inventoryFor(draft.pieces, draft.parts));
    const rawDraft = await rawProject(draft.id);
    assert.match(rawDraft.parts[0].meshSha256, /^[0-9a-f]{64}$/);
    assert.equal(rawDraft.parts[0].mesh, undefined);
    assert.notEqual(rawDraft.parts[0].meshSha256, rawDraft.originalParts[0].meshSha256);

    const stored = await data.library.put(project);
    assert.equal(data.library.backend, 'indexeddb');
    assert.equal(stored.source, 'brickwright');
    assert.equal((await rawProject(project.id)).parts[0].meshSha256, rawDraft.originalParts[0].meshSha256);
    assert.equal((await assets()).length, 2);
    for (const asset of await assets()) assert.equal(createHash('sha256').update(new Uint8Array(asset.payload)).digest('hex'), asset.sha256);
    stored.pieces[0].sourcePlacement.rotation[0] = 99;
    stored.parts[0].mesh.positions[0] = 99;
    const loaded = await data.library.get(project.id);
    assert.deepEqual(loaded.parts, project.parts);
    assert.deepEqual(loaded.pieces, project.pieces);
    assert.deepEqual(loaded.originalPieces, project.originalPieces);
    assert.deepEqual(loaded.metadata, project.metadata);
    assert.deepEqual(loaded.validation, project.validation);
    assert.deepEqual(data.findPart('ldraw-3001').mesh, project.parts[0].mesh);
    await data.library.remove(project.id);
    assert.equal(await data.library.get(project.id), null);
    const afterDelete = await data.library.get(draft.id);
    assert.deepEqual(await data.resolveOriginalPieces(afterDelete), project.originalPieces);
    assert.deepEqual(afterDelete.originalParts, project.parts);
    assert.equal((await assets()).length, 2);
    await data.library.remove(draft.id);
    assert.equal((await assets()).length, 0);

    for (const folder of ['generated-gate', 'generated-songen']) {
      const example = JSON.parse(readFileSync(new URL(`../dist/examples/${folder}/project.json`, import.meta.url)));
      await data.library.put(example);
      const saved = await data.library.get(example.id);
      assert.deepEqual(saved.pieces, example.pieces);
      assert.deepEqual(saved.parts, example.parts);
    }
    assert.equal((await assets()).length, 71, 'both approved works share exactly 71 mesh payloads');
  } finally {
    db?.close();
    if (previous) Object.defineProperty(globalThis, 'indexedDB', previous);
    else delete globalThis.indexedDB;
  }
});

test('localStorage fallback migrates an inline record and shares its binary mesh across versions', async () => {
  const key = 'brick.it.library.v1';
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const legacy = officialProject('fallback-source');
  const version = officialProject('fallback-version');
  version.parts[0].name = 'Version label';
  const records = new Map([[key, JSON.stringify({ version: 1, projects: [legacy] })]]);
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: id => records.get(id) ?? null,
    setItem: (id, value) => records.set(id, String(value)),
  } });
  try {
    const { library } = await import('../dist/data.js');
    assert.deepEqual((await library.get(legacy.id)).parts, legacy.parts);
    await library.put(version);
    assert.equal(library.backend, 'localstorage');
    const stored = JSON.parse(records.get(key));
    assert.equal(stored.version, 2);
    assert.equal(Object.keys(stored.meshes).length, 1);
    assert.equal(stored.projects[0].parts[0].meshSha256, stored.projects[1].parts[0].meshSha256);
    await library.remove(legacy.id);
    assert.deepEqual((await library.get(version.id)).parts, version.parts);
  } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else delete globalThis.localStorage;
  }
});
