// catalog.js - Cynovela v13

async function renderDataCatalog() {
  const summaryEl = document.getElementById('catalog-summary');
  const filtersEl = document.getElementById('catalog-filters');
  const tableEl   = document.getElementById('catalog-table-host');
  if (summaryEl) summaryEl.innerHTML = '<div style="padding:14px;color:#94a3b8;">' + bi('Loading...', '読み込み中...') + '</div>';
  try {
    // BETA-pagination
    const p = State.catalogPager;
    const qs = new URLSearchParams({ limit: p.limit, offset: (p.page - 1) * p.limit });
    if (p.category) qs.set('category', p.category);
    const data = await API.get(`/api/data-catalog?${qs}`);
    _catalogState.items = data.items || [];
    State.catalogPager.total = data.total != null ? data.total : (data.items || []).length;
    _pagerCallbacks['catalog'] = {
      page: (n) => { State.catalogPager.page = Math.max(1, n); renderDataCatalog(); },
      limit: (n) => { State.catalogPager.limit = n; State.catalogPager.page = 1; renderDataCatalog(); },
    };
    if (summaryEl) summaryEl.innerHTML = renderCatalogSummary(data);
    if (filtersEl) filtersEl.innerHTML = renderCatalogFilters(data);
    renderCatalogTable();
    const catPagerEl = document.getElementById('catalog-pager');
    if (catPagerEl) {
      catPagerEl.innerHTML = _renderPager({
        key: 'catalog',
        page: State.catalogPager.page,
        limit: State.catalogPager.limit,
        total: State.catalogPager.total,
      });
    }
  } catch (e) {
    if (summaryEl) summaryEl.innerHTML = `<div style="padding:14px;color:#ef4444;">${lj('Load failed','読み込み失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}

function renderCatalogSummary(data) {
  const sens = data.sensitivity_breakdown || {};
  const palette = {
    restricted:    {bg:'#fef2f2', border:'#fecaca', fg:'#991b1b', icon:'🔴'},
    confidential:  {bg:'#fffbeb', border:'#fde68a', fg:'#92400e', icon:'🟠'},
    internal:      {bg:'#f0f9ff', border:'#bae6fd', fg:'#0369a1', icon:'🟡'},
    public:        {bg:'#f0fdf4', border:'#bbf7d0', fg:'#15803d', icon:'🟢'},
  };
  const cards = ['restricted','confidential','internal','public'].map(k => {
    const p = palette[k];
    const v = sens[k] || 0;
    return `<div style="background:${p.bg};border:1px solid ${p.border};border-radius:10px;padding:12px 16px;text-align:center;flex:1;min-width:120px;">
      <div style="font-size:24px;font-weight:800;color:${p.fg};">${p.icon} ${v}</div>
      <div style="font-size:16px;color:${p.fg};margin-top:4px;font-weight:700;">${k}</div>
    </div>`;
  }).join('');
  return `
    <div style="margin-bottom:18px;">
      <div style="font-size:17px;color:#64748b;margin-bottom:8px;font-weight:700;">
        ${data.total} ${t('docs_sensitivity')}
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">${cards}</div>
    </div>`;
}

function renderCatalogFilters(data) {
  const sensOpts  = ['restricted','confidential','internal','public'];
  const typeOpts  = Object.keys(data.doc_type_breakdown || {});
  const deptOpts  = Object.keys(data.department_breakdown || {});
  const tagSet = new Set();
  (data.items || []).forEach(i => {
    (i.categories || []).forEach(t => tagSet.add(t));
    (i.auto_tags  || []).forEach(t => tagSet.add(t));
  });
  const tagOpts = Array.from(tagSet).sort();

  // GUI修正7: OKボタン方式のドロップダウン (チェックではフィルタ適用せず、OKで確定)
  const popover = (key, opts, label) => {
    const sel = _catalogState.filters[key];
    const count = sel.size;
    const summary = count === 0 ? t('all_option') : `${count} ${t('items_selected')}`;
    const checks = opts.map(o => `
      <label class="filter-checkbox-item"
             style="display:flex;align-items:center;gap:8px;padding:4px 8px;cursor:pointer;font-size:17px;border-radius:4px;"
             onmouseover="this.style.background='#f1f5f9'"
             onmouseout="this.style.background='transparent'">
        <input type="checkbox" data-key="${key}" data-val="${escapeHtml(o)}"
               ${sel.has(o) ? 'checked' : ''}
               onchange="onCatalogPendingChange(this);event.stopPropagation();"
               style="width:16px;height:16px;cursor:pointer;">
        <span>${escapeHtml(o)}</span>
      </label>`).join('');
    return `
      <div class="cat-filter-pop filter-dropdown" data-key="${key}" style="position:relative;">
        <button type="button" class="cat-filter-trigger"
                onclick="toggleCatFilterDropdown(this);event.stopPropagation();"
                style="cursor:pointer;padding:6px 12px;border:1px solid #e2e8f0;
                       border-radius:6px;background:#fff;font-size:17px;color:#475569;">
          ${label}: <strong class="cat-filter-trigger-label" style="color:${count>0?'#0369a1':'#94a3b8'};">${summary}</strong> ▾
        </button>
        <div class="cat-filter-menu"
             style="position:absolute;top:100%;left:0;background:#fff;border:1px solid #e2e8f0;
                    border-radius:6px;padding:0;margin-top:4px;z-index:50;
                    box-shadow:0 6px 18px rgba(0,0,0,0.12);min-width:200px;
                    display:none;"
             onclick="event.stopPropagation();">
          <div class="filter-dropdown-list" style="max-height:240px;overflow-y:auto;padding:6px 4px;">
            ${checks || `<div style="padding:8px;color:#94a3b8;font-size:16px;">${lj('No options','選択肢なし')}</div>`}
          </div>
          <div class="filter-dropdown-footer">
            <button type="button" class="filter-btn-clear"
                    onclick="onCatalogFilterClear('${key}', this);event.stopPropagation();">✕ ${lj('Clear','クリア')}</button>
            <button type="button" class="filter-btn-ok"
                    onclick="onCatalogFilterOk('${key}', this);event.stopPropagation();">✅ OK</button>
          </div>
        </div>
      </div>`;
  };
  return `
    ${popover('sensitivity', sensOpts, CYNOVELA_LANG==='en'?'Sensitivity':'感度')}
    ${popover('doc_type',    typeOpts, CYNOVELA_LANG==='en'?'Type':'種別')}
    ${popover('department',  deptOpts, CYNOVELA_LANG==='en'?'Dept':'部門')}
    ${popover('tag',         tagOpts,  CYNOVELA_LANG==='en'?'Tags':'タグ')}
    <label style="display:flex;align-items:center;gap:6px;font-size:17px;cursor:pointer;color:#92400e;">
      <input type="checkbox" id="cat-fil-stale" onchange="_setCatalogStale(this.checked)"
             ${_catalogState.filters.stale_only ? 'checked' : ''}
             style="width:16px;height:16px;cursor:pointer;">
      ${t('stale_only')}
    </label>
    <input id="cat-fil-q" type="text" placeholder="${CYNOVELA_LANG==='en'?'🔍 Search...':'🔍 名前/オーナー検索...'}"
           value="${escapeHtml(_catalogState.filters.q || '')}"
           oninput="onCatalogFilterChange()"
           style="flex:1;min-width:180px;padding:7px 12px;border:1px solid #e2e8f0;border-radius:6px;font-size:17px;">
    <button class="btn btn-sm" onclick="resetCatalogFilters()" style="padding:7px 14px;font-size:16px;">${bi('Reset', 'リセット')}</button>
    <!-- sweep-fix-gen-catalog-scope-honesty-20260711: 絞り込みは取得済みの現在ページ内のみに
         適用される(感度カウントはグローバル総数)。この非対称を明記して誤解を防ぐ。 -->
    <div style="width:100%;font-size:14px;color:#94a3b8;margin-top:2px;">${lj('Filters apply to the current page','絞り込みは現在のページ内に適用されます')}</div>`;
}

function onCatalogFilterClear(key, btn) {
  const wrap = btn.closest('.cat-filter-pop');
  if (!wrap) return;
  wrap.querySelectorAll('input[type="checkbox"][data-key]').forEach(cb => {
    cb.checked = false;
  });
  _catalogState.filters[key] = new Set();
  _updateCatalogTriggerLabel(wrap, 0);
  renderCatalogTable();
  const menu = wrap.querySelector('.cat-filter-menu');
  if (menu) menu.style.display = 'none';
}

function _updateCatalogTriggerLabel(wrap, count) {
  const labelEl = wrap.querySelector('.cat-filter-trigger-label');
  if (!labelEl) return;
  labelEl.textContent = count === 0 ? t('all_option') : `${count} ${t('items_selected')}`;
  labelEl.style.color = count > 0 ? '#0369a1' : '#94a3b8';
}
