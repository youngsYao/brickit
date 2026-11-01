/** Browse the local LDraw library without loading the whole library into the DOM. */
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const PAGE_SIZE = 24;
const categoryLabels = { brick:'标准砖', plate:'薄板', tile:'光面砖', slope:'斜坡件', technic:'科技零件', minifig:'人仔零件', animal:'动物', wheel:'轮毂', tyre:'轮胎', window:'窗户', door:'门', baseplate:'底板', round:'圆形件', plant:'植物', arch:'拱形件', hinge:'铰链', panel:'面板', windscreen:'挡风玻璃', electric:'电动零件' };

export function createPartsCatalog({ fallback }) {
  const grid = document.querySelector('#catalog-grid');
  const count = document.querySelector('#catalog-count');
  const notice = document.querySelector('#catalog-library-status');
  const pagination = document.querySelector('#catalog-pagination');
  const categorySelect = document.querySelector('#catalog-category');
  let query = '', category = 'all', color = '#b91620', offset = 0, total = 0;
  let controller, observer, version = 0, timer, items = [], available = false;
  let loadedQuery = null, loadedCategory = null;
  let rendererPromise;
  const renderer = () => rendererPromise ||= import('./ldraw-preview.js');

  async function preview(node) {
    const token = version;
    if (!node.isConnected || node.dataset.previewState === 'loading' || node.dataset.previewState === 'ready') return;
    node.dataset.previewState = 'loading';
    node.innerHTML = '<span class="part-preview-status">正在渲染…</span>';
    try {
      const { renderPartThumbnail } = await renderer();
      const image = await renderPartThumbnail(node.dataset.ldrawId, color);
      if (token !== version || !node.isConnected) return;
      const img = new Image();
      img.alt = `${node.dataset.partName} 的真实 LDraw 预览`;
      img.src = image;
      img.width = 256; img.height = 192;
      node.replaceChildren(img);
      node.dataset.previewState = 'ready';
    } catch {
      if (token !== version || !node.isConnected) return;
      node.dataset.previewState = 'error';
      node.innerHTML = '<span class="part-preview-status">预览暂未加载</span><button type="button" class="part-preview-retry" data-retry-preview>重新加载</button>';
    }
  }

  function renderItems() {
    observer?.disconnect();
    grid.dataset.source = 'ldraw';
    grid.innerHTML = items.length ? items.map(part => `<article class="part-card ldraw-part-card" data-ldraw-part="${escapeHtml(part.ldrawId)}">
      <div class="part-thumb ldraw-part-preview" data-ldraw-id="${escapeHtml(part.ldrawId)}" data-part-name="${escapeHtml(part.name)}" data-preview-state="pending"><span class="part-preview-status">准备预览</span></div>
      <div class="part-meta">${escapeHtml(categoryLabels[part.category] || part.categoryName || part.category)}</div>
      <h3 class="part-name">${escapeHtml(part.name)}</h3>
      <div class="part-meta ldraw-part-id">LDraw ${escapeHtml(part.ldrawId)}</div>
      <button type="button" class="button button-secondary" data-action="use-ldraw-part" data-ldraw-id="${escapeHtml(part.ldrawId)}">在工作台使用</button>
    </article>`).join('') : '<div class="empty-state"><strong>没有找到匹配的零件</strong><p>试试零件编号、英文名称或其他分类。</p><button type="button" class="button button-secondary" data-action="clear-catalog">清除筛选</button></div>';
    count.textContent = `${total.toLocaleString('zh-CN')} 种零件${total ? ` · ${offset + 1}–${offset + items.length}` : ''}`;
    pagination.hidden = total <= PAGE_SIZE;
    document.querySelector('#catalog-page-label').textContent = `第 ${Math.floor(offset / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(total / PAGE_SIZE))} 页`;
    document.querySelector('[data-catalog-page="previous"]').disabled = offset === 0;
    document.querySelector('[data-catalog-page="next"]').disabled = offset + PAGE_SIZE >= total;
    observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        observer.unobserve(entry.target);
        preview(entry.target);
      }
    }, { rootMargin: '100px' });
    grid.querySelectorAll('[data-preview-state]').forEach(node => observer.observe(node));
  }

  async function fetchPage() {
    const token = ++version;
    const requestedQuery = query, requestedCategory = category;
    controller?.abort(); observer?.disconnect();
    controller = new AbortController();
    grid.setAttribute('aria-busy', 'true');
    count.textContent = '正在读取 LDraw 零件库…';
    const url = new URL('/api/parts', location.origin);
    url.search = new URLSearchParams({ q: query, category: category === 'all' ? '' : category, offset: String(offset), limit: String(PAGE_SIZE) });
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Library unavailable');
      const data = await response.json();
      if (!data.available || !Array.isArray(data.items)) throw new Error('Library unavailable');
      if (token !== version) return;
      available = true; items = data.items; total = data.total;
      loadedQuery = requestedQuery; loadedCategory = requestedCategory;
      if (Array.isArray(data.categories)) {
        categorySelect.innerHTML = '<option value="all">全部分类</option>' + data.categories.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(categoryLabels[item.id] || item.label)}${item.count ? ` (${item.count})` : ''}</option>`).join('');
        categorySelect.value = category;
      }
      notice.textContent = `LDraw 本地零件库 · 已下载 ${(data.libraryTotal || data.total).toLocaleString('zh-CN')} 个文件 · 可浏览 ${(data.catalogueTotal || data.total).toLocaleString('zh-CN')} 种零件 · 预览颜色不代表实际在售颜色`;
      notice.dataset.state = 'ready';
      renderItems();
    } catch (error) {
      if (token !== version || error.name === 'AbortError') return;
      available = false; pagination.hidden = true;
      notice.textContent = 'LDraw 零件库暂时无法连接。当前显示示例及已保存的零件，请启动本地服务后重试。';
      notice.dataset.state = 'offline';
      grid.dataset.source = 'local';
      fallback();
    } finally {
      if (token === version) grid.setAttribute('aria-busy', 'false');
    }
  }

  pagination.addEventListener('click', event => {
    const button = event.target.closest('[data-catalog-page]');
    if (!button || button.disabled) return;
    offset = Math.max(0, offset + (button.dataset.catalogPage === 'next' ? PAGE_SIZE : -PAGE_SIZE));
    fetchPage();
    document.querySelector('#catalog-title').scrollIntoView({ behavior: 'instant', block: 'start' });
  });
  grid.addEventListener('click', event => {
    const retry = event.target.closest('[data-retry-preview]');
    if (retry) preview(retry.closest('[data-preview-state]'));
  });

  return {
    load(options = {}) {
      const nextQuery = String(options.query || '').trim(), nextCategory = options.category || 'all', nextColor = options.color || '#b91620';
      const filtersChanged = nextQuery !== query || nextCategory !== category;
      const colorChanged = nextColor !== color;
      query = nextQuery; category = nextCategory; color = nextColor;
      clearTimeout(timer);
      if (filtersChanged) offset = 0;
      if (available && colorChanged && !filtersChanged && loadedQuery === query && loadedCategory === category) { ++version; renderItems(); return; }
      // Delay text searches so typing doesn't start a library request per keystroke.
      timer = setTimeout(fetchPage, filtersChanged && query ? 180 : 0);
    },
  };
}

export async function fetchLDrawPart(id) {
  const response = await fetch(`/api/parts/${encodeURIComponent(id)}`, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error('这个零件暂时无法导入工作台，请稍后重试。');
  const part = await response.json();
  if (!part?.mesh || !part.ldrawId) throw new Error('零件缺少有效的三维网格。');
  return part;
}
