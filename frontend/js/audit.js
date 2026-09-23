// audit.js - Cynovela v13

function _toastViewLogs(linkEl) {
  try { linkEl.closest('.toast')?.remove(); } catch (_) {}
  // F2-1: 監査ログは「管理 > 監査ログ」ページに移動済み
  navigate('audit');
}

async function renderAudit() {
  // #audit-filter-bar / #audit-table / #audit-pager は #page-audit に移管済み
  if (typeof loadAuditLogsEnhanced === 'function') {
    try { await loadAuditLogsEnhanced(); } catch (e) { console.warn('loadAuditLogsEnhanced failed', e); }
  }
  // CSV出力ボタンは admin のみ表示
  const btn = document.getElementById('audit-export-btn');
  if (btn) {
    btn.style.display = (State.user && State.user.role === 'admin') ? '' : 'none';
  }
}

function _renderColoredLogItem(l) {
  const meta = _LOG_ACTION_LABELS[l.action]
            || { color: '#64748b', emoji: '📋', en: l.action || '', ja: l.action || '' };
  // C-9: timestamp_local (日本時間 +09:00 の ISO8601) があればそちらを表示。無ければ従来の UTC 生値。
  const time = ((l.timestamp_local || l.timestamp || '').replace('T', ' ')).slice(5, 16);
  const detail = l.detail ? escapeHtml(l.detail) : '';
  const target = l.target ? escapeHtml(l.target) : '';
  const label = (CYNOVELA_LANG === 'en') ? meta.en : meta.ja;
  return `<div class="log-item" style="border-left:3px solid ${meta.color};padding-left:8px;margin-bottom:6px;">
    <span style="font-size:16px;color:#94a3b8;">${escapeHtml(time)}</span>
    <span style="margin-left:6px;">${meta.emoji}</span>
    <span style="font-weight:600;color:${meta.color};margin-left:4px;">${escapeHtml(label)}</span>
    ${target ? `<span style="color:#475569;margin-left:6px;">— ${target}</span>` : ''}
    ${detail ? `<span style="color:#94a3b8;margin-left:4px;font-size:16px;">(${detail})</span>` : ''}
  </div>`;
}

function _auditMeta(action) {
  return AUDIT_ACTION_META[action] || {icon:'📋', en: action, ja: action, cat:'other'};
}

// audit-log-readability 2026-06-25: detail 列の生JSON（{"detail":"..."} や二重エンコード）を
// 1行で読める要約テキストへ整える。監査記録（DB）は一切変更せず、表示の見せ方だけ整える。
function _auditDetailText(raw) {
  if (raw === null || raw === undefined || raw === '') return '—';
  let v = raw;
  // 最大2段の JSON ラッパを剥がす（{"detail":"..."} / chat_retrieved の二重エンコード対策）
  for (let i = 0; i < 2; i++) {
    if (typeof v !== 'string') break;
    const s = v.trim();
    if (!(s.startsWith('{') || s.startsWith('['))) break;
    try { v = JSON.parse(s); } catch (_) { break; }
  }
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const parts = [];
    if (v.detail !== undefined && v.detail !== null && v.detail !== '') {
      const dv = v.detail;
      if (typeof dv === 'string' && (dv.trim().startsWith('{') || dv.trim().startsWith('['))) {
        parts.push(_auditDetailText(dv)); // ネストした JSON 文字列はもう一段整える
      } else {
        parts.push(typeof dv === 'string' ? dv : String(dv));
      }
    }
    if (v.tier) parts.push((CYNOVELA_LANG === 'ja' ? '保管庫=' : 'tier=') + v.tier);
    if (Array.isArray(v.document_ids)) {
      parts.push(CYNOVELA_LANG === 'ja' ? ('出典' + v.document_ids.length + '件')
                                       : ('docs ' + v.document_ids.length));
    }
    if (parts.length) return parts.join(' · ');
    // detail/tier/document_ids 以外のオブジェクトは key=value で簡潔に
    return Object.entries(v)
      .map(([k, val]) => k + '=' + (typeof val === 'object' ? JSON.stringify(val) : val))
      .join(' · ');
  }
  return String(v);
}

async function loadAuditLogsEnhanced() {
  const filterHost = document.getElementById('audit-filter-bar');
  if (filterHost && !filterHost.dataset.inited) {
    filterHost.dataset.inited = '1';
    filterHost.innerHTML = `
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center;">
        <input id="audit-search" type="text" placeholder="${lj('🔍 Search (target/detail)...', '🔍 検索（対象/詳細）...')}"
               oninput="onAuditSearch(this.value)"
               style="flex:1;min-width:200px;padding:8px 12px;border:1px solid #e2e8f0;
                      border-radius:6px;font-size:18px;">
        <select id="audit-category" onchange="onAuditCategory(this.value)"
                style="padding:8px 12px;border:1px solid #e2e8f0;border-radius:6px;font-size:18px;">
          <option value="">${lj('All', 'すべて')}</option>
          <option value="chat">${lj('💬 Chat','💬 チャット')}</option>
          <option value="publish">🚀 Publish</option>
          <option value="source">📁 Source</option>
          <option value="workspace">📂 Workspace</option>
          <option value="sync">${lj('🔄 Auto-sync','🔄 自動同期')}</option>
          <option value="user">${lj('👤 User','👤 ユーザー')}</option>
          <option value="backup">${lj('💾 Backup','💾 バックアップ')}</option>
          <option value="session">${lj('🗨️ Session','🗨️ セッション')}</option>
          <option value="feedback">${lj('👍 Feedback','👍 フィードバック')}</option>
          <option value="security">${lj('🛡️ Security','🛡️ セキュリティ')}</option>
          <option value="other">${lj('🔑 Login / Other','🔑 ログイン / その他')}</option>
        </select>
        <button class="btn btn-sm" onclick="resetAuditFilter()"
                style="padding:8px 14px;font-size:17px;">
          ${lj('Reset','リセット')}
        </button>
      </div>`;
  }
  // BETA-pagination: サーバーサイドフィルタへ移行
  const p = State.auditPager;
  const qs = new URLSearchParams({ limit: p.limit, offset: (p.page - 1) * p.limit });
  if (p.q) qs.set('q', p.q);
  if (p.category) qs.set('category', p.category);
  State._auditLoadError = '';
  try {
    const res = await API.get(`/api/audit-logs?${qs}`);
    if (res && res.items !== undefined) {
      _allAuditLogs = res.items;
      State.auditPager.total = res.total;
    } else {
      _allAuditLogs = Array.isArray(res) ? res : [];
    }
  } catch (e) {
    // DD-CYN-0089 §6-B: 読めなかったときに黙って空にすると、画面は「該当ログなし」と
    //   出す。記録が無いのか読めなかったのかを受け取り手が区別できない。
    _allAuditLogs = [];
    State._auditLoadError = (e && e.message) || '';
    showToast(lj(`Could not read the audit log: ${State._auditLoadError}`,
      `監査ログを読めませんでした: ${State._auditLoadError}`), 'error');
  }
  _pagerCallbacks['audit'] = {
    page: (n) => { State.auditPager.page = Math.max(1, n); loadAuditLogsEnhanced(); },
    limit: (n) => { State.auditPager.limit = n; State.auditPager.page = 1; loadAuditLogsEnhanced(); },
  };
  renderAuditTable();
  const aPager = document.getElementById('audit-pager');
  if (aPager) {
    aPager.innerHTML = _renderPager({
      key: 'audit',
      page: State.auditPager.page,
      limit: State.auditPager.limit,
      total: State.auditPager.total,
    });
  }
}

function renderAuditTable() {
  const thead = document.querySelector('#audit-table thead');
  const tbody = document.querySelector('#audit-table tbody');
  if (!thead || !tbody) return;
  thead.innerHTML = `<tr><th style="width:40px;"></th><th>${bi('Time','時刻')}</th><th>${bi('Action','アクション')}</th><th>${bi('Target','対象')}</th><th>${bi('Detail','詳細')}</th></tr>`;
  // BETA-pagination: サーバーサイドフィルタ済みなのでそのまま使う
  const filtered = _allAuditLogs;
  if (!filtered.length) {
    // DD-CYN-0089 §6-B: 読めなかったときは「該当ログなし」ではなく、読めなかったことを出す。
    if (State._auditLoadError) {
      tbody.innerHTML = `<tr><td colspan="5" style="padding:14px;text-align:center;color:#b91c1c;">${escapeHtml(lj(`Could not read the audit log: ${State._auditLoadError}`,`監査ログを読めませんでした: ${State._auditLoadError}`))}</td></tr>`;
      return;
    }
    tbody.innerHTML = `<tr><td colspan="5" style="padding:14px;text-align:center;color:#94a3b8;">${bi('No matching logs','該当ログなし')}</td></tr>`;
    return;
  }
  tbody.innerHTML = filtered.map(l => {
    const meta = _auditMeta(l.action);
    return `<tr>
      <td style="font-size:18px;text-align:center;">${meta.icon}</td>
      <td style="font-size:16px;color:#64748b;">${escapeHtml(((l.timestamp_local || l.timestamp || '').replace('T', ' ')).slice(0,16))}</td>
      <td><strong>${escapeHtml(_auditMetaLabel(meta))}</strong>
          <div style="font-size:16px;color:#94a3b8;">${escapeHtml(l.action || '')}</div></td>
      <td style="font-size:17px;">${escapeHtml(l.target || '—')}</td>
      <td style="font-size:17px;color:#475569;">
        <div style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
             title="${escapeHtml(_auditDetailText(l.detail))}">${escapeHtml(_auditDetailText(l.detail))}</div>
      </td>
    </tr>`;
  }).join('');
}

async function exportAuditCsv() {
  try {
    const url = `${API.base}/api/audit-logs/export`;
    const headers = {};
    if (API.token) headers['Authorization'] = `Bearer ${API.token}`;
    const res = await fetch(url, { headers });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        detail = (j && (j.detail && j.detail.message)) || detail;
      } catch (_) { /* */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    const fn = (res.headers.get('Content-Disposition') || '').match(/filename=([^;]+)/);
    a.download = fn ? fn[1].trim() : 'cynovela_audit.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    showToast(
      (CYNOVELA_LANG === 'ja') ? '監査ログ CSV をダウンロードしました' : 'Audit log CSV downloaded',
      'success'
    );
  } catch (e) {
    showToast(lj('Export failed: ', 'エクスポート失敗: ') + (e && e.message || 'unknown'), 'error');
  }
}

async function _renderGuardrailCard() {
  const host = document.getElementById('dashboard-guardrail-content');
  if (!host) return;
  try {
    const r = await API.get('/api/audit-logs?category=guardrail&limit=100');
    const events = r?.items || r?.events || r?.logs || [];
    if (events.length === 0) {
      host.innerHTML = `
        <div style="font-size:28px;font-weight:600;color:#111827;line-height:1.1;">0</div>
        <div style="font-size:11px;color:#9ca3af;margin-bottom:10px;">${lj('Triggers (total)','発動件数（累計）')}</div>
        <div style="font-size:11px;color:#166534;">${lj('✅ No triggers','✅ 発動なし')}</div>`;
      return;
    }
    const counts = {};
    events.forEach(e => {
      const t = e.action || e.reason || e.type || 'OTHER';
      counts[t] = (counts[t] || 0) + 1;
    });
    const total = events.length;
    const maxV = Math.max(...Object.values(counts), 1);
    const COLORS = {
      LOW_CONFIDENCE: '#ef4444', LOW_CONFIDENCE_FALLBACK: '#ef4444',
      PROMPT_INJECTION: '#f59e0b', PROMPT_INJECTION_BLOCKED: '#f59e0b',
      PII_DETECTED: '#8b5cf6', pii_detected: '#8b5cf6',
      GUARDRAIL_TRIGGERED: '#ef4444',
    };
    host.innerHTML = `
      <div style="font-size:28px;font-weight:600;color:#111827;line-height:1.1;">${total.toLocaleString()}</div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:10px;">${lj('Triggers (recent)','発動件数（直近）')}</div>
      ${Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,3).map(([k,v])=>{
        const label = String(k).replace(/_/g,' ').substring(0,12);
        return `<div style="display:flex;align-items:center;gap:5px;margin-bottom:4px;">
          <div style="width:72px;font-size:10px;color:#9ca3af;text-align:right;flex-shrink:0;">${escapeHtml(label)}</div>
          <div style="flex:1;background:#f8fafc;border-radius:2px;height:9px;overflow:hidden;">
            <div style="width:${Math.round(v/maxV*100)}%;height:100%;background:${COLORS[k]||'#94a3b8'};border-radius:2px;"></div>
          </div>
          <div style="width:28px;font-size:10px;color:#9ca3af;">${v}</div>
        </div>`;
      }).join('')}
    `;
  } catch(e) {
    host.innerHTML = '<div style="font-size:11px;color:#9ca3af;">' + lj('No trigger data','発動データなし') + '</div>';
  }
}

function renderAuditLogSummary(logs, container) {
  if (!container) return;
  container.innerHTML = '';
  if (!logs || !logs.length) {
    container.innerHTML =
      '<div class="audit-summary-empty">' +
      '<span class="en">No recent activity</span>' +
      '<span class="ja">アクティビティなし</span></div>';
    return;
  }
  logs.slice(0, 3).forEach(log => {
    const action = log.action || '';
    const label = ACTION_LABELS[action] || { en: action, ja: action };
    const timeAgo = formatTimeAgo(log.timestamp);
    const icon = _auditIcon(action);
    // user_id がない実装の audit_logs では target を表示する
    const subject = log.user_id || log.target || 'system';
    const row = document.createElement('div');
    row.className = 'audit-summary-row';
    row.innerHTML = `
      <span class="audit-icon">${icon}</span>
      <span class="audit-text">
        <strong>${escapeHtml(String(subject).slice(0, 40))}</strong>
        <span class="en"> — ${escapeHtml(label.en)}</span><span class="ja"> — ${escapeHtml(label.ja)}</span>
      </span>
      <span class="audit-time">${escapeHtml(timeAgo)}</span>`;
    container.appendChild(row);
  });
  // 監査ログへのリンク
  const linkRow = document.createElement('div');
  linkRow.style.cssText = 'text-align:center;margin-top:8px;';
  linkRow.innerHTML = `
    <a href="#" onclick="navigate('guardrails');return false;" style="font-size:14px;color:var(--accent);">
      <span class="en">📋 View all in audit log →</span><span class="ja">📋 監査ログで全件表示 →</span>
    </a>`;
  container.appendChild(linkRow);
}
