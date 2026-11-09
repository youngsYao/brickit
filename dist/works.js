import { library as browserLibrary, cloneProject, loadExampleProject } from './data.js';
import { GENERATED_EXAMPLES } from './examples/generated.js';

// The approved AI results are available in every browser without downloading
// their meshes until opened. Local saves take precedence over bundled entries.
const savedWorks = new Map(GENERATED_EXAMPLES.map(example => [example.id, {
  ...example, exampleId: example.id, source: 'ai', draft: false, favorite: false, version: 1,
}]));

function asSavedWork(project) {
  const copy = cloneProject(project);
  if (savedWorks.has(copy.id)) copy.source = 'ai';
  return copy;
}

export function createWorksLibrary(storage = browserLibrary) {
  return {
    get backend() { return storage.backend; },
    async list() {
      const local = await storage.list();
      const ids = new Set(local.map(project => project.id));
      return [
        ...local.filter(project => !project.removedBundledWork).map(asSavedWork),
        ...[...savedWorks.values()].filter(project => !ids.has(project.id)).map(cloneProject),
      ].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')) || a.id.localeCompare(b.id));
    },
    async get(id) {
      const key = String(id);
      const local = await storage.get(key);
      if (local) return local.removedBundledWork ? null : asSavedWork(local);
      const saved = savedWorks.get(key);
      if (!saved) return null;
      const project = await loadExampleProject(saved.exampleId);
      return { ...project, source: 'ai', createdAt: saved.createdAt, updatedAt: saved.updatedAt };
    },
    async put(project) {
      const copy = asSavedWork(project);
      delete copy.removedBundledWork;
      return storage.put(copy);
    },
    async remove(id) {
      const key = String(id), saved = savedWorks.get(key);
      if (saved) {
        // Keep a local removal record so refreshes do not recreate deleted work.
        await storage.put({ ...cloneProject(saved), pieces: [], removedBundledWork: true });
      } else {
        await storage.remove(key);
      }
      return true;
    },
  };
}

export const library = createWorksLibrary();
