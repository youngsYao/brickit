import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createWorksLibrary } from '../dist/works.js';

function memoryStorage(initial = []) {
  const records = new Map(initial.map(project => [project.id, structuredClone(project)]));
  return {
    backend: 'memory',
    async list() { return structuredClone([...records.values()]); },
    async get(id) { return structuredClone(records.get(id) || null); },
    async put(project) { records.set(project.id, structuredClone(project)); return structuredClone(project); },
    async remove(id) { records.delete(id); },
  };
}

test('both approved AI works are listed without fetching meshes or replacing existing work', async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Listing works must not load models'); };
  try {
    const existing = { id: 'my-model', title: '已有修改稿', source: 'user', draft: true, pieces: [] };
    const works = createWorksLibrary(memoryStorage([existing]));
    const projects = await works.list();
    assert.equal(projects.length, 3);
    assert.deepEqual(projects.find(p => p.id === existing.id), existing);
    assert.deepEqual(projects.filter(p => p.source === 'ai').map(p => p.pieceCount).sort((a, b) => a - b), [246, 589]);
    assert.ok(projects.filter(p => p.source === 'ai').every(p => p.draft === false && Number.isFinite(Date.parse(p.createdAt))));
    projects.find(p => p.source === 'ai').title = 'changed returned metadata';
    assert.ok(!(await works.list()).some(p => p.title === 'changed returned metadata'));
  } finally { globalThis.fetch = previousFetch; }
});

test('opening saved AI works preserves genuine geometry, validation and offline exports', async () => {
  const previousFetch = globalThis.fetch, requests = [];
  globalThis.fetch = async url => {
    requests.push(url);
    const data = JSON.parse(await readFile(new URL('../dist/' + url.replace(/^\.\//, ''), import.meta.url), 'utf8'));
    return { ok: true, json: async () => data };
  };
  try {
    const works = createWorksLibrary(memoryStorage());
    for (const summary of await works.list()) {
      const saved = await works.get(summary.id);
      const bundled = JSON.parse(await readFile(new URL('../dist/' + summary.projectUrl.replace(/^\.\//, ''), import.meta.url), 'utf8'));
      assert.equal(saved.source, 'ai');
      assert.equal(saved.draft, false);
      assert.equal(saved.createdAt, summary.createdAt);
      assert.equal(saved.pieces.length, summary.pieceCount);
      assert.deepEqual(saved.pieces, bundled.pieces);
      assert.deepEqual(saved.parts, bundled.parts);
      assert.deepEqual(saved.originalPieces, bundled.pieces);
      assert.deepEqual(saved.generation, bundled.generation);
      saved.favorite = true;
      await works.put(saved);
      assert.equal((await works.list()).filter(p => p.id === saved.id).length, 1);
      assert.equal((await works.get(saved.id)).favorite, true);
    }
    assert.equal(requests.length, 2);
  } finally { globalThis.fetch = previousFetch; }
});

test('existing saved versions take precedence and removed bundled work stays removed after reload', async () => {
  const saved = { id: 'songen-generated-example', title: '我的颂恩楼版本', source: 'example', favorite: true, pieces: [{ id: 'preserved-piece' }] };
  const draft = { id: 'my-draft', parentId: saved.id, source: 'user', draft: true, pieces: [{ id: 'draft-piece' }] };
  const storage = memoryStorage([saved, draft]);
  const works = createWorksLibrary(storage);
  assert.equal((await works.list()).length, 3);
  assert.deepEqual((await works.get(saved.id)).pieces, saved.pieces);
  assert.equal((await works.get(saved.id)).title, saved.title);
  assert.equal((await works.get(saved.id)).favorite, true);
  await works.remove(saved.id);
  const reloaded = createWorksLibrary(storage);
  assert.equal(await reloaded.get(saved.id), null);
  assert.equal((await reloaded.list()).length, 2);
  assert.deepEqual(await reloaded.get(draft.id), draft);
  await reloaded.put(saved);
  assert.equal((await reloaded.list()).length, 3);
  assert.deepEqual((await reloaded.get(saved.id)).pieces, saved.pieces);
  await reloaded.remove(draft.id);
  assert.equal(await reloaded.get(draft.id), null);
});
