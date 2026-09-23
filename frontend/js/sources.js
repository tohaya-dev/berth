// sources.js - Cynovela v13

function showAddSourceModal() {
  // intake-togo-v2-20260705: 二段階フロー（1/2 フォルダ選択 → 2/2 追加先選択）。
  // 取り込み入口はこの「＋ソース追加」一つに集約（入口一元化）。文言は「取り込みフォルダ」基準。
  openFormModal(lj('Add from ingest folder (1/2: choose folder)', '取り込みフォルダから追加（1/2: フォルダ選択）'), `
    <div class="form-group"><label class="form-label">${bi('Name', '名前')}</label>
      <input id="new-src-name" class="form-input" placeholder="${lj('e.g. HR & Legal folder', '例: 人事法務フォルダ')}"></div>
    <div class="form-group">
      <label class="form-label">📂 ${bi('Target folder to ingest', '取り込み対象フォルダ')}</label>
      <div style="display:flex;gap:6px;align-items:center;">
        <input id="new-src-path" class="form-input" placeholder="${lj('e.g. choose from the ingest source list', '例: 取り込み元の一覧から選択')}" style="flex:1;">
        <button type="button" class="btn btn-sm" onclick="showFolderBrowser('new-src-path')">📁 ${bi('Browse', '参照')}</button>
      </div>
      <div class="form-hint">
        ${lj('Specify a <strong>folder only</strong> (file paths are invalid).', '<strong>フォルダのみ</strong>を指定してください（ファイルパスは無効）。')}<br>
        ${lj('Subfolders of the ingest folder can each be registered as separate sources.', '取り込みフォルダ配下のサブフォルダは、それぞれ別のソースとして登録できます。')}<br>
        ${lj('If a folder is not in the list: in the container / Kubernetes form, put it under the ingest directory that is mounted into the pods as /app/ingest (Linux / WSL install: $BERTH_DATA_DIR/ingest/&lt;folder&gt;). It shows up here without a restart. In the standalone form, register it with ./launch.sh --add-path &lt;folder path&gt;.', '一覧に無いフォルダは、コンテナ / Kubernetes 形態では、Pod から /app/ingest として見える取り込みフォルダの下（Linux / WSL の導入では $BERTH_DATA_DIR/ingest/&lt;フォルダ&gt;）に置くと、起動し直さなくてもここに出ます。スタンドアロン形態では ./launch.sh --add-path &lt;フォルダのパス&gt; で登録します。')}
      </div>
    </div>
  `, lj('Next', '次へ'), _addSourceStep2);
}

// intake-togo-v2-20260705: 二段階フロー 2/2。追加先（既存WS or 新規WS）を選ぶ。
// 既存APIのみで成立（Agent 4 ゲートPASS準拠）。addSource(旧一段階実行部)は残置。
function _addSourceStep2() {
  const name = $('new-src-name').value.trim();
  const path = $('new-src-path').value.trim();
  if (!name || !path) { showToast(lj('Please enter name and folder', '名前とフォルダを入力してください'), 'warning'); return; }
  // POST /api/sources に同名409ガードが無いため、フロントで事前照合する
  if ((State.sources || []).some(s => (s.name || '') === name)) {
    showToast(lj('A source with this name already exists', '同じ名前のソースが既にあります'), 'warning'); return;
  }
  window._addSrcDraft = { name, path };
  const wsOptions = (State.workspaces || [])
    .map(w => `<option value="${escapeHtml(w.id)}">${escapeHtml(w.name || w.id)}</option>`).join('');
  openFormModal(lj('Add from ingest folder (2/2: choose destination)', '取り込みフォルダから追加（2/2: 追加先の選択）'), `
    <div class="form-group">
      <div class="form-hint" style="margin-bottom:8px;overflow-wrap:anywhere;word-break:break-all;" title="${escapeHtml(path)}">
        📂 ${escapeHtml(name)} — ${escapeHtml(_displaySourcePath(path))}
      </div>
      <label class="form-label">${bi('Destination workspace', '追加先ワークスペース')}</label>
      <label style="display:flex;align-items:center;gap:8px;margin:6px 0;cursor:pointer;">
        <input type="radio" name="add-src-dest" id="add-src-dest-existing" value="existing" checked
               onchange="_addSourceDestToggle()">
        <span>${bi('Add to an existing workspace', '既存のワークスペースに追加')}</span>
      </label>
      <input id="ws-filter-input" type="text" class="form-input" style="margin:0 0 6px 26px;width:calc(100% - 26px);"
             placeholder="${lj('Filter by workspace name', 'ワークスペース名で絞り込み')}" oninput="_addSourceWsFilter()">
      <select id="add-src-ws-sel" class="form-input" style="margin:0 0 10px 26px;width:calc(100% - 26px);">${wsOptions}</select>
      <label style="display:flex;align-items:center;gap:8px;margin:6px 0;cursor:pointer;">
        <input type="radio" name="add-src-dest" id="add-src-dest-new" value="new"
               onchange="_addSourceDestToggle()">
        <span>${bi('Create a new workspace', '新しいワークスペースを作成')}</span>
      </label>
      <input id="add-src-new-ws-name" class="form-input" style="margin:0 0 0 26px;width:calc(100% - 26px);display:none;"
             placeholder="${lj('New workspace name', '新しいワークスペース名')}">
    </div>
  `, lj('Add', '追加'), _addSourceExecute);
  if (!wsOptions) {
    // 既存WSが無い場合は新規作成側を既定選択
    const rNew = $('add-src-dest-new'); if (rNew) { rNew.checked = true; }
    _addSourceDestToggle();
  }
}

function _addSourceDestToggle() {
  const isNew = $('add-src-dest-new')?.checked;
  const sel = $('add-src-ws-sel'); const inp = $('add-src-new-ws-name');
  if (sel) sel.style.display = isNew ? 'none' : '';
  if (inp) inp.style.display = isNew ? '' : 'none';
  const flt = $('ws-filter-input');
  if (flt) flt.style.display = isNew ? 'none' : '';
  _addSourceWsFilterButtonSync();
}

// ws-filter-v1-20260706: 追加先プルダウンのキーワード絞り込み（フロント表示のみ・保存値/API不変）。
// 挙動は Workspaces 一覧の検索欄（filterWorkspaces/_applyWsFilterSort, state.js）と同型:
// toLowerCase + 部分一致 includes・trim なし。並び順は State.workspaces のまま（created_at DESC）。
function _addSourceWsFilter() {
  const sel = $('add-src-ws-sel'); if (!sel) return;
  const q = ($('ws-filter-input')?.value || '').toLowerCase();
  const prev = sel.value;
  const list = (State.workspaces || [])
    .filter(w => !q || (w.name || w.id || '').toLowerCase().includes(q));
  sel.innerHTML = list
    .map(w => `<option value="${escapeHtml(w.id)}">${escapeHtml(w.name || w.id)}</option>`).join('');
  if (prev && list.some(w => String(w.id) === String(prev))) sel.value = prev;
  _addSourceWsFilterButtonSync();
}

// 可視 option が 0 件のときだけ「追加」を押下不可にする（既存WSラジオ選択時のみ）。
function _addSourceWsFilterButtonSync() {
  const btn = $('form-modal-submit'); if (!btn) return;
  const isNew = $('add-src-dest-new')?.checked;
  const sel = $('add-src-ws-sel');
  btn.disabled = !isNew && !!sel && sel.options.length === 0;
}

async function _addSourceExecute() {
  const draft = window._addSrcDraft || {};
  if (!draft.name || !draft.path) { closeFormModal(); return; }
  const isNew = $('add-src-dest-new')?.checked;
  const wsSel = $('add-src-ws-sel');
  const newWsName = ($('add-src-new-ws-name')?.value || '').trim();
  if (isNew && !newWsName) { showToast(lj('Please enter a workspace name', 'ワークスペース名を入力してください'), 'warning'); return; }
  if (!isNew && !(wsSel && wsSel.value)) { showToast(lj('Please choose a workspace', 'ワークスペースを選んでください'), 'warning'); return; }
  try {
    // ① ソース作成（auto_scan 既定 true = バックグラウンドスキャン開始）
    const src = await API.post('/api/sources', { name: draft.name, path: draft.path });
    // ② 追加先へ結線。PUT の source_ids は全置換式のため、既存WSは現行 source_ids を取得して合成する
    let wsId;
    if (isNew) {
      const ws = await API.post('/api/workspaces', { name: newWsName });
      wsId = ws.id;
      await API.put(`/api/workspaces/${wsId}`, { source_ids: [src.id] });
    } else {
      wsId = wsSel.value;
      const cur = await API.get(`/api/workspaces/${wsId}`);
      const merged = Array.from(new Set([...(cur.source_ids || []), src.id]));
      await API.put(`/api/workspaces/${wsId}`, { source_ids: merged });
    }
    closeFormModal();
    showToast(lj(`Source "${draft.name}" added (scanning in background...)`, `ソース「${draft.name}」を追加しました（バックグラウンドでスキャン中...）`), 'success');
    window._addSrcDraft = null;
    if (typeof refreshAllData === 'function') { try { await refreshAllData(); } catch (e) {} }
    renderSources();
    // バックグラウンドスキャンの進捗反映ポーリング（addSource と同型）
    let polls = 0;
    const poll = setInterval(async () => {
      polls++;
      await renderSources();
      const fresh = State.sources.find(s => s.id === src.id);
      if (!fresh || fresh.status === 'completed' || fresh.status === 'failed' || polls > 10) {
        clearInterval(poll);
        if (fresh && fresh.status === 'completed') {
          showToast(lj(`Scan complete: ${fresh.file_count} files`, `スキャン完了: ${fresh.file_count}ファイル`), 'success');
        } else if (fresh && fresh.status === 'failed') {
          showToast(lj(`Scan failed: ${_displaySourcePath(fresh.path)}`, `スキャン失敗: ${_displaySourcePath(fresh.path)}`), 'error');
        }
      }
    }, 1000);
  } catch (e) { showToast(lj(`Add failed: ${e.message}`, `追加失敗: ${e.message}`), 'error'); }
}

function deleteSource(id) {
  // GUI修正2 #35: アーカイブ（論理削除）優先 → Settings の「アーカイブ済み」から復元・完全削除可能
  confirmAction(lj('Archive source', 'ソースをアーカイブ'),
    lj('This data source will be archived. You can restore it from “Archived” in Settings.', 'このData Sourceをアーカイブします。この操作はSettingsの「アーカイブ済み」から復元できます。'), '🗄️',
    async () => {
      try {
        await API.post(`/api/archived/source/${id}/archive`, {});
        showToast(lj('Source archived','ソースをアーカイブしました'), 'success');
        renderSources();
      } catch (e) { showToast(lj(`Archive failed: ${e.message}`,`アーカイブ失敗: ${e.message}`), 'error'); }
    });
}

// ga-close-v3 PartX: ファイル選択 handleFileSelect() と入口二択の表示切替 srcSetEntryMode() を撤去。
// どちらも参照する DOM (#src-entry-*, #ingest-upload-wrap) と CSS (.src-entry-hidden) が既に無く、
// 呼び出し元も0だった。
