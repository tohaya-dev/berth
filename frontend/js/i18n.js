// i18n.js - Cynovela v13

function t(key, vars = {}) {
  const str = (I18N[CYNOVELA_LANG] && I18N[CYNOVELA_LANG][key])
            || (I18N['en'] && I18N['en'][key])
            || key;
  return String(str).replace(/\$\{(\w+)\}/g, (_, k) => (vars[k] != null ? vars[k] : ''));
}

function lj(en, ja) {
  return (typeof CYNOVELA_LANG !== 'undefined' && CYNOVELA_LANG === 'ja') ? ja : en;
}

// finalround i18n: <option> テキストや title/placeholder 属性は <span class="en/ja"> を入れられないため、
//   data-en/data-ja(テキスト)・data-title-en/data-title-ja(title)・data-ph-en/data-ph-ja(placeholder)
//   を持つ要素を現在言語へ差替える汎用パス。setLang と起動時(applyInitialLang)から呼ぶ。
//   値(value属性)は不変＝表示テキストのみ差替えるため既存ロジックに非影響。
function _applyDataI18n() {
  if (typeof document === 'undefined') return;
  const en = (typeof CYNOVELA_LANG !== 'undefined' && CYNOVELA_LANG === 'en');
  try {
    document.querySelectorAll('[data-en][data-ja]').forEach(function (el) {
      el.textContent = en ? el.getAttribute('data-en') : el.getAttribute('data-ja');
    });
    document.querySelectorAll('[data-title-en][data-title-ja]').forEach(function (el) {
      el.setAttribute('title', en ? el.getAttribute('data-title-en') : el.getAttribute('data-title-ja'));
    });
    document.querySelectorAll('[data-ph-en][data-ph-ja]').forEach(function (el) {
      el.setAttribute('placeholder', en ? el.getAttribute('data-ph-en') : el.getAttribute('data-ph-ja'));
    });
  } catch (_) { /* ignore */ }
}

function setLang(lang) {
  if (lang !== 'ja' && lang !== 'en') lang = 'en';
  CYNOVELA_LANG = lang;
  try { localStorage.setItem('cynovela_lang', lang); } catch (_) { /* ignore */ }
  if (lang === 'ja') {
    document.body.classList.add('lang-ja');
    document.body.classList.remove('lang-en');
  } else {
    document.body.classList.add('lang-en');
    document.body.classList.remove('lang-ja');
  }
  // active-lang のハイライト更新
  const btnEn = document.getElementById('btn-lang-en');
  const btnJa = document.getElementById('btn-lang-ja');
  if (btnEn) btnEn.classList.toggle('active-lang', lang === 'en');
  if (btnJa) btnJa.classList.toggle('active-lang', lang === 'ja');
  document.documentElement.setAttribute('lang', lang);
  // finalround: <option>/title/placeholder の data-*-en/ja を現在言語へ差替（span 不可な箇所の英訳）
  _applyDataI18n();

  // ★ 言語切替後に現在のページを再描画 (t() の結果やテンプレート埋込文字列を反映)
  const _renderers = {
    overview:   typeof renderOverview     !== 'undefined' ? renderOverview     : null,
    sources:    typeof renderSources      !== 'undefined' ? renderSources      : null,
    workspaces: typeof renderWorkspaces   !== 'undefined' ? renderWorkspaces   : null,
    guardrails: typeof renderGuardrails   !== 'undefined' ? renderGuardrails   : null,
    collections:typeof renderCollections  !== 'undefined' ? renderCollections  : null,
    catalog:    typeof renderDataCatalog  !== 'undefined' ? renderDataCatalog  : null,
    chat:       typeof renderChat         !== 'undefined' ? renderChat         : null,
    settings:   typeof showAppSettings    !== 'undefined' ? showAppSettings    : null,
  };
  if (typeof State !== 'undefined' && State.currentPage && _renderers[State.currentPage]) {
    try { _renderers[State.currentPage](); } catch (e) { /* ignore render errors on lang switch */ }
  }
}

async function archiveItem(type, id) {
  if (!type || !id) return;
  const path = _archivePath(type, id);
  if (!path) return;
  const msg = (typeof CYNOVELA_LANG !== 'undefined' && CYNOVELA_LANG === 'ja')
    ? 'アーカイブしますか？後で復元できます。'
    : 'Archive this item? You can restore it later.';
  if (!confirm(msg)) return;
  try {
    await API.patch(path, {});
    if (typeof showToast === 'function') {
      showToast(
        (CYNOVELA_LANG === 'ja') ? 'アーカイブしました' : 'Archived successfully',
        'success'
      );
    }
    if (typeof refreshAllData === 'function') await refreshAllData();
    if (type === 'workspace' && typeof renderWorkspaces === 'function') renderWorkspaces();
    if (type === 'collection' && typeof renderCollections === 'function') renderCollections();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(lj('Archive failed: ', 'アーカイブ失敗: ') + (e && e.message || 'unknown'), 'error');
    }
  }
}

function getHelpText(key) {
  const v = HELP_TEXTS[key];
  if (!v) return '';
  if (typeof v === 'string') return v;
  return v[CYNOVELA_LANG] || v.ja || v.en || '';
}

function _auditMetaLabel(meta) {
  return (CYNOVELA_LANG === 'en') ? (meta.en || meta.label || '') : (meta.ja || meta.label || '');
}

function _colIsPublished(col) {
  return (col.chunk_count || 0) > 0 || !!col.last_published_at || col.status === 'ready';
}

function _colPublishedAt(col) {
  if (!col.last_published_at) return '';
  try { return new Date(col.last_published_at).toLocaleString('ja-JP'); } catch { return col.last_published_at; }
}

function _restoreChatTemplatesState() {
  let collapsed = true; // デフォルト: 折りたたみ
  try {
    const saved = sessionStorage.getItem('cynovela_chat_templates_collapsed');
    if (saved === '0') collapsed = false;
  } catch {}
  const panel = document.querySelector('.chat-templates');
  const btn = document.querySelector('.chat-templates-toggle');
  const caret = btn?.querySelector('.caret');
  if (panel) panel.classList.toggle('collapsed', collapsed);
  if (caret) caret.textContent = collapsed ? '▶' : '▼';
}

function onChatWSChange() {
  const wsId = $('chat-ws-sel').value;
  // BETA: 最近使った WS を localStorage に記録
  if (wsId) {
    try {
      let recent = JSON.parse(localStorage.getItem('cynovela_recent_ws') || '[]');
      recent = [wsId, ...recent.filter(id => id !== wsId)].slice(0, 5);
      localStorage.setItem('cynovela_recent_ws', JSON.stringify(recent));
    } catch {}
  }
  const ws = State.workspaces.find(w => w.id === wsId);
  // PHASE UI-7: WS 切替時にアクティブタブの wsId/name を更新
  if (typeof State !== 'undefined' && ws) State.currentWs = ws;
  if (typeof _updateActiveTabFromState === 'function') _updateActiveTabFromState();
  // PIIモード表示バッジ（読み取り専用）
  if (typeof _refreshPiiModeBadge === 'function') _refreshPiiModeBadge();
  const banner = $('chat-guardrail-banner');
  const badge = $('chat-policy-badge');
  const polIds = ws ? (ws.guardrail_policy_ids || []) : [];
  const pols = polIds.map(pid => State.policies.find(p => p.id === pid)).filter(Boolean);
  if (pols.length > 0) {
    // #C: inline バッジとして表示 (モデル行に同居)
    banner.style.display = 'inline-flex';
    banner.textContent = `🔒 ${pols.map(p => p.name).join(' + ')} ${lj('applied', '適用中')}`;
    badge.innerHTML = pols.map(p => `<span class="tag tag-blue">🛡️ ${p.name}</span>`).join(' ');
    return;
  }
  banner.style.display = 'none';
  badge.innerHTML = '';
}

// 個人情報マスキングモードの読み取り専用バッジ表示（書込みなし・表示のみ）
async function _refreshPiiModeBadge() {
  const el = $('chat-pii-mode-badge');
  if (!el) return;
  try {
    const data = await API.get('/api/settings/pii-mode');
    const mode = (data && data.mode) || 'standard';
    const labelMap = { strict: lj('🔐 PII: Strict', '🔐 個人情報: 厳格'), standard: lj('🔓 PII: Standard', '🔓 個人情報: 標準'), lenient: lj('🔓 PII: Lenient', '🔓 個人情報: 緩和') };
    el.textContent = labelMap[mode] || (lj('🔓 PII: ', '🔓 個人情報: ') + mode);
    el.style.display = 'inline-flex';
    el.style.marginLeft = '6px';
    el.style.padding = '2px 8px';
    el.style.borderRadius = '4px';
    el.style.fontSize = '12px';
    el.style.background = mode === 'strict' ? '#fef2f2' : '#f8fafc';
    el.style.color = mode === 'strict' ? '#991b1b' : '#475569';
    el.style.border = '1px solid #e2e8f0';
  } catch {
    el.style.display = 'none';
  }
}
window._refreshPiiModeBadge = _refreshPiiModeBadge;

async function applyVsSettings() {
  const body = {
    provider: $('vs-provider').value,
    qdrant_url: $('vs-qdrant-url').value.trim(),
  };
  const result = $('vs-result');
  try {
    const res = await API.post('/api/settings/vector-store', body);
    result.textContent = `${lj('✅ Applied', '✅ 適用完了')} (${res.provider})`;
    result.className = 'set-result success';
    if (res.warning) showToast(res.warning, 'warning');
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

// alpha §段4: useTplItem 撤去 (質問テンプレ自体を撤去したため不要)

function downloadCodeBlock(btn, lang) {
  const code = btn.closest('pre').querySelector('code').innerText;
  const ext = ({
    python: 'py', javascript: 'js', typescript: 'ts', html: 'html',
    bash: 'sh', shell: 'sh', json: 'json', yaml: 'yml', yml: 'yml', sql: 'sql',
  })[lang] || 'txt';
  const blob = new Blob([code], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `cynovela_export.${ext}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function doLogin(_userId) {
  // 2026-05-23 sec4 v4.1 項目①: ワンクリック入室は完全撤去済。残置 onclick 等から
  // 万一呼ばれても無入室に倒し、ユーザー名+パスワードフォームへ誘導する。
  const msg = (CYNOVELA_LANG === 'ja')
    ? 'ワンクリック入室は廃止されました。ユーザー名・パスワードでログインしてください。'
    : 'One-click login has been removed. Please sign in with username and password.';
  if (typeof showToast === 'function') showToast(msg, 'error');
}

function _resetSessionStats() {
  _sessionStats.used_tokens = 0;
  _sessionStats.prompt_total = 0;
  _sessionStats.completion_total = 0;
  _sessionStats.total_total = 0;
  _sessionStats.queries = 0;
  _sessionStats.zero_hits = 0;
  _sessionStats.speeds = [];
  _sessionStats.llm_times_ms = [];
  _sessionStats.modes = { basic: 0, agentic: 0 };
  _sessionStats.last = null;
}

function _getAppSettings() {
  try {
    return {
      max_turns: parseInt(localStorage.getItem('cynovela_max_turns') || '5', 10),
      default_rag_mode: localStorage.getItem('rag_display_mode') || 'normal',
    };
  } catch { return { ..._appSettingsDefault }; }
}

function saveChatTabs(tabs, activeId) {
  // DD-CYN-0095 §3-A: 利用者ごとのキーへ保存する (state.js の _chatTabStorageKey)
  localStorage.setItem(_chatTabStorageKey(_CHAT_TAB_KEY), JSON.stringify(tabs));
  if (activeId) localStorage.setItem(_chatTabStorageKey('cynovela_chat_active_tab'), activeId);
}

function _getActiveTabId() {
  return localStorage.getItem(_chatTabStorageKey('cynovela_chat_active_tab'));
}

function copyReport() {
  if (!_currentReportContent) return;
  navigator.clipboard.writeText(_currentReportContent).then(() => {
    showToast(CYNOVELA_LANG === 'ja' ? 'コピーしました' : 'Copied', 'success');
  }).catch(() => { /* */ });
}

function _isAlertDismissed(code) {
  try { return sessionStorage.getItem('alert_dismissed_' + code) === '1'; }
  catch (_) { return false; }
}

function dismissAlert(code, el) {
  try { sessionStorage.setItem('alert_dismissed_' + code, '1'); } catch (_) { /* */ }
  if (el && el.parentNode) el.parentNode.removeChild(el);
}

async function renderQueryTrend() {
  const host = document.getElementById('query-trend-host');
  if (!host) return;
  try {
    const r = await API.get('/api/audit-logs?action=chat_query&limit=200');
    const logs = r?.items || r?.logs || [];
    const daily = {};
    logs.forEach(l => {
      const d = (l.timestamp || l.created_at || '').slice(0, 10);
      if (d) daily[d] = (daily[d] || 0) + 1;
    });
    const days = [];
    for (let i = 6; i >= 0; i--) {
      const dt = new Date();
      dt.setDate(dt.getDate() - i);
      const key = dt.toISOString().slice(0, 10);
      const label = i === 0 ? lj('Today', '今日') : `${dt.getMonth()+1}/${dt.getDate()}`;
      days.push({ key, label, count: daily[key] || 0 });
    }
    const maxQ = Math.max(...days.map(d => d.count), 1);
    host.innerHTML = `
      <div style="font-size:14px;font-weight:500;color:#111827;margin-bottom:2px;">${bi('Query trend (7 days)', 'クエリトレンド（7日）')}</div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:14px;">${bi('Blue = queries / Red = zero-hits', '青 = クエリ数 / 赤 = ゼロヒット')}</div>
      <div style="display:flex;align-items:flex-end;gap:5px;height:88px;margin-bottom:8px;">
        ${days.map(d => {
          const h = Math.max(Math.round(d.count / maxQ * 88), d.count > 0 ? 6 : 3);
          return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;">
            <div style="width:100%;height:${h}px;background:#3b82f6;border-radius:3px 3px 0 0;opacity:.85;" title="${d.key}: ${d.count}${lj(' queries', 'クエリ')}"></div>
          </div>`;
        }).join('')}
      </div>
      <div style="display:flex;justify-content:space-between;font-size:11px;font-weight:500;color:#6b7280;">
        ${days.map(d => `<span>${d.label}</span>`).join('')}
      </div>`;
  } catch(e) {
    host.innerHTML = '<div style="font-size:12px;color:#9ca3af;padding:8px 0;">' + lj('Failed to load query trend', 'クエリトレンド取得失敗') + '</div>';
  }
}

function _auditIcon(action) {
  const icons = {
    'PUBLISH': '📤', 'collection_published': '📤',
    'RAG_QUERY': '🔍', 'chat_query': '🔍',
    'GUARDRAIL_TRIGGERED': '🛡️',
    'PROMPT_INJECTION_BLOCKED': '🚫',
    'LOW_CONFIDENCE_FALLBACK': '⚠️',
    'RBAC_VIOLATION': '🔒',
    'USER_LOGIN': '👤', 'USER_LOGOUT': '👋',
    'COLLECTION_CREATED': '📁', 'collection_created': '📁',
    'COLLECTION_DELETED': '🗑', 'collection_deleted': '🗑',
    'DOCUMENT_UPLOADED': '📄',
    'session_created': '💬',
  };
  return icons[action] || '📋';
}
