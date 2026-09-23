// workspace.js - Cynovela v13

function _archivePath(type, id) {
  if (type === 'workspace')   return `/api/workspaces/${id}/archive`;
  if (type === 'collection')  return `/api/collections/${id}/archive`;
  return null;
}

function _unarchivePath(type, id) {
  if (type === 'workspace')   return `/api/workspaces/${id}/unarchive`;
  if (type === 'collection')  return `/api/collections/${id}/unarchive`;
  return null;
}

async function toggleShowArchived(checked) {
  _showArchivedItems = !!checked;
  try {
    const q = _showArchivedItems ? '?include_archived=true' : '';
    const ws   = await API.get('/api/workspaces' + q);
    const cols = await API.get('/api/collections' + q);
    if (typeof State !== 'undefined') {
      if (Array.isArray(ws))   State.workspaces  = ws;
      if (Array.isArray(cols)) State.collections = cols;
    }
    if (typeof renderWorkspaces  === 'function') renderWorkspaces();
    if (typeof renderCollections === 'function') renderCollections();
  } catch (_) { /* */ }
}

function renderPollingStatusCard(s) {
  const host = document.getElementById('polling-status-card');
  if (!host) return;
  const ws = s.polling_workspaces || [];
  // GUI修正8 #05 5-5: WS別の同期状態リスト (3カラム grid)
  if (!ws.length) {
    // 全 WS を一覧表示し、自動同期OFFを明示
    const allWs = (State.workspaces || []).filter(w => !w.archived_at);
    if (!allWs.length) {
      host.innerHTML = `
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                    padding:14px 16px;margin-bottom:18px;">
          <div class="section-label" style="margin-bottom:6px;">🔄 ${t('auto_sync_section')}</div>
          <div style="font-size:17px;color:#64748b;">${t('no_workspace_yet')}</div>
        </div>`;
      return;
    }
    // P2 §4: 自動同期テーブル — インライン トグル + 緑ハイライト
    const rows = allWs.map(w => {
      const lastScan = (w.last_scan_date) ? new Date(w.last_scan_date).toLocaleString() : '—';
      const enabled = !!w.auto_sync;
      const cls = enabled ? 'sync-row sync-enabled' : 'sync-row';
      return `
        <tr class="${cls}" data-ws-id="${escapeHtml(w.id)}">
          <td style="padding:6px 12px;font-weight:600;color:#1e293b;">${escapeHtml(w.name||w.id)}</td>
          <td style="padding:6px 12px;font-size:17px;color:#64748b;">${escapeHtml(lastScan)}</td>
          <td style="padding:6px 12px;font-size:17px;">
            <label class="toggle-switch" title="${enabled ? 'Auto-sync ON' : 'Auto-sync OFF'}">
              <input type="checkbox" class="sync-toggle" ${enabled ? 'checked' : ''}
                     onchange="toggleAutoSync('${escapeHtml(w.id)}', ${enabled})">
              <span class="toggle-slider"></span>
            </label>
            <span style="margin-left:8px;color:${enabled ? '#15803d' : '#94a3b8'};">
              ${enabled
                ? '<span class="en">ON</span><span class="ja">ON</span>'
                : '<span class="en">OFF</span><span class="ja">OFF</span>'}
            </span>
          </td>
        </tr>`;
    }).join('');
    host.innerHTML = `
      <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
                  padding:14px 16px;margin-bottom:18px;">
        <div class="section-label" style="margin-bottom:8px;">🔄
          <span class="en">Auto-sync (polling)</span><span class="ja">自動同期（ポーリング）</span>
        </div>
        <table style="width:100%;background:#fff;border-radius:6px;border:1px solid #e2e8f0;border-collapse:collapse;">
          <thead>
            <tr style="background:#f1f5f9;">
              <th style="text-align:left;padding:6px 12px;font-size:16px;font-weight:700;color:#475569;letter-spacing:0.04em;">Workspace</th>
              <th style="text-align:left;padding:6px 12px;font-size:16px;font-weight:700;color:#475569;letter-spacing:0.04em;">
                <span class="en">Last scan</span><span class="ja">最終スキャン</span>
              </th>
              <th style="text-align:left;padding:6px 12px;font-size:16px;font-weight:700;color:#475569;letter-spacing:0.04em;">
                <span class="en">Auto-sync</span><span class="ja">自動同期</span>
              </th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        <div style="font-size:16px;color:#94a3b8;margin-top:6px;">
          <span class="en">Toggle to enable / disable polling for each workspace.</span><span class="ja">スイッチで各 WS のポーリングを ON/OFF できます。</span>
        </div>
      </div>`;
    return;
  }
  const fmtInterval = (sec) => {
    if (sec >= 86400) return `${Math.round(sec/86400)}${lj('d','日')}`;
    if (sec >= 3600)  return `${Math.round(sec/3600)}${lj('h','時間')}`;
    if (sec >= 60)    return `${Math.round(sec/60)}${lj('m','分')}`;
    return `${sec}${lj('s','秒')}`;
  };
  const rows = ws.map(w => {
    // 自動同期 ON 判定: interval_seconds > 0 = ポーリング有効。緑ハイライトで強調表示。
    const isOn = (Number(w.interval_seconds) || 0) > 0;
    const rowStyle = isOn
      ? 'background:#f0fdf4;border-left:3px solid #16a34a;'
      : '';
    return `
    <tr style="${rowStyle}">
      <td style="padding:8px 12px;font-weight:600;">${escapeHtml(w.name||w.workspace_id)}</td>
      <td style="padding:8px 12px;font-size:17px;color:#0369a1;">${fmtInterval(w.interval_seconds)}</td>
      <td style="padding:8px 12px;font-size:17px;">
        ${w.auto_publish ? `<span style="color:#15803d;">✅ ${lj('Auto Publish','自動Publish')}</span>` : `<span style="color:#94a3b8;">${lj('Manual','手動')}</span>`}
      </td>
      <td style="padding:8px 12px;font-size:16px;color:#64748b;">
        ${w.last_scan_at ? new Date(w.last_scan_at).toLocaleString(CYNOVELA_LANG==='ja'?'ja-JP':'en-US') : lj('Not run','未実行')}
      </td>
      <td style="padding:8px 12px;font-size:16px;color:#0369a1;">
        ${w.next_scan_at ? new Date(w.next_scan_at).toLocaleString(CYNOVELA_LANG==='ja'?'ja-JP':'en-US') : lj('At next loop','次回ループで判定')}
      </td>
    </tr>`;
  }).join('');
  host.innerHTML = `
    <div style="background:#fdf4ff;border:1px solid #e9d5ff;border-radius:10px;
                padding:14px 16px;margin-bottom:18px;">
      <div style="font-size:18px;font-weight:700;color:#7e22ce;margin-bottom:10px;">
        🔄 ${t('auto_sync_section')} — ${ws.length} Workspace
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:18px;">
        <thead>
          <tr style="background:rgba(255,255,255,0.5);">
            <th style="text-align:left;padding:8px 12px;font-size:16px;color:#7e22ce;">Workspace</th>
            <th style="text-align:left;padding:8px 12px;font-size:16px;color:#7e22ce;">${bi('Interval','間隔')}</th>
            <th style="text-align:left;padding:8px 12px;font-size:16px;color:#7e22ce;">Publish</th>
            <th style="text-align:left;padding:8px 12px;font-size:16px;color:#7e22ce;">${t('last_scan')}</th>
            <th style="text-align:left;padding:8px 12px;font-size:16px;color:#7e22ce;">${bi('Next Scan','次回スキャン')}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function renderGuardrailsCoverage() {
  const host = document.getElementById('guardrails-coverage');
  if (!host) return;
  let workspaces = [];
  try {
    const res = await API.get('/api/workspaces?limit=100');
    workspaces = (res && res.items !== undefined) ? res.items : (res || []);
  } catch (e) { workspaces = []; }
  if (!workspaces.length) {
    host.innerHTML = '<div style="padding:14px;color:#94a3b8;">' + bi('No workspaces', 'Workspace がありません') + '</div>';
    return;
  }
  const rows = workspaces.map(ws => {
    const policyNames = ws.policy_names || [];
    const protectedWs = policyNames.length > 0;
    const piiCount = ws.pii_count || 0;
    const bg = protectedWs ? '' : 'background:#fef2f2;';
    return `<tr style="border-bottom:1px solid #f0f0f0;${bg}">
      <td style="padding:10px 12px;font-weight:600;">🏢 ${escapeHtml(ws.name || ws.id)}</td>
      <td style="padding:10px 12px;font-size:13px;color:#475569;">
        ${policyNames.length ? policyNames.map(n => `<span class="tag tag-grey" style="margin-right:4px;">${escapeHtml(n)}</span>`).join('') : `<span style="color:#991b1b;font-weight:600;">${bi('Not set','未設定')}</span>`}
      </td>
      <td style="padding:10px 12px;text-align:right;font-size:13px;color:${piiCount>0?'#92400e':'#64748b'};">${piiCount}</td>
      <td style="padding:10px 12px;font-size:14px;">
        ${protectedWs ? `<span style="color:#15803d;font-weight:600;">✅ ${bi('Protected','保護中')}</span>` : `<span style="color:#991b1b;font-weight:600;">⚠️ ${bi('Unprotected','未保護')}</span>`}
      </td>
    </tr>`;
  }).join('');
  host.innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:14px;min-width:720px;">
      <thead><tr style="background:#f8fafc;">
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${bi('Workspace','WS名')}</th>
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${bi('Applied policy','適用ポリシー')}</th>
        <th style="text-align:right;padding:10px 12px;font-size:13px;color:#475569;">${bi('PII count','PII件数')}</th>
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${bi('Protection','保護状態')}</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderArchivedSection(data) {
  const sources     = data.sources || [];
  const workspaces  = data.workspaces || [];
  const collections = data.collections || [];
  const total = sources.length + workspaces.length + collections.length;
  if (total === 0) {
    return '<div style="padding:14px;color:#94a3b8;font-size:17px;">' + bi('No archived items.', 'アーカイブされたアイテムはありません。') + '</div>';
  }
  const rows = (kind, items) => items.map(it => {
    const at = it.archived_at ? new Date(it.archived_at).toLocaleString('ja-JP') : '';
    return `<tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:6px 10px;font-size:16px;color:#64748b;">${kind}</td>
      <td style="padding:6px 10px;font-weight:600;">${escapeHtml(it.name || it.id)}</td>
      <td style="padding:6px 10px;font-size:16px;color:#94a3b8;">${escapeHtml(at)}</td>
      <td style="padding:6px 10px;text-align:right;white-space:nowrap;">
        <button class="btn btn-sm" onclick="_restoreArchived('${kind}','${it.id}')"
                style="font-size:16px;padding:3px 10px;">♻️ ${bi('Restore','復元')}</button>
        <button class="btn btn-sm" onclick="_purgeArchived('${kind}','${it.id}')"
                style="font-size:16px;padding:3px 10px;background:#fff;border:1px solid #fecaca;color:#991b1b;margin-left:4px;">🗑 ${bi('Delete permanently','完全削除')}</button>
      </td>
    </tr>`;
  }).join('');
  return `
    <div style="font-size:16px;color:#94a3b8;margin-bottom:6px;">${bi('Total','合計')} ${total} ${bi('items','件')}</div>
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:6px;overflow-x:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:17px;">
        <thead><tr style="background:#f8fafc;">
          <th style="text-align:left;padding:6px 10px;font-size:16px;color:#475569;">${bi('Type','種別')}</th>
          <th style="text-align:left;padding:6px 10px;font-size:16px;color:#475569;">${bi('Name','名前')}</th>
          <th style="text-align:left;padding:6px 10px;font-size:16px;color:#475569;">${bi('Archived at','アーカイブ日時')}</th>
          <th></th>
        </tr></thead>
        <tbody>
          ${rows('source', sources)}
          ${rows('workspace', workspaces)}
          ${rows('collection', collections)}
        </tbody>
      </table>
    </div>`;
}

function deleteWorkspace(id) {
  // GUI修正2 #35: アーカイブ優先（即時削除はSettingsの「完全削除」のみ）
  confirmAction(lj('Archive Workspace','Workspaceをアーカイブ'),
    lj('This Workspace will be archived. It will be excluded from RAG search, but can be restored.\n\n(Choosing "Delete permanently" under "Archived" in Settings makes it unrecoverable.)','このWorkspaceをアーカイブします。RAG検索対象から除外されますが、復元できます。\n\n（Settingsの「アーカイブ済み」から「完全削除」を選ぶと復元不可になります）'),
    '🗄️', async () => {
    try {
      await API.post(`/api/archived/workspace/${id}/archive`, {});
      showToast(lj('Workspace archived','Workspaceをアーカイブしました'), 'success');
      await refreshAllData();
      renderWorkspaces();
    } catch (e) { showToast(lj(`Archive failed: ${e.message}`,`アーカイブ失敗: ${e.message}`), 'error'); }
  });
}

async function onColWSChange() {
  const wsId = $('new-col-ws')?.value;
  const container = $('col-file-checks');
  if (!wsId) { container.innerHTML = bi('Select a Workspace','Workspaceを選択してください'); return; }
  const ws = State.workspaces.find(w => w.id === wsId);
  if (!ws) return;

  let allFiles = [];
  for (const sid of (ws.source_ids||[])) {
    if (!State.allFiles[sid]) {
      try { State.allFiles[sid] = await API.get(`/api/sources/${sid}/files`); } catch { State.allFiles[sid] = []; }
    }
    allFiles = allFiles.concat(State.allFiles[sid]);
  }
  container.innerHTML = allFiles.map(f =>
    `<label class="check-item"><input type="checkbox" value="${f.id}" checked onchange="_updateFileCnt('col-file-checks')"> ${f.name} ${(f.categories||[]).map(catTag).join(' ')}</label>`
  ).join('') || `<div style="color:var(--text-4)">${bi('No files','ファイルなし')}</div>`;
  _updateFileCnt('col-file-checks');
  // S-5: 分類タブの集計も更新 (allFiles に classification 含まれているのでフロントで集計可)
  await _colLoadClassifyTab();
}

async function refreshAllData() {
  try {
    const [sources, workspaces, collections] = await Promise.all([
      API.get('/api/sources'), API.get('/api/workspaces'), API.get('/api/collections'),
    ]);
    State.sources = sources; State.workspaces = workspaces; State.collections = collections;
  } catch (e) { console.error('Data refresh failed:', e); }
  // Stage C (V3.5.0): RAGチャットの WS 一覧キャッシュを無効化し、Publish 完了後に
  // 公開済み WS が再選択肢として即出るようにする(renderChat が次回再取得)。
  State._selectableWS = null;
  // P2: viewer では /api/policies・/api/settings が 403 になるため個別に握り潰す
  try { State.policies = await API.get('/api/policies'); } catch (_e) { /* 403 silenced */ }
  try { State.settings = await API.get('/api/settings'); } catch (_e) { /* 403 silenced */ }
  // multi-ingest-roots-20260728: パス表示写像 (ingest.roots / ingest.host_path) の設定を初期化時に
  // 先読みし、初回描画の素通し (_syncSourceRows の非同期温め依存) を塞ぐ。失敗は '' 扱いで無害。
  try { if (typeof _loadIngestHostPath === 'function') await _loadIngestHostPath(); } catch (_e) { /* silenced */ }
  updateSidebarBadges();
}

async function loadWsSyncPanel(wsId) {
  const host = document.getElementById('ws-sync-panel');
  if (!host) return;
  if (!wsId) { host.innerHTML = '<div style="padding:14px;color:#94a3b8;">' + bi('No Workspace specified','Workspaceが指定されていません') + '</div>'; return; }
  host.innerHTML = '<div style="padding:14px;color:#94a3b8;">' + bi('Loading...', '読み込み中...') + '</div>';
  try {
    const cfg = await API.get(`/api/workspaces/${wsId}/sync-config`);
    host.innerHTML = renderWsSyncConfig(wsId, cfg);
  } catch (e) {
    host.innerHTML = `<div style="padding:14px;color:#ef4444;">${lj('Load failed','読み込み失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadChunks(workspaceId, filter) {
  const listEl = document.getElementById('chunk-list');
  if (!listEl || !workspaceId) return;
  _currentChunkFilter = filter || 'all';
  listEl.innerHTML = '<div style="padding:12px;color:#9ca3af;font-size:16px;">' + bi('Loading...', '読み込み中...') + '</div>';
  document.querySelectorAll('.chunk-filter-btn').forEach(btn => {
    const label = btn.textContent;
    btn.classList.toggle('active',
      (filter === 'all' && label.includes('全件')) ||
      (filter === 'pii' && label.includes('PII')) ||
      (filter === 'excluded' && label.includes('除外'))
    );
  });
  try {
    const cp = State.chunksPager;
    const qs = new URLSearchParams({
      filter, limit: cp.limit, offset: (cp.page - 1) * cp.limit,
    });
    const data = await API.get(`/api/workspaces/${workspaceId}/chunks?${qs}`);
    State.chunksPager.total = data.total;
    _pagerCallbacks['chunks'] = {
      page: (n) => { State.chunksPager.page = Math.max(1, n); loadChunks(workspaceId, filter); },
      limit: (n) => { State.chunksPager.limit = n; State.chunksPager.page = 1; loadChunks(workspaceId, filter); },
    };
    // P3-9: 統計サマリー (上部に表示)
    const sm = data.summary || {};
    const summaryHtml = `
      <div class="chunk-stats">
        <div class="chunk-stat"><div class="chunk-stat-num">${sm.total||0}</div><div class="chunk-stat-label">${bi('Total chunks','総チャンク')}</div></div>
        <div class="chunk-stat warn"><div class="chunk-stat-num">${sm.pii||0}</div><div class="chunk-stat-label">${bi('PII detected','PII検出')}</div></div>
        <div class="chunk-stat danger"><div class="chunk-stat-num">${sm.excluded||0}</div><div class="chunk-stat-label">${bi('RAG excluded','RAG除外')}</div></div>
        <div class="chunk-stat info"><div class="chunk-stat-num">${sm.acl_restricted||0}</div><div class="chunk-stat-label">${bi('ACL restricted','ACL制限')}</div></div>
      </div>
    `;
    if (!data.chunks.length) {
      listEl.innerHTML = summaryHtml + `<div style="padding:12px;color:#9ca3af;font-size:16px;">${
        filter === 'all' ? bi('No published chunks','Publishされたチャンクがありません') : bi('No chunks match this filter','このフィルターに一致するチャンクがありません')
      }</div>`;
      return;
    }
    listEl.innerHTML = summaryHtml + data.chunks.map((chunk, i) => {
      // ga-close-v3 PartX: chunks.pii_detected 列は使わない (raw 側は簡易正規表現の当たりでも
      //   1 になり伏字0件でも立ち、masked 側は伏字後の再判定なので普通 0 になる)。
      //   サーバと同じ判定 (pii_summary に1件以上あるか) に揃える。
      const _chunkHasPii = !!(chunk.pii_summary && typeof chunk.pii_summary === 'object'
        && Object.values(chunk.pii_summary).some(v => (parseInt(v, 10) || 0) > 0));
      const badge = chunk.excluded
        ? `<span class="chunk-badge excluded">🚫 ${bi('RAG excluded','RAG除外')}</span>`
        : _chunkHasPii
          ? '<span class="chunk-badge pii">🔒 PII</span>'
          : `<span class="chunk-badge normal">✅ ${bi('Normal','正常')}</span>`;
      const pageInfo = chunk.page_hint ? `p.${chunk.page_hint}` : 'p.-';
      const charInfo = chunk.char_count ? `${chunk.char_count}${lj(' chars','文字')}` : '';
      // P3-9: ACL バッジ
      const aclTxt = Array.isArray(chunk.allowed_roles) ? chunk.allowed_roles.join(', ') : 'all';
      const aclRestricted = Array.isArray(chunk.allowed_roles) && !chunk.allowed_roles.includes('viewer');
      const aclBadge = aclRestricted
        ? `<span class="chunk-badge acl">🔐 ${escapeHtml(aclTxt)}</span>`
        : `<span class="chunk-badge acl-open">ACL: ${escapeHtml(aclTxt)}</span>`;
      // 項目④: 検出種別×件数のバッジ列（値は持たない・種類と件数のみ）
      // ga-close-v3 PartX: 型ラベルは state.js の1か所 (piiTypeLabel) から取る。表に無い型も落とさない。
      let piiSummaryHtml = '';
      if (chunk.pii_summary && typeof chunk.pii_summary === 'object') {
        const parts = Object.keys(chunk.pii_summary).sort().map(k => {
          const lbl = piiTypeLabel(k);
          const cnt = chunk.pii_summary[k];
          return `<span class="chunk-badge pii-type" title="${lj('Detection count (values not retained)','検出件数（値は保持しません）')}" style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;">${escapeHtml(lbl)} ×${cnt}</span>`;
        });
        if (parts.length) piiSummaryHtml = parts.join(' ');
      }
      return `<div class="chunk-item">
        <div class="chunk-item-header">
          <span style="font-weight:600;font-size:16px;">#${i + 1}</span>
          <span style="color:#6b7280;font-size:16px;">${escapeHtml(chunk.source_doc)}</span>
          <span style="color:#9ca3af;font-size:16px;">${pageInfo}</span>
          <span style="color:#9ca3af;font-size:16px;">${charInfo}</span>
          ${badge}
          ${aclBadge}
          ${piiSummaryHtml}
        </div>
        <div class="chunk-preview">${escapeHtml(chunk.preview)}${chunk.preview.length >= 100 ? '…' : ''}</div>
      </div>`;
    }).join('');
  } catch (e) {
    listEl.innerHTML = `<div style="padding:12px;color:#ef4444;font-size:16px;">${lj('Load error','読み込みエラー')}: ${escapeHtml(e.message)}</div>`;
  }
}

function filterChunks(filter) { loadChunks(getCurrentWorkspaceId(), filter); }


async function loadPublishHistory(workspaceId) {
  const listEl = document.getElementById('publish-history-list');
  if (!listEl || !workspaceId) return;
  listEl.innerHTML = '<div style="padding:12px;color:#9ca3af;font-size:16px;">' + bi('Loading...', '読み込み中...') + '</div>';
  try {
    const data = await API.get(`/api/workspaces/${workspaceId}/publish-history?limit=10`);
    if (!data.history.length) {
      listEl.innerHTML = '<div style="padding:12px;color:#9ca3af;font-size:16px;">' + bi('No publish history','Publish履歴がありません') + '</div>';
      return;
    }
    listEl.innerHTML = data.history.map(h => {
      const dt = new Date(h.timestamp).toLocaleString('ja-JP');
      return `<div style="border:1px solid #e5e7eb;border-radius:6px;padding:10px 14px;margin-bottom:6px;font-size:16px;">
        <div style="font-weight:600;color:#374151;margin-bottom:6px;">${escapeHtml(dt)}</div>
        <div style="display:flex;gap:16px;flex-wrap:wrap;color:#6b7280;">
          <span>📄 ${h.doc_count}${bi(' files','ファイル')}</span>
          <span>🧩 ${h.chunk_count}${bi(' chunks','チャンク')}${bi(` (avg ${h.avg_chunk_chars} chars)`,`（平均 ${h.avg_chunk_chars}文字）`)}</span>
          ${h.pii_count > 0 ? `<span>🔒 ${bi('PII masked','PII置換')} ${h.pii_count}</span>` : ''}
          ${h.excluded_count > 0 ? `<span>🚫 ${bi('Excluded','除外')} ${h.excluded_count}</span>` : ''}
          <span>⏱️ ${h.elapsed_seconds}${bi('s','秒')}</span>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    listEl.innerHTML = `<div style="padding:12px;color:#ef4444;font-size:16px;">${lj('Load error','読み込みエラー')}: ${escapeHtml(e.message)}</div>`;
  }
}

async function fetchLatestPipelineResult(workspaceId) {
  try {
    let data;
    try {
      data = await API.get(`/api/workspaces/${workspaceId}/publish-history?limit=1`);
    } catch (_e) { return null; }
    const h = (data.history || [])[0];
    if (!h) return null;
    return {
      workspace_id: workspaceId,
      doc_count: h.doc_count,
      chunk_count: h.chunk_count,
      avg_chunk_chars: h.avg_chunk_chars,
      pii_count: h.pii_count,
      excluded_count: h.excluded_count,
      elapsed_seconds: h.elapsed_seconds,
      summary_lines: [
        lj(`📄 Processed ${h.doc_count} files`,`📄 ${h.doc_count}ファイル処理`),
        lj(`🧩 Created ${h.chunk_count} chunks (avg ${Math.round(h.avg_chunk_chars)} chars/chunk)`,`🧩 ${h.chunk_count}チャンク作成（平均 ${Math.round(h.avg_chunk_chars)}文字/チャンク）`),
        ...(h.pii_count > 0 ? [lj(`🔒 ${h.pii_count} chunks: PII masked then vectorized`,`🔒 ${h.pii_count}チャンク：PII置換してベクター化`)] : []),
        ...(h.excluded_count > 0 ? [lj(`🚫 ${h.excluded_count} chunks: excluded from RAG`,`🚫 ${h.excluded_count}チャンク：RAG対象から除外`)] : []),
        lj(`⏱️ ${h.elapsed_seconds}s`,`⏱️ ${h.elapsed_seconds}秒`),
      ],
    };
  } catch { return null; }
}

function showEditWorkspace(wsId, currentName, currentDesc) {
  const html = `
    <div class="form-group">
      <label class="form-label">${bi('Name','名前')}</label>
      <input id="edit-ws-name" type="text" class="form-input" value="${escapeHtml(currentName||'')}">
    </div>
    <div class="form-group">
      <label class="form-label">${bi('Description','説明')}</label>
      <textarea id="edit-ws-desc" class="form-input" style="min-height:80px;resize:vertical;">${escapeHtml(currentDesc||'')}</textarea>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:10px;margin-top:10px;">
      <button class="btn btn-sm" onclick="closeP3Modal()">${bi('Cancel', 'キャンセル')}</button>
      <button class="btn btn-sm btn-primary" onclick="saveWorkspace('${wsId}')">${bi('Save', '保存')}</button>
    </div>
  `;
  showP3Modal(lj('✏️ Edit Workspace','✏️ ワークスペースを編集'), html);
}

async function saveWorkspace(wsId) {
  const nameEl = document.getElementById('edit-ws-name');
  const descEl = document.getElementById('edit-ws-desc');
  const name = (nameEl?.value || '').trim();
  const desc = descEl?.value ?? '';
  if (!name) { showToast(lj('Please enter a name','名前を入力してください'), 'warning'); return; }
  try {
    await API.patch('/api/workspaces/' + wsId, { name, description: desc });
    closeP3Modal();
    showToast(lj('Workspace updated','ワークスペースを更新しました'), 'success');
    if (typeof renderWorkspaces === 'function') renderWorkspaces();
  } catch (e) {
    showToast(lj(`Update failed: ${e.message}`,`更新失敗: ${e.message}`), 'error');
  }
}

async function loadFollowupChips(answer, workspaceId, previews) {
  if (!answer || answer.length < 20) return '';
  try {
    // provider3way-suggestq-20260629: 取得済みチャンクのプレビューを渡し、コーパスに根拠が
    //   無い候補(空振り)をサーバ側で落とす。未指定なら従来どおりフィルタ無し(後方互換)。
    const _previews = Array.isArray(previews) ? previews.filter(p => p && String(p).trim()) : [];
    const r = await API.post('/api/chat/followups', { answer, workspace_id: workspaceId || '', previews: _previews });
    const list = (r && r.followups) || [];
    if (!list.length) return '';
    const chips = list.map(q => {
      // v3.5.0 Stage1 (B4②): pass via data attribute to avoid JSON double-escaping in the
      // onclick attribute (old JSON.stringify+escapeHtml broke JSON.parse on quotes/CJK).
      return `<button class="followup-chip" data-q="${escapeHtml(q)}" onclick="useFollowup(this.dataset.q)">${escapeHtml(q)}</button>`;
    }).join('');
    return `<div class="followup-chips">${chips}</div>`;
  } catch { return ''; }
}

function switchChatTab(tabId) {
  const tabs = loadChatTabs();
  const tab = tabs.find(t => t.id === tabId);
  if (!tab) return;
  saveChatTabs(tabs, tabId);
  renderChatTabs();
  // Workspace と session を切替
  if (typeof State !== 'undefined') {
    State.sessionId = tab.sessionId || null;
    if (tab.wsId) {
      const sel = document.getElementById('chat-ws-sel');
      if (sel) sel.value = tab.wsId;
      if (typeof onChatWSChange === 'function') onChatWSChange();
    }
  }
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.innerHTML = '';
}

// ===== Stage 3: DOMContentLoaded blocks moved from FIX app.js =====

// --- Block #1 (FIX app.js L471-L473) ---
document.addEventListener('DOMContentLoaded', () => {
  setLang(CYNOVELA_LANG);
});

// --- Block #2 (FIX app.js L501-L504) ---
document.addEventListener('DOMContentLoaded', () => {
  const cm = document.getElementById('chat-messages');
  if (cm) cm.style.setProperty('--chat-font-size', _chatFontSize + 'px');
});

// fix065-066 段 D-1: ワークスペース作成モーダルの「全選択 / クリア」ボタンが
// 参照する 3 関数。state.js:1805-1811 の selBtns() が onclick で呼ぶが本体未定義だった。
function _toggleAllChecks(containerId, checked) {
  const cont = document.getElementById(containerId);
  if (!cont) return;
  cont.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = !!checked; });
}
window.selectAllWsSources = (checked) => _toggleAllChecks('ws-src-checks', checked);
window.selectAllWsUsers = (checked) => _toggleAllChecks('ws-user-checks', checked);
window.selectAllWsPolicies = (checked) => _toggleAllChecks('ws-policy-checks', checked);
