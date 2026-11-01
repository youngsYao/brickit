/** Same-origin access to the local BrickWright service. Secrets stay server-side. */
export class GenerationError extends Error {
  constructor(message, code = 'REQUEST_FAILED', status = 0) {
    super(message);
    this.name = 'GenerationError';
    this.code = code;
    this.status = status;
  }
}

async function request(path, { signal, ...options } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const error = body?.error || body?.detail;
      throw new GenerationError(
        (typeof error === 'object' && error?.message) || (typeof error === 'string' && error) || '生成服务暂时无法完成请求，请稍后重试。',
        error?.code || `HTTP_${response.status}`, response.status,
      );
    }
    if (!body || typeof body !== 'object') throw new GenerationError('生成服务返回的数据无法识别。', 'INVALID_RESPONSE');
    return body;
  } catch (error) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (error instanceof GenerationError) throw error;
    throw new GenerationError('暂时无法连接生成服务，请确认本地服务已启动后重试。', 'SERVICE_UNAVAILABLE');
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}

export const generationApi = {
  getCapabilities: options => request('/api/generation/capabilities', options),
  start: (input, options) => request('/api/generations', { ...options, method: 'POST', body: JSON.stringify(input) }),
  get: (id, options) => request(`/api/generations/${encodeURIComponent(id)}`, options),
  cancel: (id, options) => request(`/api/generations/${encodeURIComponent(id)}`, { ...options, method: 'DELETE' }),
  artifactUrl: (id, name) => `/api/generations/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(name)}`,
};
