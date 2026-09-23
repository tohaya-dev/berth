// ui.js - Cynovela v13

async function unarchiveItem(type, id) {
  if (!type || !id) return;
  const path = _unarchivePath(type, id);
  if (!path) return;
  try {
    await API.patch(path, {});
    if (typeof showToast === 'function') {
      showToast(
        (CYNOVELA_LANG === 'ja') ? '復元しました' : 'Restored',
        'success'
      );
    }
    if (typeof refreshAllData === 'function') await refreshAllData();
    if (type === 'workspace' && typeof renderWorkspaces === 'function') renderWorkspaces();
    if (type === 'collection' && typeof renderCollections === 'function') renderCollections();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(lj('Unarchive failed: ', '復元失敗: ') + (e && e.message || 'unknown'), 'error');
    }
  }
}

function showToast(msg, type = 'info') {
  const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  // E-1: type 別に表示時間を変える (重要度の高いトーストは長く出す)
  //   error  : 9 秒 (重大エラーを見逃させない)
  //   warning: 5 秒 (注意喚起)
  //   その他 : 4 秒 (従来どおり)
  const durations = { error: 9000, warning: 5000, success: 4000, info: 4000 };
  const wrap = $('toast-wrap');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  // E-4: error 時のみ「ログを見る」アクションを追加 (Settings の監査ログへジャンプ)
  const logLink = type === 'error'
    ? `<a class="toast-action" href="javascript:void(0)" onclick="_toastViewLogs(this)">📋 ${bi('View logs', 'ログを見る')}</a>`
    : '';
  t.innerHTML = `<span class="toast-icon">${icons[type]||'ℹ️'}</span>
    <div class="toast-body"><div class="toast-msg">${escapeHtml(msg)}</div>${logLink}</div>
    <button class="toast-close" onclick="this.parentElement.remove()">✕</button>`;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), durations[type] ?? 4000);
}

async function showFilePreview(fileId) {
  try {
    const data = await API.get(`/api/files/${fileId}/preview`);
    const name = escapeHtml(data.name || '');
    let body;
    if (data.preview != null) {
      body = `<pre style="white-space:pre-wrap;word-break:break-word;
              background:#f8fafc;padding:12px;border-radius:6px;
              max-height:60vh;overflow:auto;font-size:13px;line-height:1.5;">${escapeHtml(data.preview)}</pre>
              <div style="font-size:12px;color:#94a3b8;margin-top:6px;">${lj('Showing the first 2000 characters', '先頭2000文字を表示しています')}</div>`;
    } else {
      body = `<div style="padding:14px;color:#94a3b8;">${lj(`Cannot preview (${escapeHtml(data.reason || 'unknown')})`, `プレビューできません (${escapeHtml(data.reason || '不明')})`)}</div>`;
    }
    openFormModal(`📄 ${name}`, body, lj('Close', '閉じる'), closeFormModal);
  } catch (e) {
    showToast(lj(`Preview failed: ${e.message}`, `プレビュー失敗: ${e.message}`), 'error');
  }
}

function _renderWsCardV2(ws, opts = {}) {
  const compact = !!opts.compact; // グリッド表示時は右ペイン非表示
  const safeName = escapeHtml(ws.name || lj('Untitled', '無題'));
  const safeNameJs = (ws.name || '').replace(/'/g, "\\'");
  const safeDescJs = (ws.description || '').replace(/'/g, "\\'");
  const desc = ws.description || lj('Manage data access boundaries', 'データアクセスの境界を管理');
  // メトリクス
  const fileCount = ws.file_count != null ? ws.file_count : (ws.source_ids||[]).length;
  const userCount = ws.user_count != null ? ws.user_count : (ws.user_ids||[]).length;
  const lastScanDate = ws.last_scan_date
    ? new Date(ws.last_scan_date).toLocaleDateString('ja-JP')
    : '—';
  const lastScanStatus = ws.last_scan_status || null;
  const scanIcon = lastScanStatus === 'ok' ? '✅'
                 : lastScanStatus === 'error' ? '❌' : '';
  const autoSync = !!ws.auto_sync;
  const publishedCols = ws.published_collections != null ? ws.published_collections : 0;
  const vectorChunks = ws.vectorized_chunks != null ? ws.vectorized_chunks : 0;
  const piiCount = ws.pii_count != null ? ws.pii_count : 0;
  const queryCount7d = ws.query_count_7d != null ? ws.query_count_7d : 0;
  // ガバナンスバッジ
  const polNames = ws.policy_names || [];
  const badges = polNames.map(n =>
    `<span class="ws-policy-badge">🛡️ ${escapeHtml(n)}</span>`
  ).join('');

  const rightPane = compact ? '' : `
    <div class="ws-card-right">
      <div class="ws-meta-row">
        <span class="ws-meta-icon">📦</span>
        <span class="ws-meta-label">Collection</span>
        <span class="ws-meta-value ${publishedCols === 0 ? 'ws-warn' : ''}">
          ${publishedCols > 0 ? `${publishedCols}${t('published_unit')}` : `<span class="ws-warn-text">⚠️ ${t('unpublished_badge')}</span>`}
        </span>
      </div>
      <div class="ws-meta-row">
        <span class="ws-meta-icon">⚡</span>
        <span class="ws-meta-label">${t('chunks_label')}</span>
        <span class="ws-meta-value">${vectorChunks}${t('searchable_unit')}</span>
      </div>
      <div class="ws-meta-row">
        <span class="ws-meta-icon">🔒</span>
        <span class="ws-meta-label">PII</span>
        <span class="ws-meta-value ${piiCount > 0 ? 'ws-warn' : ''}">
          ${piiCount > 0 ? `${piiCount} ⚠️` : t('none_label')}
        </span>
      </div>
      <div class="ws-meta-row">
        <span class="ws-meta-icon">💬</span>
        <span class="ws-meta-label">${t('queries_7d')}</span>
        <span class="ws-meta-value">${queryCount7d}</span>
      </div>
    </div>`;

  return `
    <div class="ws-card${compact ? ' ws-grid-view' : ''}" data-ws-id="${ws.id}"
         onclick="openWsDetail('${ws.id}','${safeNameJs}')">
      <div class="ws-card-header">
        <div class="ws-card-icon">📁</div>
        <div class="ws-card-title-block">
          <div class="ws-card-name">${safeName}</div>
          <div class="ws-card-desc">${escapeHtml(desc)}</div>
        </div>
        <div class="ws-card-badges">${badges}</div>
      </div>
      <div class="ws-card-body">
        <div class="ws-card-left">
          <div class="ws-meta-row">
            <span class="ws-meta-icon">📄</span>
            <span class="ws-meta-label">${t('files_label')}</span>
            <span class="ws-meta-value">${fileCount}</span>
          </div>
          <div class="ws-meta-row">
            <span class="ws-meta-icon">👥</span>
            <span class="ws-meta-label">${t('users_label')}</span>
            <span class="ws-meta-value">${userCount}</span>
          </div>
          <div class="ws-meta-row">
            <span class="ws-meta-icon">🕐</span>
            <span class="ws-meta-label">${t('last_scan')}</span>
            <span class="ws-meta-value ws-scan-status" data-status="${lastScanStatus || ''}">
              ${escapeHtml(lastScanDate)} ${scanIcon}
            </span>
          </div>
          <div class="ws-meta-row">
            <span class="ws-meta-icon">🔄</span>
            <span class="ws-meta-label">${t('auto_sync')}</span>
            <span class="ws-meta-value" style="color:${autoSync?'#15803d':'#94a3b8'};">
              ${autoSync ? 'ON' : 'OFF'}
            </span>
          </div>
        </div>
        ${rightPane}
      </div>
      <div class="ws-card-actions" onclick="event.stopPropagation();">
        <button class="ws-action-btn ws-action-edit" data-ws="${ws.id}"
                data-role-min="admin"
                onclick="event.stopPropagation();showEditWorkspace('${ws.id}','${safeNameJs}','${safeDescJs}')">
          ✏️ ${t('edit_btn')}
        </button>
        <button class="ws-action-btn ws-action-scan" data-ws="${ws.id}"
                data-role-min="admin"
                onclick="event.stopPropagation();onWsActionScan(this)">
          ▶ ${t('scan_btn')}
        </button>
        <button class="ws-action-btn ws-action-catalog" data-ws="${ws.id}"
                onclick="event.stopPropagation();onWsActionCatalog('${ws.id}')">
          📦 ${t('view_collections')}
        </button>
        <button class="ws-action-btn ws-action-policy" data-ws="${ws.id}"
                data-role-min="admin"
                title="${t('line1899')}"
                onclick="event.stopPropagation();showAssignPolicyModal('${ws.id}')">
          🛡️<span class="ws-action-label"> Policy</span>
        </button>
        <button class="ws-action-btn" data-ws="${ws.id}"
                data-role-min="admin"
                title="${t('line1905')}"
                onclick="event.stopPropagation();archiveItem('workspace', '${ws.id}')"
                style="color:#9a3412;">
          📦<span class="ws-action-label"></span>
        </button>
        <button class="ws-action-btn ws-action-delete" data-ws="${ws.id}"
                data-role-min="admin"
                title="${t('line1912')}"
                onclick="event.stopPropagation();deleteWorkspace('${ws.id}')"
                style="color:#991b1b;">
          🗑<span class="ws-action-label"></span>
        </button>
      </div>
    </div>`;
}

async function savePolicyFromModal() {
  const name = (document.getElementById('policy-modal-name')?.value || '').trim();
  if (!name) { showToast(lj('Please enter a policy name', 'ポリシー名を入力してください'), 'warning'); return; }
  const active = !!document.getElementById('policy-modal-active')?.checked;
  const rows = [...document.querySelectorAll('#policy-modal-rules .policy-rule-row')];
  const rules = rows.map(row => ({
    classifier: row.querySelector('.policy-rule-classifier')?.value,
    action:     row.querySelector('.policy-rule-action')?.value,
  })).filter(r => r.classifier && r.action);
  try {
    if (_editingPolicyId) {
      await API.put(`/api/policies/${_editingPolicyId}`, {
        name, rules, state: active ? 'active' : 'inactive',
      });
      showToast(lj('Policy updated', 'ポリシーを更新しました'), 'success');
    } else {
      const created = await API.post('/api/policies', { name, rules });
      if (!active && created && created.id) {
        await API.put(`/api/policies/${created.id}`, { state: 'inactive' });
      }
      showToast(lj('Policy created', 'ポリシーを作成しました'), 'success');
    }
    closePolicyModal();
    renderGuardrails();
  } catch (e) {
    showToast(lj(`Save failed: ${e.message}`, `保存失敗: ${e.message}`), 'error');
  }
}

async function deletePolicyConfirm(policyId, name) {
  confirmAction(lj('Delete policy', 'ポリシー削除'), lj(`Delete policy "${name}"?`, `ポリシー「${name}」を削除しますか？`), '🗑', async () => {
    try {
      await API.del(`/api/policies/${policyId}`);
      showToast(lj('Deleted', '削除しました'), 'success');
      renderGuardrails();
    } catch (e) { showToast(lj(`Delete failed: ${e.message}`, `削除失敗: ${e.message}`), 'error'); }
  });
}

async function _restoreArchived(kind, id) {
  try {
    await API.post(`/api/archived/${kind}/${id}/restore`, {});
    showToast(lj(`${kind} restored`,`${kind} を復元しました`), 'success');
    loadArchivedSection();
    refreshAllData().catch(()=>{});
  } catch (e) { showToast(lj(`Restore failed: ${e.message}`,`復元失敗: ${e.message}`), 'error'); }
}

async function _purgeArchived(kind, id) {
  if (!confirm(lj('This will permanently delete. This action cannot be undone. Continue?', '完全に削除します。この操作は復元できません。続行しますか？'))) return;
  try {
    await API.del(`/api/archived/${kind}/${id}`);
    showToast(lj(`${kind} permanently deleted`,`${kind} を完全削除しました`), 'success');
    loadArchivedSection();
  } catch (e) { showToast(lj(`Delete failed: ${e.message}`,`削除失敗: ${e.message}`), 'error'); }
}

async function fetchCompareBModels() {
  const ep = ($('cmp-b-endpoint')?.value || '').trim();
  const sel = $('cmp-b-model-list');
  if (!ep || !sel) {
    showToast(lj('Please enter an endpoint','エンドポイントを入力してください'), 'warning');
    return;
  }
  sel.innerHTML = '<option>' + bi('Loading...', '読み込み中...') + '/option>';
  sel.style.display = '';
  try {
    const r = await API.post('/api/llm/list-models', { base_url: ep });
    const models = r.models || r.data || [];
    sel.innerHTML = `<option value="">${lj('— Select a model —', '— モデルを選択 —')}</option>` +
      (models.map(m => {
        const id = m.id || m.name || m;
        return `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`;
      }).join(''));
  } catch (e) {
    sel.innerHTML = `<option>${lj(`Fetch failed: ${escapeHtml(e.message)}`, `取得失敗: ${escapeHtml(e.message)}`)}</option>`;
  }
}

// ga-close-v3 PartX: 伏字の強さの書き込み系 savePiiMode() を撤去。画面から選択肢を外したため
// 呼び出し元は0になった。PUT /api/settings/pii-mode の受け口・引き渡しの仕組みは本ランでは変更しない。

async function addSource() {
  const name = $('new-src-name').value.trim();
  const path = $('new-src-path').value.trim();
  if (!name || !path) return showToast(lj('Please enter name and path','名前とパスを入力してください'), 'warning');
  try {
    await API.post('/api/sources', { name, path });
    closeFormModal();
    showToast(lj(`Source "${name}" added (scanning in background...)`,`ソース「${name}」を追加しました（バックグラウンドでスキャン中...）`), 'success');
    renderSources();
    // バックグラウンドスキャンの進捗を反映するため、数回ポーリングする
    let polls = 0;
    const poll = setInterval(async () => {
      polls++;
      await renderSources();
      const fresh = State.sources.find(s => s.name === name);
      if (!fresh || fresh.status === 'completed' || fresh.status === 'failed' || polls > 10) {
        clearInterval(poll);
        if (fresh && fresh.status === 'completed') {
          showToast(lj(`Scan complete: ${fresh.file_count} files`,`スキャン完了: ${fresh.file_count}ファイル`), 'success');
        } else if (fresh && fresh.status === 'failed') {
          showToast(lj(`Scan failed: ${_displaySourcePath(fresh.path)}`,`スキャン失敗: ${_displaySourcePath(fresh.path)}`), 'error');
        }
      }
    }, 1000);
  } catch (e) { showToast(lj(`Add failed: ${e.message}`,`追加失敗: ${e.message}`), 'error'); }
}

// ga-close-v3 PartX: ブラウザ送信入口の実行系 uploadFiles() を撤去 (intake-togo-v2-20260705 で
// 画面から外れて以降、呼び出し元は同じ死にコード群の中だけだった)。/api/upload の受け口は Agent A の担当。

async function openSourceInFinder(id) {
  try {
    const r = await API.get(`/api/sources/${id}/open-in-finder`);
    // fix2-D: コンテナ実行時は OS ファイルマネージャーを開けないため、ホスト側パスを info 案内する
    // (誤誘導の success トーストやエラートーストを出さない)。standalone は従来どおり success。
    if (r && r.container) {
      showToast(r.message || lj('Cannot open Finder in container mode.', 'コンテナ実行のため Finder は開けません'), 'info');
      return;
    }
    const label = r?.opened_with || lj('File manager', 'ファイルマネージャー');
    showToast(lj(`Opened with ${label}`, `${label}で開きました`), 'success');
  } catch (e) { showToast(lj(`File manager integration failed: ${e.message}`,`ファイルマネージャー連携失敗: ${e.message}`), 'error'); }
}

async function createWorkspace() {
  const name = $('new-ws-name').value.trim();
  if (!name) return showToast(lj('Please enter a name','名前を入力してください'), 'warning');
  const srcIds = [...document.querySelectorAll('#ws-src-checks input:checked')].map(i => i.value);
  const userIds = [...document.querySelectorAll('#ws-user-checks input:checked')].map(i => i.value);
  const policyIds = [...document.querySelectorAll('#ws-policy-checks input:checked')].map(i => i.value);
  // fix-all-v2: 取り込みモード (pdf_mode) をフォームから読み取り POST body に含める。
  const pdfMode = $('new-ws-pdf-mode')?.value || 'fast';
  try {
    closeFormModal();
    showToast(lj('Creating workspace (including automatic source scan)...', 'Workspace作成中（ソースの自動スキャンを含む）...'), 'info');
    await API.post('/api/workspaces', { name, source_ids: srcIds, user_ids: userIds, policy_ids: policyIds, pdf_mode: pdfMode });
    showToast(lj(`Workspace "${name}" created`, `Workspace「${name}」を作成しました`), 'success');
    await refreshAllData();
    renderWorkspaces();
  } catch (e) {
    // fix064 H: 同名重複 (HTTP 409) は分かりやすいメッセージで通知
    const msg = String(e && e.message || '');
    if (msg.includes('409') || msg.includes('already exists') || msg.includes('既に存在')) {
      showToast(lj(`Workspace name already exists: ${name}`, `ワークスペース名「${name}」は既に存在します`), 'error');
    } else {
      showToast(lj(`Create failed: ${e.message}`,`作成失敗: ${e.message}`), 'error');
    }
  }
}

// fix-export-usable-container-20260717: エクスポート実行中フラグ＋ボタンbusy化ヘルパ
let _exportInProgress = false;

function _setExportButtonsBusy(busy) {
  ['export-ws-btn', 'full-export-ws-btn'].forEach(id => {
    const b = document.getElementById(id);
    if (!b) return;
    if (busy) { if (!b.dataset.origHtml) b.dataset.origHtml = b.innerHTML; b.disabled = true; b.innerHTML = '⏳ ' + lj('Exporting…', 'エクスポート中…'); }
    else { if (b.dataset.origHtml) b.innerHTML = b.dataset.origHtml; b.disabled = false; }
  });
  if (!busy && typeof _updateExportButtonsState === 'function') _updateExportButtonsState();
}

async function exportWorkspace(wsIdArg) {
  const wsId = wsIdArg || ((typeof getCurrentWorkspaceId === 'function') ? getCurrentWorkspaceId() : '');
  if (!wsId) { showToast(lj('Please select a workspace', 'ワークスペースを選択してください'), 'error'); return; }
  if (_exportInProgress) return;
  _exportInProgress = true;
  _setExportButtonsBusy(true);
  try {
    const headers = {};
    if (API.token) headers['Authorization'] = `Bearer ${API.token}`;
    const res = await fetch(`/api/workspaces/${wsId}/export`, { headers });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workspace_${wsId}.zip`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(lj('Export complete', 'エクスポートが完了しました'), 'success');
  } catch (e) {
    showToast(lj('Export failed', 'エクスポートに失敗しました'), 'error');
  } finally {
    _exportInProgress = false;
    _setExportButtonsBusy(false);
  }
}

async function fullExportWorkspace(wsIdArg) {
  const wsId = wsIdArg || ((typeof getCurrentWorkspaceId === 'function') ? getCurrentWorkspaceId() : '');
  if (!wsId) { showToast(lj('Please select a workspace', 'ワークスペースを選択してください'), 'error'); return; }
  if (_exportInProgress) return;
  _exportInProgress = true;
  _setExportButtonsBusy(true);
  showToast(lj('Full export started (please wait a moment)', 'フルエクスポートを開始しました（しばらくお待ちください）'), 'info');
  try {
    const headers = {};
    if (API.token) headers['Authorization'] = `Bearer ${API.token}`;
    const res = await fetch(`/api/workspaces/${wsId}/full-export`, { headers });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workspace_${wsId}_full.zip`;
    a.click();
    URL.revokeObjectURL(url);
    showToast(lj('Full export complete', 'フルエクスポートが完了しました'), 'success');
  } catch (e) {
    showToast(lj('Full export failed', 'フルエクスポートに失敗しました'), 'error');
  } finally {
    _exportInProgress = false;
    _setExportButtonsBusy(false);
  }
}

async function importWorkspace() {
  const fileEl = document.getElementById('import-workspace-file');
  const file = fileEl?.files?.[0];
  if (!file) { showToast(lj('Please select a ZIP file', 'ZIPファイルを選択してください'), 'error'); return; }
  if (!confirm(lj('Import a workspace. Continue?', 'ワークスペースをインポートします。続行しますか？'))) return;
  const el = document.getElementById('import-workspace-result');
  if (el) el.textContent = lj('Importing...', 'インポート中...');
  try {
    const form = new FormData();
    form.append('file', file);
    const headers = {};
    if (API.token) headers['Authorization'] = `Bearer ${API.token}`;
    const res = await fetch('/api/workspaces/import', {
      method: 'POST',
      headers,
      body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    if (el) el.textContent = lj('Done', '完了');
    showToast(lj('Import complete. Please publish the collection.', 'インポートが完了しました。コレクションをPublishしてください。'), 'success');
    if (typeof refreshAllData === 'function') refreshAllData();
  } catch (e) {
    if (el) el.textContent = lj('Failed', '失敗');
    showToast(lj('Import failed', 'インポートに失敗しました'), 'error');
  }
}

async function startPublishStream(colId) {
  // PORTABILITY FIX 20260527 Stage2 D-2: 再 Publish 前に差分チェック
  // 前回 Publish から差分が無ければユーザに確認する
  try {
    const diff = await API.get(`/api/collections/${colId}/publish-diff`);
    if (diff && diff.has_changes === false) {
      // #4: confirm を廃止し、変更なし時は再Publish を完全ブロック (即リターン)。
      showToast(lj('No changes since last publish. Update documents before re-publishing.',
        '変更がないため Publish できません。ドキュメントを更新してから再試行してください。'), 'warning');
      return;
    }
  } catch (e) {
    // U-8 (DD-CYN-0020): 差分が取れないときは「変更が無い」とも「ある」とも言えない。
    //   判断できないので素通しで公開へ進ませず、失敗の中身を画面に出して止める
    //   (フェイルクローズ)。以前はここを黙って通していた。
    console.warn('publish-diff check failed (blocking):', e);
    showToast(lj(
      `Publish cancelled: could not check for changes: ${e.message}`,
      `変更の有無を確認できないため Publish を中止しました: ${e.message}`), 'error');
    return;
  }
  showToast(lj('Publishing...', 'Publish開始...'), 'info');
  ensureProgressUI(colId);
  // V3.5.0 取り込み可視化: 個別Publishは inline 進捗バー(ensureProgressUI)+ 完了時に
  // 3行サマリーの受領書(IngestViz・overlay なし)。定型文を毎回繰り返さない。
  if (typeof IngestViz !== 'undefined') {
    const _c = (typeof State !== 'undefined' && State.collections || []).find(c => c.id === colId);
    IngestViz.start(colId, { colName: (_c && _c.name) || '', overlay: false });
  }
  let res;
  try {
    res = await API.post(`/api/collections/${colId}/publish/async`, {});
  } catch (e) {
    showToast(lj(`Publish start failed: ${e.message}`, `Publish開始失敗: ${e.message}`), 'error');
    finalizeProgressUI(colId, false, e.message);
    return;
  }
  const jobId = res.job_id;
  if (!jobId) {
    showToast(lj('Publish start failed: job_id not obtained', 'Publish開始失敗: job_id 未取得'), 'error');
    finalizeProgressUI(colId, false, 'no job_id');
    return;
  }
  // ingest-resilience: jobId 永続化(リロード復帰/前回ログ)＋「裏で継続」案内。
  if (typeof IngestViz !== 'undefined') {
    const _c2 = (typeof State !== 'undefined' && State.collections || []).find(c => c.id === colId);
    IngestViz.registerJob(colId, jobId, { colName: (_c2 && _c2.name) || '' });
  }
  _startPublishPoll(colId, jobId);
}

async function stopPublish(colId) {
  try {
    await API.post(`/api/collections/${colId}/publish/stop`, {});
    showToast(lj('Stop request sent','停止要求を送信しました'), 'info');
  } catch (e) { showToast(lj(`Stop failed: ${e.message}`,`停止失敗: ${e.message}`), 'error'); }
}

function _detectPiiClient(text) {
  // 元テキスト基準でスパン収集 → 重複解消 → マスクHTMLを構築
  if (!text) return { spans: [], maskedHtml: escapeHtml(text || '') };
  const spans = [];
  PII_REGEXES.forEach(({type, re}) => {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      spans.push({ type, start: m.index, end: m.index + m[0].length, value: m[0] });
    }
  });
  if (!spans.length) return { spans, maskedHtml: escapeHtml(text).replace(/\n/g, '<br>') };
  spans.sort((a, b) => a.start - b.start);
  const dedup = [];
  let lastEnd = -1;
  for (const s of spans) {
    if (s.start >= lastEnd) { dedup.push(s); lastEnd = s.end; }
  }
  // ga-close-v3 PartX: 型ラベルは state.js の1か所 (piiTypeLabel) から取る。
  const labels = new Proxy({}, { get: (_t, k) => piiTypeLabel(String(k)) });
  let cursor = 0;
  const parts = [];
  for (const s of dedup) {
    parts.push(escapeHtml(text.slice(cursor, s.start)));
    parts.push(`<span class="pii-mask" title="${lj(`Masked ${escapeHtml(labels[s.type] || s.type)} when sending to the LLM`, `LLMへの送信時に ${escapeHtml(labels[s.type] || s.type)} をマスクしました`)}"
                 style="background:#fffbeb;color:#92400e;padding:1px 6px;border-radius:4px;
                        font-size:0.92em;border:1px solid #fde68a;cursor:help;">[MASKED:${s.type}]</span>`);
    cursor = s.end;
  }
  parts.push(escapeHtml(text.slice(cursor)));
  return { spans: dedup, maskedHtml: parts.join('').replace(/\n/g, '<br>') };
}

// A-2: saveSettings() は Legacy #set-endpoint/#set-model カード専用だったため、カード撤去に伴い削除。
//      正規経路の保存は governance.js の applyLlmSettings(/api/settings/llm) に一本化済み。

async function resetUserPassword(uid) {
  const password = $('reset-user-password').value;
  if (!password || password.length < 4) return showToast(lj('Password must be at least 4 characters','パスワードは4文字以上で入力してください'), 'warning');
  try {
    await API.post(`/api/admin/users/${uid}/reset-password`, { password });
    closeFormModal();
    showToast(lj('Password changed','パスワードを変更しました'), 'success');
  } catch (e) { showToast(lj(`Change failed: ${e.message}`,`変更失敗: ${e.message}`), 'error'); }
}

function deactivateUser(uid, username) {
  confirmAction(lj('Deactivate user', 'ユーザー無効化'), lj(`Deactivate "${username}"? (soft delete; audit logs are retained)`, `「${username}」を無効化しますか？（論理削除・監査ログは保持）`), '🚫', async () => {
    try {
      await API.del(`/api/admin/users/${uid}`);
      showToast(lj(`"${username}" deactivated`, `「${username}」を無効化しました`), 'success');
      renderAdminUsers();
    } catch (e) { showToast(lj(`Disable failed: ${e.message}`,`無効化失敗: ${e.message}`), 'error'); }
  });
}

function deleteBackup(name) {
  confirmAction(lj('Delete backup', 'バックアップ削除'), lj(`Delete "${name}"?`, `「${name}」を削除しますか？`), '🗑', async () => {
    try {
      await API.del(`/api/admin/backups/${encodeURIComponent(name)}`);
      showToast(lj('Backup deleted','バックアップを削除しました'), 'success');
      renderBackupList();
    } catch (e) { showToast(lj(`Delete failed: ${e.message}`,`削除失敗: ${e.message}`), 'error'); }
  });
}

function renderMarkdownAnswer(answer) {
  // #02: marked.js が読み込まれていれば Markdown を HTML レンダリング。
  //       未ロード時は従来の簡易処理（escapeHtml + **強調** + 改行）にフォールバック。
  const src = answer || '';
  let html;
  if (typeof window !== 'undefined' && typeof window.marked !== 'undefined') {
    try {
      window.marked.setOptions({ breaks: true, gfm: true, headerIds: false, mangle: false });
      html = sanitizeHtml(window.marked.parse(src));
    } catch (e) { /* fallback below */ }
  }
  if (html == null) {
    html = escapeHtml(src).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
  }
  return _wrapCodeBlocksWithToolbar(html);
}

async function showSessionList() {
  const wsId = $('chat-ws-sel')?.value;
  if (!wsId) { showToast(lj('Please select a Workspace', 'Workspaceを選択してください'), 'warning'); return; }
  let body = '<div style="color:var(--text-3);font-size:16px">' + bi('Loading...', '読み込み中...') + '</div>';
  showP3Modal('💬 ' + lj('Chat history', 'チャット履歴'), body);
  try {
    const list = await API.get(`/api/sessions?workspace_id=${encodeURIComponent(wsId)}`);
    if (!list.length) {
      body = '<div style="padding:12px;color:var(--text-3);font-size:16px;text-align:center">' + lj('No sessions', 'セッションがありません') + '</div>';
    } else {
      body = list.map(s => {
        const safeId = String(s.id).replace(/'/g, "\\'");
        const title = escapeHtml(s.title || t('line6415'));
        const updated = (s.updated_at || '').replace('T', ' ').slice(0,16);
        const cnt = s.message_count || 0;
        return `<div class="session-item" onclick="loadSession('${safeId}')">
          <div class="session-title">${title}</div>
          <div class="session-meta">${updated} / ${lj(`${cnt} msgs`, `${cnt}件`)}</div>
          <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();deleteSessionUI('${safeId}')">🗑</button>
        </div>`;
      }).join('');
    }
    const m = document.getElementById('p3-modal');
    if (m) m.querySelector('.p3-modal-body').innerHTML = body;
  } catch (e) {
    showToast(lj(`Load failed: ${e.message}`,`読み込み失敗: ${e.message}`), 'error');
  }
}

async function deleteSessionUI(sessionId) {
  if (!confirm(lj('Delete this chat history?', 'このチャット履歴を削除しますか？'))) return;
  try {
    await API.del(`/api/sessions/${sessionId}`);
    showSessionList();
  } catch (e) { showToast(lj(`Delete failed: ${e.message}`,`削除失敗: ${e.message}`), 'error'); }
}

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str || '');
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function qsScanFolder() {
  const folder = document.getElementById('qs-folder-input')?.value?.trim();
  const host = document.getElementById('qs-preview-host');
  if (!folder) { showToast(lj('Please enter a folder path','フォルダパスを入力してください'), 'warn'); return; }
  if (_qsLooksLikeFilePath(folder)) {
    showToast(lj('Please specify a folder path, not a file', 'ファイルパスではなくフォルダパスを指定してください'), 'error');
    return;
  }
  if (!host) return;
  host.innerHTML = '<div style="font-size:16px;color:#94a3b8;">' + lj('Scanning...', 'スキャン中...') + '</div>';
  try {
    const data = await API.post('/api/folder-scan-preview', { folder_path: folder, recursive: true });
    const f = data.files || {};
    const sk = f.skipped || {};
    const min = Math.ceil((data.estimated_time_sec || 0) / 60);
    host.innerHTML = `
      <div style="font-size:17px;line-height:1.7;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;">
        <div><strong>📂 ${lj(`Detected files: ${data.total_supported || 0}`, `検出ファイル: ${data.total_supported || 0} 件`)}</strong></div>
        <div style="font-size:16px;margin-top:4px;color:#475569;">
          PDF ${f.pdf||0} / Word ${f.docx||0} / Excel ${f.xlsx||0} / PPTX ${f.pptx||0} /
          TXT ${f.txt||0} / Markdown ${f.md||0} / CSV ${f.csv||0} /
          HTML ${f.html||0} / Email ${f.eml||0} / ZIP ${f.zip||0} / ${lj('Images', '画像')} ${f.images||0}
        </div>
        <div style="font-size:16px;color:#94a3b8;margin-top:4px;">
          ${lj('Skipped', 'スキップ')}: ${lj('Video', '動画')} ${sk.video||0} / ${lj('Audio', '音声')} ${sk.audio||0} / ${lj('Other', 'その他')} ${sk.other||0}
        </div>
        <div style="font-size:16px;margin-top:6px;color:#475569;">
          ⏱️ ${lj(`Estimated processing time: ~${min} min (incl. image processing: ${Math.ceil((data.image_processing_time_sec||0)/60)} min)`, `推定処理時間: 約 ${min} 分 (画像処理: ${Math.ceil((data.image_processing_time_sec||0)/60)} 分含む)`)}
        </div>
      </div>`;
    // ファイル内訳から最適プリセット推奨
    let recommended = 'mixed';
    if ((f.images || 0) > 5 && (f.images || 0) > (f.pdf || 0)) recommended = 'tech_manual';
    else if ((f.xlsx || 0) > 5) recommended = 'table_data';
    else if ((f.eml || 0) > 5) recommended = 'communication';
    else if ((f.pdf || 0) + (f.docx || 0) > 0) recommended = 'tech_manual';
    const sel = document.getElementById('qs-preset-sel');
    if (sel) {
      sel.value = recommended;
      qsUpdatePresetDetail();
    }
    window._qsLastScan = { folder, total: data.total_supported || 0, estimated_min: min };
  } catch (e) {
    host.innerHTML = `<div style="color:#ef4444;font-size:16px;">${lj(`Scan failed: ${escapeHtml(e.message)}`, `スキャン失敗: ${escapeHtml(e.message)}`)}</div>`;
  }
}

async function qsExecute(folder, preset, mode, policyId, policyLabel, qualityLabel) {
  // V3.5.0 取り込み可視化: チャンキング中のフリッカートースト連発を撤去し、
  // 左右分割パネル(IngestViz)で「進捗バー + 生ログ + 完了の3行サマリー」に一本化する。
  document.getElementById('quickstart-modal')?.remove();
  localStorage.setItem('rag_preset', mode);
  localStorage.setItem('chunking_preset', preset);
  if (policyId) localStorage.setItem('qs_policy_id', policyId);
  const ts = new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 14);
  const wsName = `Quick_${ts}`;
  const colName = `Quick_${ts}_collection`;
  // DD-CYN-0089 §6-A: 失敗したときに、どの資料の可視化パネルを閉じればよいかを
  //   catch から知るための控え。col は try の中の const なので catch からは見えない。
  let _qsColId = null;
  try {
    // DD-CYN-0091 A: 作業場所には、その時点で在る閲覧者(viewer)の役割の利用者を全員含める。
    //   管理者が画面から作る道 (openWsModal) は user_ids を明示で渡すため、その既定は変わらない。
    let _qsUserIds = ['user-admin'];
    try {
      const _users = await API.get('/api/admin/users');
      const _viewerIds = (_users || [])
        .filter(u => u.role === 'viewer' && u.is_active !== 0 && u.is_active !== false)
        .map(u => u.id);
      _qsUserIds = Array.from(new Set(['user-admin', ..._viewerIds]));
    } catch (e) { console.warn('quickstart: viewer list fetch failed (continuing with admin only):', e); }

    // DD-CYN-0091 C: 同じ取り込み元 (source.path) に既にまとまりが在るなら、新しく作らず
    //   そのまとまりを更新する。照合は取り込み元の識別子 (path) で行う。名前や時刻では照合しない。
    //   複数見つかったときは最も新しいものを更新する (§16-7)。
    let ws = null, src = null, col = null, _qsReused = false;
    try {
      const _srcs = await API.get('/api/sources');
      const _srcList = Array.isArray(_srcs) ? _srcs : ((_srcs && _srcs.items) || []);
      const _srcCand = _srcList.filter(s => s.path === folder);
      if (_srcCand.length > 0) {
        const _srcIdSet = new Set(_srcCand.map(s => s.id));
        const _wss = await API.get('/api/workspaces');
        const _wsList = Array.isArray(_wss) ? _wss : ((_wss && _wss.items) || []);
        const _wsCand = _wsList.filter(w => (w.source_ids || []).some(id => _srcIdSet.has(id)));
        if (_wsCand.length > 0) {
          const _wsIdSet = new Set(_wsCand.map(w => w.id));
          const _cols = await API.get('/api/collections');
          const _colList = Array.isArray(_cols) ? _cols : ((_cols && _cols.items) || []);
          const _colCand = _colList.filter(c => _wsIdSet.has(c.workspace_id))
            .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
          if (_colCand.length > 0) {
            col = _colCand[0];
            ws = _wsCand.find(w => w.id === col.workspace_id) || _wsCand[0];
            src = _srcCand.find(s => (ws.source_ids || []).includes(s.id)) || _srcCand[0];
            _qsReused = true;
          }
        }
      }
    } catch (e) { console.warn('quickstart: existing-collection lookup failed (creating new):', e); }

    if (!_qsReused) {
      // ① WS 作成 / ② Source 作成 / ③ WS↔Source 紐付け
      ws = await API.post('/api/workspaces', { name: wsName, description: lj('Quick start', 'クイックスタート'), user_ids: _qsUserIds });
      // QuickStart は直後に自分で走査を開始する。サーバ既定の auto_scan (バックグラウンド走査) と
      // 二重になると、明示の走査が 409 (二重走査は開始しない) で断られて QuickStart が途中で止まる。
      src = await API.post('/api/sources', { name: `qs_src_${ts}`, path: folder, auto_scan: false });
      await API.put(`/api/workspaces/${ws.id}`, { source_ids: [src.id] });

      // ③' ポリシー適用 (選択時のみ・失敗は致命ではない=ガバナンス証跡は受領書/来歴に残す)
      // qs-policy-fix-20260627: 汎用 PUT /api/workspaces/{id} は guardrail_policy_id を処理せず黙殺するため
      //   割り当てが付かなかった(バッジ無し)。WS編集モーダルと同じ専用EP(workspace_policies へ INSERT)へ是正。
      if (policyId) {
        try { await API.put(`/api/workspaces/${ws.id}/policy`, { policy_ids: [policyId] }); }
        catch (e) { console.warn('quickstart policy attach failed (continuing):', e); }
      }
    }

    // ④ Source スキャン (同期: ファイル登録まで完了する)
    await API.post(`/api/sources/${src.id}/scan`);

    // ⑤ ファイルID取得 (scan レスポンスは file_ids を含まないため別途 GET)
    const files = await API.get(`/api/sources/${src.id}/files`);
    const fileIds = (files || []).map(f => f.id).filter(Boolean);
    if (fileIds.length === 0) {
      showToast(lj('⚠️ No ingestible files were found', '⚠️ 取り込めるファイルが見つかりませんでした'), 'warning');
      return;
    }

    if (!_qsReused) {
      // ⑥ Collection 作成
      col = await API.post('/api/collections', {
        name: colName,
        workspace_id: ws.id,
        file_ids: fileIds,
      });
    } else {
      // ⑥' 既存のまとまりへファイル構成を同期 (再スキャンの増減を反映) → 再publishで更新
      await API.put(`/api/collections/${col.id}`, { file_ids: fileIds });
    }
    _qsColId = col.id;
    // 新規 Collection を State に取り込む (Publish 中に Collections に遷移した場合の
    // _reattachPublishProgress が card.querySelector で UI 復元できるように)
    await refreshAllData();

    // ⑦ 取り込み可視化パネル(左右分割)を開く=通し案内。これ以降フリッカートーストは出さない。
    const _qsColName = _qsReused ? (col.name || colName) : colName;
    IngestViz.start(col.id, {
      title: lj(`Quick start: ${_qsColName}`, `クイックスタート: ${_qsColName}`),
      colName: _qsColName, folder, fileCount: fileIds.length,
      policyLabel: policyLabel || '', qualityLabel: qualityLabel || '',
      overlay: true, gotoChat: true,
    });
    IngestViz.event(col.id, '🗂️', _qsReused
      ? lj(`Reusing workspace "${(ws && ws.name) || ''}"`, `既存のワークスペース「${(ws && ws.name) || ''}」を使います`)
      : lj(`Created workspace "${wsName}"`, `ワークスペース「${wsName}」を作成`));
    IngestViz.event(col.id, '📂', lj(`Scanned folder: ${fileIds.length} files detected`, `フォルダをスキャン: ${fileIds.length} ファイル検出`));
    IngestViz.event(col.id, '📁', _qsReused
      ? lj(`Updating collection "${_qsColName}"`, `既存のコレクション「${_qsColName}」を更新します`)
      : lj(`Created collection "${colName}"`, `コレクション「${colName}」を作成`));

    // ⑧ 非同期 Publish 起動 (publish/async + polling 仕組みを利用)
    const job = await API.post(`/api/collections/${col.id}/publish/async`, {});
    if (!job.job_id) {
      IngestViz.fail(col.id, lj('Publish start failed (job_id not obtained)', 'Publish 開始失敗 (job_id 未取得)'));
      return;
    }
    // ingest-resilience: jobId を永続化(リロード復帰/前回ログ用)＋「裏で継続」案内。
    IngestViz.registerJob(col.id, job.job_id, { colName: _qsColName, overlay: true });

    // ⑨ polling: 進捗/完了は IngestViz が _pollPublishJob 経由で反映。
    //     完了時のチャット遷移はパネルの「チャットへ」ボタンに委譲(完了サマリーを見てから移動)。
    _startPublishPoll(col.id, job.job_id, {
      onFail: () => { navigate('collections'); },
    });
  } catch (e) {
    // DD-CYN-0089 §6-A: ここが showToast だけだったため、次の2つが同時に起きていた。
    //   1. 可視化パネル (.iv-overlay) が「準備中…」のまま残る。押したのに何も
    //      起きていないように見える。公開の開始が拒否された場合、サーバは job の行を
    //      作らないので、後から /api/jobs/{id} を引いて追い直す道も無い。
    //   2. そのトーストは #toast-wrap に出るが、パネルの暗幕の裏に隠れていた
    //      (z-index の直しは style.css 側で行う)。
    // 一覧から公開を押した道 (startPublishStream) は、同じ場面で進み具合の表示を
    // 閉じてから失敗を出している。ここもそれに合わせる。文言は同じものを使う。
    const _m = (e && e.message) || '';
    if (_qsColId) {
      if (typeof IngestViz !== 'undefined' && typeof IngestViz.isTracking === 'function'
          && IngestViz.isTracking(_qsColId)) {
        IngestViz.fail(_qsColId, _m);
      }
      if (typeof finalizeProgressUI === 'function') finalizeProgressUI(_qsColId, false, _m);
    }
    showToast(lj(`Quick start failed: ${_m}`,`クイックスタート失敗: ${_m}`), 'error');
  }
}

// ga-close-v3 PartX: クイックスタート「手元のファイルを送る」実行系 qsExecuteUpload() を撤去。
// 呼び出し元は intake-togo-v2-20260705 で撤去済みで、以後 参照0 のまま残っていた。

// ===== Stage 3: DOMContentLoaded blocks moved from FIX app.js =====

// --- Block #5 (FIX app.js L6011-L6013) ---
// alpha §段4: renderQuestionTemplates 呼出撤去 (関数自体を chat.js で撤去)

