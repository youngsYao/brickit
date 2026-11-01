import * as THREE from 'three';
import { LDrawLoader } from './assets/vendor/LDrawLoader.js';
import { LDrawConditionalLineMaterial } from './assets/vendor/LDrawConditionalLineMaterial.js';
import { RoomEnvironment } from './assets/vendor/RoomEnvironment.js';

const LIBRARY_PATH = '/api/ldraw/';
const WIDTH = 256, HEIGHT = 192;
const MODEL_LIMIT = 48, THUMBNAIL_LIMIT = 96;
const models = new Map(), thumbnails = new Map(), pending = new Map();
let shared = null, queue = Promise.resolve(), epoch = 0;

export class PartPreviewError extends Error {
  constructor(message, code, cause) {
    super(message); this.name = 'PartPreviewError'; this.code = code;
    if (cause) this.cause = cause;
  }
}

function normalizeInput(ldrawId, color) {
  const id = String(ldrawId ?? '').trim().replace(/\.dat$/i, '').toLowerCase();
  if (!/^[a-z0-9][a-z0-9_-]{0,95}$/.test(id)) throw new PartPreviewError('零件编号无效，无法加载预览。', 'INVALID_PART_ID');
  let hex = String(color ?? '').trim().toLowerCase();
  if (/^#[0-9a-f]{3}$/.test(hex)) hex = '#' + [...hex.slice(1)].map(char => char + char).join('');
  if (!/^#[0-9a-f]{6}$/.test(hex)) throw new PartPreviewError('零件颜色无效，无法加载预览。', 'INVALID_COLOR');
  return { id, color: hex, key: `${id}|${hex}` };
}

function touch(cache, key, value) {
  cache.delete(key); cache.set(key, value); return value;
}
function materialsOf(object) { return Array.isArray(object.material) ? object.material : object.material ? [object.material] : []; }
function resourcesOf(root, into = new Set()) {
  root?.traverse(object => {
    if (object.geometry?.isBufferGeometry) into.add(object.geometry);
    for (const material of materialsOf(object)) if (material?.isMaterial) into.add(material);
  });
  return into;
}
function loaderMaterials(loader, into = new Set()) {
  for (const material of [...loader.materials, loader.missingColorMaterial]) {
    if (!material?.isMaterial) continue;
    into.add(material);
    const edge = loader.edgeMaterialCache.get(material);
    if (edge?.isMaterial) into.add(edge);
    const conditional = edge && loader.conditionalEdgeMaterialCache.get(edge);
    if (conditional?.isMaterial) into.add(conditional);
  }
  for (const material of [loader.missingEdgeColorMaterial, loader.missingConditionalEdgeColorMaterial]) if (material?.isMaterial) into.add(material);
  return into;
}
function disposeResources(resources, protectedResources = new Set()) {
  for (const resource of resources) if (!protectedResources.has(resource)) resource.dispose();
}

async function createSharedState() {
  const palette = new LDrawLoader().setConditionalLineMaterial(LDrawConditionalLineMaterial).setPartsLibraryPath(LIBRARY_PATH);
  let renderer, environment;
  try {
    await palette.preloadMaterials(`${LIBRARY_PATH}LDConfig.ldr`);
    if (!palette.materials.length) throw new Error('LDraw color definitions are unavailable');
    palette.addDefaultMaterials();
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true, powerPreference: 'low-power' });
    renderer.setPixelRatio(1); renderer.setSize(WIDTH, HEIGHT, false);
    renderer.setClearColor(0xf7f6f2, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.1;
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xfffcf3, 0x8d8d7e, 2.4));
    const key = new THREE.DirectionalLight(0xfff5e7, 3.2); key.position.set(-4, 8, 6); scene.add(key);
    const fill = new THREE.DirectionalLight(0xeef2ff, 1.3); fill.position.set(6, 3, -5); scene.add(fill);
    const room = new RoomEnvironment(), pmrem = new THREE.PMREMGenerator(renderer);
    try { environment = pmrem.fromScene(room, .06); }
    finally { room.dispose(); pmrem.dispose(); }
    scene.environment = environment.texture; scene.environmentIntensity = .5;
    const state = { renderer, scene, palette, paletteResources: loaderMaterials(palette), environment, contextLost: false };
    state.onContextLost = event => { event.preventDefault(); state.contextLost = true; };
    renderer.domElement.addEventListener('webglcontextlost', state.onContextLost);
    return state;
  } catch (cause) {
    environment?.dispose(); renderer?.dispose(); renderer?.forceContextLoss();
    disposeResources(loaderMaterials(palette));
    throw new PartPreviewError('零件预览尚不可用，请检查 LDraw 零件库或浏览器图形支持。', 'PREVIEW_UNAVAILABLE', cause);
  }
}

/** A loader is temporary per model. Its geometry caches never cross model
 * ownership boundaries; only the immutable LDConfig material palette is shared.
 * This makes evicting an LRU entry safe even when its own subparts share buffers.
 */
async function loadModel(state, id) {
  const cached = models.get(id);
  if (cached) return touch(models, id, cached);
  const loader = new LDrawLoader().setConditionalLineMaterial(LDrawConditionalLineMaterial).setPartsLibraryPath(LIBRARY_PATH);
  // Dispose the constructor's private missing-color sentinels before switching
  // to the shared palette. Standard colors and fixed printed colors remain intact.
  disposeResources(loaderMaterials(loader));
  loader.materials = [...state.palette.materials];
  loader.materialLibrary = { ...state.palette.materialLibrary };
  loader.edgeMaterialCache = state.palette.edgeMaterialCache;
  loader.conditionalEdgeMaterialCache = state.palette.conditionalEdgeMaterialCache;
  loader.missingColorMaterial = state.palette.missingColorMaterial;
  loader.missingEdgeColorMaterial = state.palette.missingEdgeColorMaterial;
  loader.missingConditionalEdgeColorMaterial = state.palette.missingConditionalEdgeColorMaterial;

  // r180 catches some nested-part errors and returns partial geometry. Record
  // those failures before its catch: a thumbnail must represent the whole part.
  const failures = [];
  const originalLoadModel = loader.partsCache.loadModel.bind(loader.partsCache);
  loader.partsCache.loadModel = async name => {
    try { return await originalLoadModel(name); }
    catch (error) { failures.push(error); throw error; }
  };
  let root, keep = new Set();
  try {
    root = await loader.loadAsync(`${LIBRARY_PATH}parts/${encodeURIComponent(id)}.dat`);
    if (failures.length) throw failures[0];
    let faces = 0, invalidMaterial = false;
    root.traverse(object => {
      if (object.isMesh) faces += object.geometry?.attributes.position?.count || 0;
      if (materialsOf(object).some(material => !material?.isMaterial || material === loader.missingColorMaterial || material === loader.missingEdgeColorMaterial || material === loader.missingConditionalEdgeColorMaterial)) invalidMaterial = true;
    });
    if (!faces || invalidMaterial) throw new Error('The complete LDraw part or its materials are unavailable');
    const bounds = new THREE.Box3().setFromObject(root), extent = bounds.getSize(new THREE.Vector3());
    if (bounds.isEmpty() || ![...bounds.min, ...bounds.max].every(Number.isFinite) || extent.length() <= 0) throw new Error('The LDraw part has no renderable geometry');
    keep = resourcesOf(root);
    const entry = { root, resources: new Set([...keep].filter(resource => !state.paletteResources.has(resource))) };
    touch(models, id, entry);
    while (models.size > MODEL_LIMIT) {
      const oldest = models.keys().next().value;
      disposeResources(models.get(oldest).resources); models.delete(oldest);
    }
    return entry;
  } catch (cause) {
    throw new PartPreviewError('无法加载此零件的完整 LDraw 模型，请稍后重试。', 'PART_LOAD_FAILED', cause);
  } finally {
    // Await all already-started cache work before deciding what can be released.
    // Cached subpart clones can share buffers inside this temporary loader only.
    const groups = await Promise.allSettled(Object.values(loader.partsCache._cache));
    const allocated = loaderMaterials(loader);
    resourcesOf(root, allocated);
    for (const result of groups) if (result.status === 'fulfilled') resourcesOf(result.value, allocated);
    const retained = new Set([...state.paletteResources, ...keep]);
    disposeResources(allocated, retained);
    loader.partsCache._cache = {};
    loader.partsCache.parseCache._cache = {};
  }
}

function renderModel(state, entry, color) {
  if (state.contextLost || state.renderer.getContext().isContextLost()) throw new PartPreviewError('零件预览的图形连接已中断，请重新加载预览。', 'CONTEXT_LOST');
  const root = entry.root.clone(true), upright = new THREE.Group(), centered = new THREE.Group();
  upright.rotation.x = Math.PI; upright.add(root); centered.add(upright);
  const temporaryMaterials = new Map();
  root.traverse(object => {
    if (!object.isMesh && !object.isLineSegments) return;
    const style = original => {
      const tint = object.isMesh && String(original.userData.code) === '16';
      const edge = object.isLineSegments;
      if (!tint && !edge) return original;
      const key = `${original.uuid}|${edge ? 'edge' : 'main'}`;
      if (!temporaryMaterials.has(key)) {
        const material = original.clone();
        if (tint) material.color.set(color);
        if (edge) { material.color.set('#514c44'); material.transparent = true; material.opacity = .42; material.depthWrite = false; }
        temporaryMaterials.set(key, material);
      }
      return temporaryMaterials.get(key);
    };
    object.material = Array.isArray(object.material) ? object.material.map(style) : style(object.material);
  });
  try {
    centered.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(centered), extent = bounds.getSize(new THREE.Vector3());
    centered.position.sub(bounds.getCenter(new THREE.Vector3())); centered.updateMatrixWorld(true);
    const distance = Math.max(100, extent.length() * 3);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, .1, distance + extent.length() * 3);
    camera.position.copy(new THREE.Vector3(1, .8, 1.2).normalize().multiplyScalar(distance));
    camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const viewBounds = new THREE.Box3(), point = new THREE.Vector3();
    centered.traverse(object => {
      if (!object.geometry) return;
      object.geometry.computeBoundingBox(); const local = object.geometry.boundingBox;
      for (const x of [local.min.x,local.max.x]) for (const y of [local.min.y,local.max.y]) for (const z of [local.min.z,local.max.z]) {
        point.set(x,y,z).applyMatrix4(object.matrixWorld).applyMatrix4(camera.matrixWorldInverse); viewBounds.expandByPoint(point);
      }
    });
    const viewCenter = viewBounds.getCenter(new THREE.Vector3());
    camera.position.add(new THREE.Vector3(viewCenter.x,viewCenter.y,0).applyQuaternion(camera.quaternion));
    const halfHeight = Math.max(viewBounds.max.y-viewBounds.min.y, (viewBounds.max.x-viewBounds.min.x)/(WIDTH/HEIGHT), .01) * .61;
    camera.left = -halfHeight*WIDTH/HEIGHT; camera.right = halfHeight*WIDTH/HEIGHT;
    camera.top = halfHeight; camera.bottom = -halfHeight; camera.updateProjectionMatrix(); camera.updateMatrixWorld(true);
    state.scene.add(centered); state.renderer.render(state.scene, camera);
    if (state.contextLost || state.renderer.getContext().isContextLost()) throw new PartPreviewError('零件预览的图形连接已中断，请重新加载预览。', 'CONTEXT_LOST');
    return state.renderer.domElement.toDataURL('image/png');
  } finally {
    state.scene.remove(centered);
    for (const material of temporaryMaterials.values()) material.dispose();
  }
}

/** Render a complete catalog part. One offscreen WebGL context serves every
 * card. Calls (including different colors) are queued so output cannot mix.
 * Failures reject; the caller should present a retry/loading/error state.
 */
export async function renderPartThumbnail(ldrawId, color = '#b91620') {
  const input = normalizeInput(ldrawId, color), version = epoch;
  if (thumbnails.has(input.key)) return touch(thumbnails, input.key, thumbnails.get(input.key));
  if (pending.has(input.key)) return pending.get(input.key);
  const task = queue.catch(() => {}).then(async () => {
    if (version !== epoch) throw new PartPreviewError('零件预览已取消。', 'PREVIEW_DISPOSED');
    shared ||= await createSharedState();
    const entry = await loadModel(shared, input.id);
    if (version !== epoch) throw new PartPreviewError('零件预览已取消。', 'PREVIEW_DISPOSED');
    const dataURL = renderModel(shared, entry, input.color);
    touch(thumbnails, input.key, dataURL);
    while (thumbnails.size > THUMBNAIL_LIMIT) thumbnails.delete(thumbnails.keys().next().value);
    return dataURL;
  });
  pending.set(input.key, task); queue = task.catch(() => {});
  try { return await task; }
  finally { if (pending.get(input.key) === task) pending.delete(input.key); }
}

/** Cancel queued work, then release templates, palette and the single context.
 * Await this during teardown; subsequent render calls lazily create a new one.
 */
export function disposePartPreviews() {
  epoch++; thumbnails.clear(); pending.clear();
  const cleanup = queue.catch(() => {}).then(() => {
    for (const entry of models.values()) disposeResources(entry.resources);
    models.clear();
    if (shared) {
      disposeResources(shared.paletteResources); shared.environment.dispose();
      shared.renderer.domElement.removeEventListener('webglcontextlost', shared.onContextLost);
      shared.renderer.dispose(); shared.renderer.forceContextLoss(); shared = null;
    }
  });
  queue = cleanup; return cleanup;
}
