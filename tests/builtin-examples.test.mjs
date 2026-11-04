import test from 'node:test';
import assert from 'node:assert/strict';
import { BUILTIN_EXAMPLES, createDefaultPieces, createExampleProject, resolveOriginalPieces, registerProjectParts, inventoryFor, orderPieces } from '../dist/data.js';

test('both bundled examples open without a generation service and have independent baselines', () => {
  assert.deepEqual(BUILTIN_EXAMPLES.map(example => example.id), ['apple-example', 'songen-example']);
  const apple=createExampleProject(), building=createExampleProject('songen-example');
  assert.equal(apple.id, 'apple-example');
  assert.deepEqual(apple.pieces, createDefaultPieces());
  assert.equal(building.id, 'songen-example');
  assert.equal(building.title, '厦大 · 颂恩楼');
  assert.equal(building.source, 'example');
  assert.equal(building.sourceImage, './assets/songen-reference.jpg');
  assert.equal(building.thumbnail, './assets/songen-preview.png');
  assert.ok(building.pieces.length > apple.pieces.length);
  assert.equal(new Set(building.pieces.map(piece=>piece.id)).size, building.pieces.length);
  assert.doesNotThrow(()=>registerProjectParts(building));
  assert.equal(inventoryFor(building.pieces).reduce((sum,row)=>sum+row.count,0), building.pieces.length);
  const original=structuredClone(building.originalPieces);
  building.pieces[0].color='#ffffff';
  assert.deepEqual(building.originalPieces,original);
  assert.deepEqual(createExampleProject('songen-example').pieces,original);
  assert.throws(()=>createExampleProject('missing-example'), /内置示例/);
});

test('restoring a saved building draft resolves the building rather than the legacy apple', async () => {
  const expected=createExampleProject('songen-example').pieces;
  const fromId=await resolveOriginalPieces({id:'songen-example', source:'example', pieces:[]});
  assert.deepEqual(fromId,expected);
  const fromRoot=await resolveOriginalPieces({id:'edited-building',rootId:'songen-example',source:'user',draft:true,pieces:[]});
  assert.deepEqual(fromRoot,expected);
  const fromExampleId=await resolveOriginalPieces({id:'saved-building',exampleId:'songen-example',source:'user',draft:true,pieces:[]});
  assert.deepEqual(fromExampleId,expected);
  assert.deepEqual(await resolveOriginalPieces({id:'apple-example',source:'example',pieces:[]}),createDefaultPieces());
  assert.equal(await resolveOriginalPieces({id:'unknown-example',source:'example',draft:true,pieces:[]}),null);
});

test('bottom-up building playback preserves every piece and the source geometry', () => {
  const project=createExampleProject('songen-example'), before=structuredClone(project.pieces), ordered=orderPieces(project.pieces);
  assert.deepEqual(project.pieces,before);
  assert.equal(ordered.length,before.length);
  assert.deepEqual(new Set(ordered.map(piece=>piece.id)),new Set(before.map(piece=>piece.id)));
  const byId=new Map(before.map(piece=>[piece.id,piece]));
  for(const piece of ordered)assert.deepEqual(piece,byId.get(piece.id));
});
