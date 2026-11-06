import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { createPartMeshGeometry } from '../dist/model.js';

const brick = JSON.parse(readFileSync(new URL('./fixtures/ldraw-3004-mesh.json', import.meta.url)));

function volume(geometry) {
  const positions = geometry.attributes.position, index = geometry.index;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let result = 0;
  for (let i = 0; i < (index?.count ?? positions.count); i += 3) {
    a.fromBufferAttribute(positions, index ? index.getX(i) : i);
    b.fromBufferAttribute(positions, index ? index.getX(i + 1) : i + 1);
    c.fromBufferAttribute(positions, index ? index.getX(i + 2) : i + 2);
    result += a.dot(b.cross(c)) / 6;
  }
  return result;
}

function partFrom(geometry) {
  return { mesh: { positions: Array.from(geometry.attributes.position.array), indices: Array.from(geometry.index.array) } };
}

function indexedPart(part) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(part.mesh.positions, 3));
  geometry.setIndex(part.mesh.indices);
  return geometry;
}

function triangles(geometry) {
  const positions = geometry.attributes.position, index = geometry.index, result = [];
  for (let i = 0; i < (index?.count ?? positions.count); i += 3) {
    const vertices = [];
    for (let j = 0; j < 3; j++) {
      const offset = index ? index.getX(i + j) : i + j;
      vertices.push([positions.getX(offset), positions.getY(offset), positions.getZ(offset)].join(','));
    }
    result.push(vertices.sort().join(';'));
  }
  return result.sort();
}

test('shared cube vertices preserve flat faces and sharp corners', () => {
  const part = { mesh: {
    positions: [-1,-1,-1, 1,-1,-1, 1,1,-1, -1,1,-1, -1,-1,1, 1,-1,1, 1,1,1, -1,1,1],
    indices: [0,2,1, 0,3,2, 4,5,6, 4,6,7, 0,4,7, 0,7,3, 1,2,6, 1,6,5, 0,1,5, 0,5,4, 3,7,6, 3,6,2],
  } };
  const geometry = createPartMeshGeometry(part);
  assert.ok(volume(geometry) > 0);
  const p = geometry.attributes.position, n = geometry.attributes.normal;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let i = 0; i < p.count; i += 3) {
    a.fromBufferAttribute(p, i); b.fromBufferAttribute(p, i + 1); c.fromBufferAttribute(p, i + 2);
    const face = c.sub(b).cross(a.sub(b)).normalize();
    for (let j = 0; j < 3; j++) {
      assert.ok(face.dot(new THREE.Vector3().fromBufferAttribute(n, i + j)) > .999999, 'a box face must not interpolate normals toward a neighboring side');
    }
  }
  geometry.dispose();
});

test('round studs have smooth side walls without smoothing across their flat caps', () => {
  const cylinder = new THREE.CylinderGeometry(1, 1, 1, 16);
  const geometry = createPartMeshGeometry(partFrom(cylinder));
  const p = geometry.attributes.position, n = geometry.attributes.normal;
  let caps = 0, sides = 0;
  for (let i = 0; i < p.count; i += 3) {
    const top = [0,1,2].every(j => Math.abs(p.getY(i + j) - .5) < 1e-6);
    const bottom = [0,1,2].every(j => Math.abs(p.getY(i + j) + .5) < 1e-6);
    if (top || bottom) {
      caps++;
      for (let j = 0; j < 3; j++) assert.ok(Math.abs(n.getY(i + j) - (top ? 1 : -1)) < 1e-6);
    } else {
      sides++;
      for (let j = 0; j < 3; j++) {
        const radial = new THREE.Vector3(p.getX(i + j), 0, p.getZ(i + j)).normalize();
        assert.ok(radial.dot(new THREE.Vector3().fromBufferAttribute(n, i + j)) > .99);
        assert.ok(Math.abs(n.getY(i + j)) < 1e-6);
      }
    }
  }
  assert.ok(caps > 0 && sides > 0);
  cylinder.dispose(); geometry.dispose();
});

test('an inward LDraw brick is outward-facing without changing any source triangle or saved mesh', () => {
  const original = JSON.stringify(brick);
  const source = indexedPart(brick), geometry = createPartMeshGeometry(brick);
  assert.ok(volume(source) < 0, 'fixture retains the original inward shell');
  assert.ok(volume(geometry) > 0, 'rendered faces must point out of the shell');
  assert.deepEqual(triangles(geometry), triangles(source), 'winding and normals may change; cavities, connectors and every vertex remain intact');
  assert.equal(JSON.stringify(brick), original);
  source.computeBoundingBox();
  assert.ok(source.boundingBox.equals(geometry.boundingBox));
  assert.equal(geometry.attributes.position.count, brick.mesh.indices.length);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ side: THREE.FrontSide }));
  const ray = new THREE.Raycaster(new THREE.Vector3(0, 2, 0), new THREE.Vector3(0, -1, 0));
  const hit = ray.intersectObject(mesh)[0];
  assert.ok(hit && hit.face.normal.y > .99, 'a ray from outside must hit an outward top face, rather than the inner underside');
  mesh.material.dispose(); source.dispose(); geometry.dispose();
});

test('real brick plate and stud cap faces stay planar under lighting', () => {
  const geometry = createPartMeshGeometry(brick), p = geometry.attributes.position, n = geometry.attributes.normal;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  let checked = 0;
  for (let i = 0; i < p.count; i += 3) {
    a.fromBufferAttribute(p, i); b.fromBufferAttribute(p, i + 1); c.fromBufferAttribute(p, i + 2);
    const face = c.sub(b).cross(a.sub(b)).normalize();
    if (Math.abs(face.y) < .999999) continue;
    checked++;
    for (let j = 0; j < 3; j++) assert.ok(face.dot(new THREE.Vector3().fromBufferAttribute(n, i + j)) > .999999);
  }
  assert.ok(checked >= 100, 'covers real stud caps, base plate surfaces, and underside planes');
  geometry.dispose();
});
