import * as THREE from 'three';
import { RoundedBoxGeometry } from './assets/vendor/RoundedBoxGeometry.js';
import { mergeGeometries } from './assets/vendor/BufferGeometryUtils.js';
import { RoomEnvironment } from './assets/vendor/RoomEnvironment.js';
import { UNIT, findPart, canPlace } from './data.js';

export const PALETTES = {
  red: { name: '经典红', color: '#b91620', svg: '#c9362f' },
  green: { name: '青苹果', color: '#8d9e42', svg: '#8f9f51' },
  yellow: { name: '阳光黄', color: '#e6ad2d', svg: '#e8b94d' }
};
const U = UNIT || .58;
const MODEL_LIFT = .055;
const GROUND_COLOR = '#f7f6f2';
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const clamp = THREE.MathUtils.clamp;
const sameId = (a, b) => a !== null && b !== null && String(a) === String(b);

/** A scene adapter. Project mutations always remain the caller's responsibility. */
export async function createModel(container, options = {}) {
  const { onSelect, onPlace, onReady, onError } = options;
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  } catch (error) {
    onError?.(error);
    throw error;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .99;
  renderer.setClearColor(GROUND_COLOR, 0);
  renderer.domElement.setAttribute('aria-hidden', 'true');
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const modelGroup = new THREE.Group();
  modelGroup.position.y = MODEL_LIFT;
  scene.add(modelGroup);
  const camera = new THREE.OrthographicCamera(-6, 6, 4.4, -4.4, .1, 150);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const environment = pmrem.fromScene(room, .04);
  scene.environment = environment.texture;
  scene.environmentIntensity = .35;
  room.dispose(); pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xfffcf1, 0xb0b49c, 1.8));
  const key = new THREE.DirectionalLight(0xfff5e7, 3.1);
  key.position.set(-7, 13, 8);
  key.target.position.set(0, 2, 0);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -12, right: 12, top: 13, bottom: -9, near: .5, far: 55 });
  key.shadow.normalBias = .024; key.shadow.bias = -.0001; key.shadow.radius = 5;
  scene.add(key, key.target);
  const fill = new THREE.DirectionalLight(0xf2f5e4, .9); fill.position.set(7, 5, -5); scene.add(fill);
  const rim = new THREE.DirectionalLight(0xffffff, 1.2); rim.position.set(1, 10, -9); scene.add(rim);

  const staticResources = [];
  const keep = resource => (staticResources.push(resource), resource);
  const baseGeometry = keep(new THREE.CylinderGeometry(3.9, 3.95, .095, 96));
  const baseMaterial = keep(new THREE.MeshStandardMaterial({ color: '#e6e8da', roughness: .8 }));
  const base = new THREE.Mesh(baseGeometry, baseMaterial); base.position.y = -.08; base.receiveShadow = true; scene.add(base);
  const dotGeometry = keep(new THREE.CylinderGeometry(.143, .15, .043, 16));
  const dotMaterial = keep(new THREE.MeshStandardMaterial({ color: '#dfe3d1', roughness: .75 }));
  const baseDots = [];
  for (let x = -6; x <= 6; x++) for (let z = -6; z <= 6; z++) if (x * x + z * z < 42) baseDots.push([x * U, z * U]);
  const studs = new THREE.InstancedMesh(dotGeometry, dotMaterial, baseDots.length);
  const matrix = new THREE.Matrix4();
  baseDots.forEach(([x, z], i) => { matrix.makeTranslation(x, -.015, z); studs.setMatrixAt(i, matrix); });
  studs.receiveShadow = true; scene.add(studs);
  const floorGeometry = keep(new THREE.PlaneGeometry(100, 100));
  const floorMaterial = keep(new THREE.ShadowMaterial({ opacity: .04 }));
  const floor = new THREE.Mesh(floorGeometry, floorMaterial); floor.rotation.x = -Math.PI / 2; floor.position.y = -.145; floor.receiveShadow = true; scene.add(floor);

  const shadowCanvas = document.createElement('canvas'); shadowCanvas.width = shadowCanvas.height = 128;
  const shadowContext = shadowCanvas.getContext('2d');
  const gradient = shadowContext.createRadialGradient(64, 64, 4, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(79,83,51,.20)'); gradient.addColorStop(.48, 'rgba(79,83,51,.10)'); gradient.addColorStop(1, 'rgba(79,83,51,0)');
  shadowContext.fillStyle = gradient; shadowContext.fillRect(0, 0, 128, 128);
  const shadowTexture = keep(new THREE.CanvasTexture(shadowCanvas));
  for (const [w, d, y, opacity] of [[9, 8, -.137, 1], [4, 3, .027, .6]]) {
    const geometry = keep(new THREE.PlaneGeometry(w, d));
    const material = keep(new THREE.MeshBasicMaterial({ map: shadowTexture, transparent: true, opacity, depthWrite: false }));
    const plane = new THREE.Mesh(geometry, material); plane.rotation.x = -Math.PI / 2; plane.position.y = y; scene.add(plane);
  }
  const gridCanvas = document.createElement('canvas'); gridCanvas.width = gridCanvas.height = 256;
  const gridContext = gridCanvas.getContext('2d'); gridContext.fillStyle = '#a8b08d';
  for (let x = 8; x < 256; x += 24) for (let z = 8; z < 256; z += 24) { gridContext.beginPath(); gridContext.arc(x, z, .8, 0, Math.PI * 2); gridContext.fill(); }
  const gridTexture = keep(new THREE.CanvasTexture(gridCanvas));
  const grid = new THREE.Mesh(keep(new THREE.PlaneGeometry(18, 15)), keep(new THREE.MeshBasicMaterial({ map: gridTexture, transparent: true, opacity: .3, depthWrite: false })));
  grid.rotation.x = -Math.PI / 2; grid.position.y = -.14; scene.add(grid);

  const geometryCache = new Map(), materialCache = new Map();
  const studPoints = [new THREE.Vector2(0, -.026), new THREE.Vector2(.162, -.026), new THREE.Vector2(.174, -.018), new THREE.Vector2(.174, .043), new THREE.Vector2(.166, .054), new THREE.Vector2(0, .054)];
  const studGeometry = new THREE.LatheGeometry(studPoints, 20);
  const materialFor = color => {
    const safe = /^#[0-9a-f]{6}$/i.test(color || '') ? color : PALETTES.red.color;
    if (!materialCache.has(safe)) materialCache.set(safe, new THREE.MeshPhysicalMaterial({ color: safe, roughness: .28, metalness: 0, clearcoat: .42, clearcoatRoughness: .2 }));
    return materialCache.get(safe);
  };
  const asNonIndexed = geometry => { if (!geometry.index) return geometry; const expanded = geometry.toNonIndexed(); geometry.dispose(); return expanded; };
  function geometryFor(spec) {
    const part = findPart(spec.partId || spec.id);
    const shape = spec.shape || part?.shape || (spec.h < .3 ? 'plate' : 'brick');
    const w = Math.max(1, finite(spec.w, part?.w || 1)), d = Math.max(1, finite(spec.d, part?.d || 1));
    const h = Math.max(.08, finite(spec.h, part?.h || .56));
    const hasStuds = spec.studs ?? part?.studs ?? !['tile', 'beam', 'connector'].includes(shape);
    const cacheKey = [shape, w, d, h, hasStuds].join(':');
    if (geometryCache.has(cacheKey)) return { geometry: geometryCache.get(cacheKey), key: cacheKey };
    const width = w * U - .022, depth = d * U - .022, height = h - .015;
    let body;
    if (shape === 'round') {
      body = new THREE.CylinderGeometry(Math.min(width, depth) / 2, Math.min(width, depth) / 2, height, 32);
      body.scale(width / Math.min(width, depth), 1, depth / Math.min(width, depth));
    } else if (shape === 'slope') {
      const profile = new THREE.Shape();
      profile.moveTo(-width / 2, -height / 2); profile.lineTo(width / 2, -height / 2);
      profile.lineTo(width / 2, height / 2); profile.lineTo(-width / 2, -height / 2 + Math.min(height * .2, .1)); profile.closePath();
      body = new THREE.ExtrudeGeometry(profile, { depth: depth - .016, bevelEnabled: true, bevelSegments: 1, steps: 1, bevelSize: .008, bevelThickness: .008 });
      body.translate(0, 0, -(depth - .016) / 2);
    } else if (shape === 'beam') {
      const length = Math.max(width, depth), thickness = Math.min(width, depth), count = Math.max(w, d);
      const profile = new THREE.Shape();
      profile.moveTo(-length / 2, -height / 2); profile.lineTo(length / 2, -height / 2); profile.lineTo(length / 2, height / 2); profile.lineTo(-length / 2, height / 2); profile.closePath();
      for (let i = 0; i < count; i++) { const hole = new THREE.Path(); hole.absellipse((i - (count - 1) / 2) * U, 0, Math.min(.17, height * .32), Math.min(.17, height * .32), 0, Math.PI * 2, true); profile.holes.push(hole); }
      body = new THREE.ExtrudeGeometry(profile, { depth: thickness, bevelEnabled: true, bevelSize: .012, bevelThickness: .008, bevelSegments: 1, steps: 1, curveSegments: 16 });
      body.translate(0, 0, -thickness / 2); if (d > w) body.rotateY(Math.PI / 2);
    } else if (shape === 'connector') {
      body = new THREE.CylinderGeometry(height * .46, height * .46, Math.max(width, depth), 24);
      if (w >= d) body.rotateZ(Math.PI / 2); else body.rotateX(Math.PI / 2);
    } else {
      body = new RoundedBoxGeometry(width, height, depth, 2, Math.min(.022, height * .2));
    }
    const geometries = [asNonIndexed(body)];
    if (hasStuds && !['beam', 'connector'].includes(shape)) {
      for (let x = 0; x < w; x++) for (let z = 0; z < d; z++) {
        if (shape === 'slope' && x !== w - 1) continue;
        if (shape === 'round' && w > 1 && d > 1) { const dx = x - (w - 1) / 2, dz = z - (d - 1) / 2; if (dx * dx / (w * w / 4) + dz * dz / (d * d / 4) > .92) continue; }
        const stud = studGeometry.clone(); stud.translate((x - (w - 1) / 2) * U, h / 2 + .021, (z - (d - 1) / 2) * U);
        geometries.push(asNonIndexed(stud));
      }
    }
    const geometry = mergeGeometries(geometries, false);
    geometries.forEach(g => g.dispose());
    geometry.computeBoundingBox(); geometry.computeBoundingSphere(); geometryCache.set(cacheKey, geometry);
    return { geometry, key: cacheKey };
  }

  let records = [], pieces = [], progress = 0, selectedId = null, mode = 'build';
  let autoRotate = false, exploded = false, placement = null, validatePlacement = options.validatePlacement;
  let theta = -.53, targetTheta = -.53, phi = .40, targetPhi = .40, zoom = 1, targetZoom = 1;
  let cameraCenter = new THREE.Vector3(0, 2.7, 0), modelHeight = 6.3, modelWidth = 7, modelRadius = 4;
  let viewportWidth = 1, viewportHeight = 1, visible = false;
  let disposed = false, contextLost = false, needsFrame = true, raf = 0, lastTime = performance.now();
  const pointer = new THREE.Vector2(), raycaster = new THREE.Raycaster();
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -MODEL_LIFT);
  const planePoint = new THREE.Vector3();
  let ghost = null, ghostEdges = null, ghostProposal = null, ghostValid = false;
  const ghostMaterial = new THREE.MeshStandardMaterial({ color: '#d47b4e', transparent: true, opacity: .42, roughness: .45, depthWrite: false });
  const ghostLineMaterial = new THREE.LineBasicMaterial({ color: '#90a45f', transparent: true, opacity: .85, depthTest: false });
  const selectionBox = new THREE.BoxHelper(new THREE.Object3D(), 0xd67146);
  selectionBox.material.depthTest = false; selectionBox.material.transparent = true; selectionBox.material.opacity = .8; selectionBox.renderOrder = 20; selectionBox.visible = false; scene.add(selectionBox);
  function updateSelection() {
    const target = mode === 'edit' ? records.find(r => sameId(r.spec.id, selectedId)) : records[progress - 1];
    if (!target?.mesh.visible || progress === records.length && mode === 'build') { selectionBox.visible = false; return; }
    target.mesh.updateMatrixWorld(true); selectionBox.setFromObject(target.mesh); selectionBox.visible = true;
    selectionBox.material.color.set(mode === 'edit' ? 0xd67146 : 0xe4ad56);
  }
  function pruneGeometry() {
    const inUse = new Set(records.map(r => r.geometryKey));
    if (ghost?.userData.geometryKey) inUse.add(ghost.userData.geometryKey);
    for (const [key, geometry] of geometryCache) if (!inUse.has(key)) { geometry.dispose(); geometryCache.delete(key); }
    const materialsInUse = new Set(records.map(rec => rec.mesh.material));
    for (const [key, material] of materialCache) if (!materialsInUse.has(material)) { material.dispose(); materialCache.delete(key); }
  }
  function updateBounds() {
    if (!records.length) { cameraCenter.set(0, 2.7, 0); modelHeight = 6.3; modelWidth = 7; modelRadius = 4; return; }
    const bounds = new THREE.Box3();
    for (const rec of records) {
      const local = rec.mesh.geometry.boundingBox.clone();
      const matrix = new THREE.Matrix4().compose(rec.base, rec.mesh.quaternion, rec.mesh.scale);
      bounds.union(local.applyMatrix4(matrix));
    }
    const size = bounds.getSize(new THREE.Vector3()), center = bounds.getCenter(new THREE.Vector3());
    modelHeight = Math.max(3, size.y); modelWidth = Math.max(5.8, Math.hypot(size.x, size.z) * .88); modelRadius = Math.max(3.9, Math.max(size.x, size.z) / 2 + 1);
    cameraCenter.set(center.x, Math.max(1.6, bounds.min.y + size.y * .44), center.z);
  }
  function updateCamera(aspect = viewportWidth / viewportHeight) {
    const framing = Math.max(8.3, modelHeight * 1.34, modelWidth / Math.max(.45, aspect) * 1.08);
    camera.left = -framing * aspect / 2; camera.right = framing * aspect / 2; camera.top = framing / 2; camera.bottom = -framing / 2;
    const target = cameraCenter.clone(); if (exploded) target.y += 1.1;
    const distance = Math.max(20, modelRadius * 3);
    camera.position.set(target.x + Math.sin(theta) * Math.cos(phi) * distance, target.y + Math.sin(phi) * distance, target.z + Math.cos(theta) * Math.cos(phi) * distance);
    camera.lookAt(target); camera.zoom = zoom; camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
  }
  const resize = () => {
    const rect = container.getBoundingClientRect(); visible = rect.width > 0 && rect.height > 0;
    if (!visible || disposed) return;
    viewportWidth = rect.width; viewportHeight = rect.height;
    renderer.setSize(viewportWidth, viewportHeight); updateCamera(); needsFrame = true;
  };
  const ro = new ResizeObserver(resize); ro.observe(container); resize();
  function clearGhost() {
    if (ghost) scene.remove(ghost); if (ghostEdges) { scene.remove(ghostEdges); ghostEdges.geometry.dispose(); }
    ghost = ghostEdges = null; ghostProposal = null; ghostValid = false;
  }
  function createGhost() {
    clearGhost();
    if (!placement || mode !== 'edit') return;
    const { geometry, key } = geometryFor(placement.part);
    ghost = new THREE.Mesh(geometry, ghostMaterial); ghost.userData.geometryKey = key; ghost.renderOrder = 10; ghost.visible = false; scene.add(ghost);
    ghostEdges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 40), ghostLineMaterial); ghostEdges.renderOrder = 11; ghostEdges.visible = false; scene.add(ghostEdges);
  }
  function visibleMeshes() { return records.filter(r => r.mesh.visible).map(r => r.mesh); }
  function updateRay(clientX, clientY) {
    const rect = container.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    pointer.set((clientX - rect.left) / rect.width * 2 - 1, -(clientY - rect.top) / rect.height * 2 + 1);
    updateCamera(); raycaster.setFromCamera(pointer, camera); return true;
  }
  function placementAt(clientX, clientY) {
    if (!placement || !ghost || mode !== 'edit' || !updateRay(clientX, clientY)) return;
    const hit = raycaster.intersectObjects(visibleMeshes(), false)[0];
    let surfaceY = 0, hitPoint;
    if (hit) {
      const record = records.find(r => r.mesh === hit.object);
      const bounds = new THREE.Box3().setFromObject(hit.object);
      const supportingPart = record ? findPart(record.spec.partId) : null;
      const supportingShape = record?.spec.shape || supportingPart?.shape || 'brick';
      const hasStuds = record?.spec.studs ?? supportingPart?.studs ?? !['tile', 'beam', 'connector'].includes(supportingShape);
      surfaceY = bounds.max.y - MODEL_LIFT - (hasStuds && !['beam', 'connector'].includes(supportingShape) ? .075 : 0); // Studs mate into the underside rather than raising the next body.
      hitPoint = hit.point;
      // A side hit still previews the top of that brick, matching stud-based placement.
      if (!record) surfaceY = 0;
    } else {
      if (!raycaster.ray.intersectPlane(groundPlane, planePoint)) { ghost.visible = ghostEdges.visible = false; ghostProposal = null; return; }
      hitPoint = planePoint;
    }
    const part = placement.part, rotation = placement.rotation;
    const quarter = Math.round(rotation / (Math.PI / 2));
    const width = Math.abs(quarter % 2) ? part.d : part.w, depth = Math.abs(quarter % 2) ? part.w : part.d;
    const snap = (value, studs) => (Math.round(value / U - studs / 2) + studs / 2) * U;
    const proposal = {
      x: clamp(snap(hitPoint.x, width), -8 * U, 8 * U),
      y: Math.max(0, surfaceY) + part.h / 2,
      z: clamp(snap(hitPoint.z, depth), -8 * U, 8 * U),
      ry: rotation, rz: 0
    };
    const candidate = { ...part, ...proposal, partId: part.id, id: '__placement__', color: placement.color, type: 'body' };
    let validation;
    try { validation = validatePlacement ? validatePlacement(candidate) : canPlace(pieces, candidate, null); } catch { validation = { ok: true }; }
    ghostValid = typeof validation === 'boolean' ? validation : validation?.ok !== false;
    ghostProposal = proposal;
    ghost.position.set(proposal.x, proposal.y + MODEL_LIFT, proposal.z); ghost.rotation.set(0, rotation, 0); ghost.visible = true;
    ghostMaterial.color.set(placement.color || '#d47b4e'); ghostMaterial.opacity = ghostValid ? .42 : .24;
    ghostLineMaterial.color.set(ghostValid ? '#829b58' : '#c96349');
    ghostEdges.position.copy(ghost.position); ghostEdges.rotation.copy(ghost.rotation); ghostEdges.visible = true;
    container.dataset.placementValid = String(ghostValid); needsFrame = true;
  }
  function frame(time) {
    if (disposed) return;
    const dt = Math.min((time - lastTime) / 1000, .05); lastTime = time;
    let moving = false;
    if (visible && !document.hidden && !contextLost) {
      if (autoRotate && !placement) { targetTheta += dt * .2; moving = true; }
      const ease = reducedMotion ? 1 : 1 - Math.pow(.002, dt);
      if (Math.abs(theta - targetTheta) + Math.abs(phi - targetPhi) + Math.abs(zoom - targetZoom) > .0001) moving = true;
      theta = THREE.MathUtils.lerp(theta, targetTheta, ease); phi = THREE.MathUtils.lerp(phi, targetPhi, ease); zoom = THREE.MathUtils.lerp(zoom, targetZoom, ease);
      for (const rec of records) {
        if (!rec.mesh.visible) continue;
        const explodeOffset = exploded ? finite(rec.spec.layer, 1) * .245 : 0;
        const targetY = rec.base.y + explodeOffset;
        if (Math.abs(rec.mesh.position.y - targetY) > .002) { rec.mesh.position.y = THREE.MathUtils.lerp(rec.mesh.position.y, targetY, Math.min(1, ease * 1.3)); moving = true; }
        else rec.mesh.position.y = targetY;
      }
      if (moving || needsFrame) { updateCamera(); updateSelection(); renderer.render(scene, camera); needsFrame = false; }
    }
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  const activePointers = new Map();
  let pointerDown = false, startX = 0, startY = 0, lastX = 0, lastY = 0, dragged = false, pinchDistance = 0, lastPointer = null;
  const distanceBetweenPointers = () => { const a = [...activePointers.values()]; return a.length >= 2 ? Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y) : 0; };
  function onPointerDown(event) {
    if (event.button !== 0) return;
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    pointerDown = true; dragged = false; startX = lastX = event.clientX; startY = lastY = event.clientY;
    if (activePointers.size > 1) { pinchDistance = distanceBetweenPointers(); dragged = true; }
    try { container.setPointerCapture(event.pointerId); } catch { /* Capture may be unavailable in an embedded browser. */ }
    lastPointer = { x: event.clientX, y: event.clientY };
    if (placement) placementAt(event.clientX, event.clientY);
  }
  function onPointerMove(event) {
    lastPointer = { x: event.clientX, y: event.clientY };
    if (activePointers.has(event.pointerId)) activePointers.set(event.pointerId, lastPointer);
    if (activePointers.size > 1) {
      const next = distanceBetweenPointers();
      if (pinchDistance > 0) targetZoom = clamp(targetZoom * next / pinchDistance, .48, 2.4);
      pinchDistance = next; dragged = true; needsFrame = true; return;
    }
    if (pointerDown) {
      const dx = event.clientX - lastX, dy = event.clientY - lastY;
      if (Math.hypot(event.clientX - startX, event.clientY - startY) > 7) dragged = true;
      if (dragged) { targetTheta -= dx * .008; targetPhi = clamp(targetPhi + dy * .006, .10, 1.12); container.classList.add('dragging'); needsFrame = true; }
      lastX = event.clientX; lastY = event.clientY;
    }
    if (placement && !dragged) placementAt(event.clientX, event.clientY);
  }
  function onPointerUp(event) {
    const wasDown = activePointers.has(event.pointerId); activePointers.delete(event.pointerId);
    if (!wasDown) return;
    const wasDragged = dragged;
    pointerDown = activePointers.size > 0; container.classList.remove('dragging');
    try { container.releasePointerCapture(event.pointerId); } catch { /* Safe if the browser already released capture. */ }
    if (pointerDown) { dragged = true; return; }
    if (!wasDragged) {
      if (placement && mode === 'edit') { placementAt(event.clientX, event.clientY); if (ghostProposal) onPlace?.({ ...ghostProposal }); }
      else if (updateRay(event.clientX, event.clientY)) { const hit = raycaster.intersectObjects(visibleMeshes(), false)[0]; onSelect?.(hit ? hit.object.userData.id : null); }
    }
    dragged = false;
  }
  function onCancel(event) { activePointers.delete(event.pointerId); pointerDown = activePointers.size > 0; dragged = false; pinchDistance = 0; container.classList.remove('dragging'); }
  function onLeave() { if (!pointerDown && ghost) { ghost.visible = false; if (ghostEdges) ghostEdges.visible = false; needsFrame = true; } }
  const onWheel = event => { event.preventDefault(); targetZoom = clamp(targetZoom * (event.deltaY > 0 ? .94 : 1.06), .48, 2.4); needsFrame = true; };
  const onContextLost = event => { event.preventDefault(); contextLost = true; onError?.(new Error('3D 视图暂时中断，请刷新页面后重试。')); };
  const onContextRestored = () => { contextLost = false; needsFrame = true; };
  container.addEventListener('pointerdown', onPointerDown); container.addEventListener('pointermove', onPointerMove); container.addEventListener('pointerup', onPointerUp);
  container.addEventListener('pointercancel', onCancel); container.addEventListener('pointerleave', onLeave); container.addEventListener('wheel', onWheel, { passive: false });
  renderer.domElement.addEventListener('webglcontextlost', onContextLost); renderer.domElement.addEventListener('webglcontextrestored', onContextRestored);

  function renderExport(size, { title = '我的积木作品', colorName = '', poster = true } = {}) {
    const oldSize = renderer.getSize(new THREE.Vector2()), oldRatio = renderer.getPixelRatio();
    const old = { theta, phi, zoom, exploded, selection: selectionBox.visible, ghost: ghost?.visible, ghostEdges: ghostEdges?.visible, background: scene.background };
    const recordStates = records.map(rec => ({ rec, visible: rec.mesh.visible, y: rec.mesh.position.y }));
    const output = document.createElement('canvas'); output.width = output.height = size;
    const context = output.getContext('2d'); context.fillStyle = GROUND_COLOR; context.fillRect(0, 0, size, size);
    try {
      for (const rec of records) { rec.mesh.visible = true; rec.mesh.position.y = rec.base.y; }
      selectionBox.visible = false; if (ghost) ghost.visible = false; if (ghostEdges) ghostEdges.visible = false;
      exploded = false; zoom = 1;
      if (!poster) { theta = -.53; phi = .4; }
      const sceneWidth = poster ? Math.round(size * 1.2) : size, sceneHeight = poster ? Math.round(size * .76) : size;
      renderer.setPixelRatio(1); renderer.setSize(sceneWidth, sceneHeight, false); updateCamera(sceneWidth / sceneHeight); modelGroup.updateMatrixWorld(true); renderer.render(scene, camera);
      if (poster) {
        context.drawImage(renderer.domElement, (size - sceneWidth) / 2, size * .11, sceneWidth, sceneHeight);
        const pad = size * .06;
        context.fillStyle = '#d86d47'; context.font = `600 ${Math.round(size * .039)}px Outfit, sans-serif`; context.fillText('brick.it', pad, size * .078);
        context.fillStyle = '#8e9580'; context.font = `${Math.round(size * .0105)}px Outfit, sans-serif`; context.fillText('MAKE IT YOURS. ONE BRICK AT A TIME.', pad, size * .105);
        context.fillStyle = '#3f4934'; context.font = `500 ${Math.round(size * .029)}px Outfit, PingFang SC, sans-serif`;
        const safeTitle = String(title).slice(0, 42); const maxWidth = size * .86; let displayTitle = safeTitle;
        while (context.measureText(displayTitle).width > maxWidth && displayTitle.length > 4) displayTitle = displayTitle.slice(0, -2) + '…';
        context.fillText(displayTitle, pad, size * .9);
        context.fillStyle = '#8e9580'; context.font = `${Math.round(size * .012)}px Outfit, PingFang SC, sans-serif`;
        context.fillText(`${records.length} 颗积木${colorName ? `  /  ${String(colorName).slice(0, 20)}` : ''}  /  MADE BY YOU`, pad, size * .933);
        context.strokeStyle = '#dce0d1'; context.beginPath(); context.moveTo(pad, size * .96); context.lineTo(size - pad, size * .96); context.stroke();
      } else context.drawImage(renderer.domElement, 0, 0, size, size);
    } finally {
      recordStates.forEach(({ rec, visible, y }) => { rec.mesh.visible = visible; rec.mesh.position.y = y; });
      theta = old.theta; phi = old.phi; zoom = old.zoom; exploded = old.exploded; selectionBox.visible = old.selection;
      if (ghost) ghost.visible = old.ghost; if (ghostEdges) ghostEdges.visible = old.ghostEdges; scene.background = old.background;
      renderer.setPixelRatio(oldRatio); renderer.setSize(oldSize.x, oldSize.y, false); updateCamera(); modelGroup.updateMatrixWorld(true); updateSelection(); renderer.render(scene, camera); needsFrame = true;
    }
    return output;
  }

  const api = {
    loadPieces(nextPieces = []) {
      if (disposed) return;
      pieces = nextPieces.map((piece, i) => ({ ...piece, id: piece.id ?? `piece-${i}`, w: Math.max(1, finite(piece.w, 1)), d: Math.max(1, finite(piece.d, 1)), h: Math.max(.08, finite(piece.h, .56)), x: finite(piece.x), y: finite(piece.y), z: finite(piece.z), ry: finite(piece.ry), rz: finite(piece.rz), layer: finite(piece.layer, 1) }));
      modelGroup.clear(); records = [];
      for (const spec of pieces) {
        const { geometry, key } = geometryFor(spec);
        const defaultColor = spec.type === 'leaf' ? '#648a35' : spec.type === 'stem' ? '#76502f' : PALETTES.red.color;
        const mesh = new THREE.Mesh(geometry, materialFor(spec.color || defaultColor)); mesh.castShadow = true; mesh.receiveShadow = true; mesh.userData.id = spec.id;
        mesh.position.set(spec.x, spec.y, spec.z); mesh.rotation.set(0, spec.ry, spec.rz); modelGroup.add(mesh);
        records.push({ spec, mesh, base: new THREE.Vector3(spec.x, spec.y, spec.z), geometryKey: key });
      }
      progress = records.length;
      if (!records.some(r => sameId(r.spec.id, selectedId))) selectedId = null;
      updateBounds(); modelGroup.updateMatrixWorld(true); pruneGeometry(); updateSelection(); needsFrame = true;
    },
    setProgress(value, { animate = true } = {}) {
      if (disposed) return;
      const previous = progress; progress = clamp(Math.round(finite(value, records.length)), 0, records.length);
      records.forEach((rec, i) => {
        const visible = mode === 'edit' || i < progress;
        const newlyShown = visible && !rec.mesh.visible;
        rec.mesh.visible = visible;
        const target = rec.base.y + (exploded ? rec.spec.layer * .245 : 0);
        if (newlyShown && animate && !reducedMotion && i === progress - 1 && progress > previous) rec.mesh.position.y = target + 1.75;
        else if (!animate || !visible || i !== progress - 1) rec.mesh.position.y = target;
      });
      updateSelection(); needsFrame = true;
    },
    setMode(value) {
      mode = value === 'edit' ? 'edit' : 'build';
      if (mode === 'edit') { records.forEach(rec => { rec.mesh.visible = true; }); }
      else { placement = null; clearGhost(); records.forEach((rec, i) => { rec.mesh.visible = i < progress; }); }
      container.dataset.modelMode = mode; container.style.cursor = placement ? 'crosshair' : 'grab';
      updateSelection(); needsFrame = true;
    },
    selectPiece(id) { selectedId = id; updateSelection(); needsFrame = true; },
    setPlacement(part, color = '#d86d47', rotationRadians = 0) {
      placement = part ? { part: { ...part }, color, rotation: finite(rotationRadians) } : null;
      if (placement) { autoRotate = false; exploded = false; targetZoom = Math.min(targetZoom, 1.1); }
      createGhost(); container.style.cursor = placement ? 'crosshair' : 'grab';
      delete container.dataset.placementValid;
      if (lastPointer && placement) placementAt(lastPointer.x, lastPointer.y);
      needsFrame = true;
    },
    setPlacementValidator(fn) { validatePlacement = typeof fn === 'function' ? fn : null; if (lastPointer && placement) placementAt(lastPointer.x, lastPointer.y); },
    setExploded(value) { exploded = !!value; if (exploded) { placement = null; clearGhost(); container.style.cursor = 'grab'; delete container.dataset.placementValid; } targetZoom = exploded ? .76 : 1; needsFrame = true; },
    setAutoRotate(value) { autoRotate = !!value; needsFrame = true; },
    zoomBy(factor) { targetZoom = clamp(targetZoom * finite(factor, 1), .48, 2.4); needsFrame = true; },
    resetView() { targetTheta = -.53; targetPhi = .4; targetZoom = exploded ? .76 : 1; needsFrame = true; },
    async snapshot(exportOptions = {}) { const canvas = renderExport(1800, { ...exportOptions, poster: true }); return new Promise(resolve => canvas.toBlob(resolve, 'image/png')); },
    async thumbnail() { return renderExport(480, { poster: false }).toDataURL('image/jpeg', .84); },
    dispose() {
      if (disposed) return; disposed = true; cancelAnimationFrame(raf); ro.disconnect();
      container.removeEventListener('pointerdown', onPointerDown); container.removeEventListener('pointermove', onPointerMove); container.removeEventListener('pointerup', onPointerUp); container.removeEventListener('pointercancel', onCancel); container.removeEventListener('pointerleave', onLeave); container.removeEventListener('wheel', onWheel);
      renderer.domElement.removeEventListener('webglcontextlost', onContextLost); renderer.domElement.removeEventListener('webglcontextrestored', onContextRestored);
      clearGhost(); ghostMaterial.dispose(); ghostLineMaterial.dispose(); selectionBox.geometry.dispose(); selectionBox.material.dispose();
      geometryCache.forEach(g => g.dispose()); geometryCache.clear(); materialCache.forEach(m => m.dispose()); materialCache.clear();
      studGeometry.dispose(); staticResources.forEach(r => r.dispose?.()); environment.dispose(); renderer.dispose(); renderer.domElement.remove();
    },
    get state() { return { mode, progress, count: records.length, pieces: records.filter(r => r.mesh.visible).length, selectedId, placement: placement?.part.id || null, placementValid: ghostValid, exploded, autoRotate, theta, targetTheta, phi, zoom, targetZoom, renderCalls: renderer.info.render.calls }; }
  };
  updateCamera(); needsFrame = true;
  requestAnimationFrame(() => { if (!disposed) onReady?.(); });
  return api;
}
