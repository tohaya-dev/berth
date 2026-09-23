// governance.js - Cynovela v13

// settings-unsaved-indicator-fix-20260628: LLM 主設定欄(#llm-provider/#llm-base-url/#llm-model)に
// 手入力したがまだ「💾 まとめて適用」していない状態を「未適用の変更があります」と表示する最小実装。
// 保存値を基準として保持し、欄の input/change で基準と比較してラベルを出し入れする。適用成功で基準を更新し消す。
let _llmSavedBaseline = null; // {provider, base_url, model} 保存値の基準。null=未ロード(常に非表示)
function _llmCurrentFormVals() {
  return {
    provider: ($('llm-provider') ? $('llm-provider').value : ''),
    base_url: ($('llm-base-url') ? $('llm-base-url').value.trim() : ''),
    model: ($('llm-model') ? $('llm-model').value.trim() : ''),
  };
}
function _updateLlmUnsavedNote() {
  const note = document.getElementById('llm-unsaved-note');
  if (!note) return;
  if (!_llmSavedBaseline) { note.style.display = 'none'; return; }
  const cur = _llmCurrentFormVals();
  const dirty = cur.provider !== _llmSavedBaseline.provider
    || cur.base_url !== _llmSavedBaseline.base_url
    || cur.model !== _llmSavedBaseline.model;
  note.style.display = dirty ? '' : 'none';
}
function _setLlmBaseline() {
  // 流し込み/保存直後の現在値を保存値の基準として確定し、ラベルを非表示にする。
  _llmSavedBaseline = _llmCurrentFormVals();
  _updateLlmUnsavedNote();
}
function _bindLlmUnsavedWatchers() {
  ['llm-provider', 'llm-base-url', 'llm-model'].forEach(id => {
    const el = document.getElementById(id);
    if (!el || el.dataset.unsavedBound === '1') return;
    el.dataset.unsavedBound = '1';
    el.addEventListener('input', _updateLlmUnsavedNote);
    el.addEventListener('change', _updateLlmUnsavedNote);
  });
}

function _renderPolicyRuleRow(idx, rule = {}) {
  const types = ['EMAIL', 'PHONE_JP', 'PHONE_LAND', 'CREDIT', 'MYNUMBER', 'IPV4', 'URL'];
  const actions = ['mask', 'exclude_from_rag', 'log_only', 'allow'];
  return `<div class="policy-rule-row" data-idx="${idx}" style="display:flex;gap:6px;margin-bottom:6px;align-items:center;">
    <select class="form-input policy-rule-classifier" style="flex:1;">
      ${types.map(t => `<option value="${t}" ${rule.classifier===t?'selected':''}>${t}</option>`).join('')}
    </select>
    <span style="color:#64748b;">→</span>
    <select class="form-input policy-rule-action" style="flex:1;">
      ${actions.map(a => `<option value="${a}" ${rule.action===a?'selected':''}>${a}</option>`).join('')}
    </select>
    <button type="button" class="btn btn-sm" onclick="this.closest('.policy-rule-row').remove()"
            style="background:#fff;border:1px solid #fecaca;color:#991b1b;">🗑</button>
  </div>`;
}

function openCreatePolicyModal() {
  _editingPolicyId = null;
  const title = document.getElementById('policy-modal-title');
  if (title) title.textContent = lj('Create Policy', 'ポリシー作成');
  const nameInput = document.getElementById('policy-modal-name');
  if (nameInput) nameInput.value = '';
  const activeInput = document.getElementById('policy-modal-active');
  if (activeInput) activeInput.checked = true;
  const rulesHost = document.getElementById('policy-modal-rules');
  if (rulesHost) rulesHost.innerHTML = _renderPolicyRuleRow(0, {});
  _openPolicyModal();
}

function addPolicyRule() {
  const host = document.getElementById('policy-modal-rules');
  if (!host) return;
  const idx = host.querySelectorAll('.policy-rule-row').length;
  host.insertAdjacentHTML('beforeend', _renderPolicyRuleRow(idx, {}));
}

function _openPolicyModal() {
  document.getElementById('policy-modal-overlay').style.display = 'block';
  document.getElementById('policy-modal').style.display = 'block';
}

function closePolicyModal() {
  document.getElementById('policy-modal-overlay').style.display = 'none';
  document.getElementById('policy-modal').style.display = 'none';
  _editingPolicyId = null;
}

async function loadPolicyMatrix() {
  const host = document.getElementById('policy-matrix-host');
  if (!host) return;
  host.innerHTML = '<div style="padding:14px;color:#94a3b8;">' + bi('Loading...', '読み込み中...') + '</div>';
  try {
    const data = await API.get('/api/policy-matrix');
    host.innerHTML = renderPolicyMatrix(data.matrix || {});
    // GUI修正 #15 #16: 動的に挿入した help-btn のツールチップを初期化
    if (typeof _initHelpTooltips === 'function') _initHelpTooltips();
  } catch (e) {
    host.innerHTML = `<div style="padding:14px;color:#ef4444;">${lj('Fetch failed','取得失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}

function renderPolicyMatrix(matrix) {
  const opt = (cur) => POLICY_ACTIONS.map(a =>
    `<option value="${a.v}" ${cur===a.v?'selected':''}>${a.label}</option>`
  ).join('');
  const rows = POLICY_ROLES.map(role => `
    <tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:8px 12px;font-weight:700;color:#1e293b;background:#f8fafc;">${escapeHtml(role)}</td>
      ${POLICY_TYPES.map(t => `
        <td style="padding:6px 8px;">
          <select data-role="${role}" data-type="${t}"
                  style="padding:5px 8px;border:1px solid #e2e8f0;border-radius:5px;font-size:16px;background:#fff;">
            ${opt(matrix[role]?.[t] || 'mask')}
          </select>
        </td>`).join('')}
    </tr>`).join('');
  return `
    <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin-bottom:18px;">
      <!-- GUI修正 #16: 解説文を ❓ ヘルプボタンに収納（常時表示やめ） -->
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <span style="font-size:17px;color:#475569;font-weight:600;">${bi('Matrix operations', 'マトリクス操作')}</span>
        <button class="help-btn" onclick="showHelp('policy_matrix', this)">?<span class="help-pop"></span></button>
      </div>
      <div style="overflow-x:auto;">
        <table id="policy-matrix-table" style="border-collapse:collapse;width:100%;font-size:17px;">
          <thead>
            <tr style="background:#f8fafc;">
              <th style="text-align:left;padding:8px 12px;color:#475569;">${bi('Role ＼ PII type', 'ロール ＼ PII種別')}</th>
              ${POLICY_TYPES.map(t => `<th style="text-align:center;padding:8px 12px;color:#475569;">${escapeHtml(t)}</th>`).join('')}
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div style="margin-top:12px;display:flex;gap:10px;justify-content:flex-end;">
        <button class="btn btn-sm" onclick="loadPolicyMatrix()" style="padding:8px 16px;font-size:17px;">${bi('Reset', 'リセット')}</button>
        <button class="btn btn-sm btn-primary" onclick="savePolicyMatrix()" style="padding:8px 22px;font-size:17px;">${bi('Save Matrix', 'マトリクスを保存')}</button>
      </div>
    </div>`;
}

async function savePolicyMatrix() {
  const matrix = {};
  POLICY_ROLES.forEach(r => { matrix[r] = {}; });
  document.querySelectorAll('#policy-matrix-table select[data-role]').forEach(sel => {
    matrix[sel.dataset.role][sel.dataset.type] = sel.value;
  });
  try {
    await API.put('/api/policy-matrix', { matrix });
    showToast(lj('Policy matrix saved','ポリシーマトリクスを保存しました'), 'success');
  } catch (e) {
    showToast(lj(`Save failed: ${e.message}`,`保存失敗: ${e.message}`), 'error');
  }
}

async function exportComplianceCsv() {
  // uifix v1 C (2026-05-24): 旧実装は <a href> 直接ダウンロードで Authorization ヘッダが
  // 付かず 401。fetch + Blob 経由に変えて token を載せる。
  try {
    const url = `${API.base}/api/compliance-report.csv`;
    const headers = {};
    if (API.token) headers['Authorization'] = `Bearer ${API.token}`;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'compliance-report.csv';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    showToast(lj('Compliance report CSV downloaded','コンプライアンスレポートCSVをダウンロードしました'), 'success');
  } catch (e) {
    showToast(lj(`Export failed: ${e.message}`,`エクスポート失敗: ${e.message}`), 'error');
  }
}

async function renderSettings() {
  // PHASE UI-1: Settings アコーディオンに既存カードを振り分ける
  initSettingsAccordion();
  // PHASE UI-1 / B-1: リモートアクセス情報を読み込む
  loadRemoteAccessSection().catch(()=>{});
  // PHASE UI-1 / B-4: 直近の処理ログ
  loadProcessingLogsSection().catch(()=>{});
  // PHASE UI-1 / X-5-5: ヘルスチェックサマリー
  loadHealthSummary().catch(()=>{});
  // PHASE UI-3: フィードバックダッシュボード
  loadFeedbackDashboard().catch(()=>{});
  // PHASE UI-5: チャンキングプリセットセレクター
  loadChunkingPresetsForSettings().catch(()=>{});
  // P6-A: MCPセクションの非同期ロード
  loadMcpSection().catch(()=>{});
  // FEATURE 1: システムプロンプトをロード
  loadSystemPrompt().catch(()=>{});
  // P4-5: PII検出モードを反映
  loadPiiMode().catch(()=>{});
  // GUI修正2 #35: アーカイブ済み一覧
  loadArchivedSection().catch(()=>{});
  // GUI修正2 #30: Settings本体の Chunking セクション
  loadChunkingMainSection().catch(()=>{});
  // DD-CYN-0089 §6-B: ここが try に入っていなかったため、読めないとこの行より後ろ
  //   (LLM・埋め込み・利用者・控え ほか) が丸ごと描かれないまま、画面には何も出なかった。
  //   読めなかったことを画面へ出し、描ける分は描く。
  try {
    State.settings = await API.get('/api/settings');
  } catch (e) {
    const _m = (e && e.message) || '';
    State.settings = State.settings || {};
    showToast(lj(`Could not read the settings: ${_m}`, `設定を読めませんでした: ${_m}`), 'error');
  }
  // FEATURE 8: 検索ヒット件数の読み込み
  try {
    const _nRes = State.settings && State.settings['retrieval.n_results'];
    const _el = document.getElementById('adv-retrieval-n');
    if (_el && _nRes) _el.value = _nRes;
  } catch (e) { /* ignore */ }
  // PHASE M-2 拡張: 画像処理モードを反映
  loadImageMode().catch(()=>{});
  // A-2: Legacy #set-endpoint/#set-model カード撤去に伴い、その renderSettings 反映を削除。
  //      正規経路（下の LLM Provider 設定 /api/settings/llm）に一本化済み。
  // Phase 2: LLM Provider 設定をロード
  try {
    const llm = await API.get('/api/settings/llm');
    // provider-default-url-20260627: コンテナ対応の既定 Base URL(単一定義)をフロントへ取り込む(B-2 共有)。
    if (typeof _llmDefaultBaseUrl !== 'undefined' && llm && llm.default_base_url) _llmDefaultBaseUrl = llm.default_base_url;
    $('llm-provider').value = llm.provider || 'lmstudio';
    $('llm-base-url').value = llm.base_url || '';
    $('llm-model').value = llm.model || '';
    $('llm-api-key-status').textContent = llm.api_key_set ? lj('✅ API key set', '✅ API key 設定済み') : lj('⚠️ API key not set', '⚠️ API key 未設定');
    // BETA: APIキー入力欄を masked / editable で再描画
    const _llmWrap = document.getElementById('llm-api-key-wrap');
    if (_llmWrap) _llmWrap.innerHTML = _renderApiKeyField(
      'llm-api-key', llm.api_key_set === true,
      lj('(this session only, not saved)', '(このセッションのみ・保存しません)'));
    // 設定ロード起因の呼び出しは保存済み Base URL を上書きしない(_llmSettingsLoading ガード)。
    if (typeof _llmSettingsLoading !== 'undefined') _llmSettingsLoading = true;
    try { onLlmProviderChange(); } finally { if (typeof _llmSettingsLoading !== 'undefined') _llmSettingsLoading = false; }
    // llmprovider-simplify-20260628: 保存 provider から二択(#llm-provider-mode)と表示の出し分けを同期
    //   (openai_compat→OpenRouter/鍵欄表示・lmstudio|ollama→ローカル/鍵欄隠す)。Base URL は触らない。
    if (typeof _syncLlmProviderModeUI === 'function') _syncLlmProviderModeUI();
    // settings-unsaved-indicator-fix-20260628: 流し込み後の値を基準として確定し、3欄の入力監視を張る。
    _bindLlmUnsavedWatchers();
    _setLlmBaseline();
  } catch (e) { /* ignore */ }
  // Phase 2 Step 2: Embedding 設定
  try {
    const emb = await API.get('/api/settings/embedding');
    $('emb-provider').value = emb.provider || 'local';
    $('emb-model').value = emb.model || '';
    $('emb-base-url').value = emb.base_url || '';
    const _embWrap = document.getElementById('emb-api-key-wrap');
    if (_embWrap) _embWrap.innerHTML = _renderApiKeyField(
      'emb-api-key', emb.api_key_set === true,
      lj('(only if env var CYNOVELA_EMBEDDING_API_KEY is absent)', '(環境変数 CYNOVELA_EMBEDDING_API_KEY が無い場合のみ)'));
    onEmbProviderChange();
    // mas-status-20260725: 外の口 (Mac Accelerator Service) の稼働状態と退避状態を表示する。
    //   退避中は目立つ警告にし「黙って遅くならない」を画面で担保する。
    const _accSt = document.getElementById('emb-accel-status');
    if (_accSt) {
      if (emb.provider === 'openai_compat') {
        const _fbActive = emb.fallback && emb.fallback.active;
        const _reach = emb.accelerator && emb.accelerator.reachable;
        if (_fbActive) {
          _accSt.textContent = lj(
            '⚠️ Accelerator unreachable — embeddings fell back to local (' + (emb.fallback.target || 'cpu') + ') since ' + (emb.fallback.since || ''),
            '⚠️ 外の口(アクセラレータ)に届かないため、埋め込みはローカル(' + (emb.fallback.target || 'cpu') + ')へ退避中です (' + (emb.fallback.since || '') + ' から)');
          _accSt.style.color = 'var(--warning, #b45309)';
        } else if (_reach) {
          const _dev = (emb.accelerator.detail && emb.accelerator.detail.device) || '';
          _accSt.textContent = lj(
            '✅ Accelerator connected' + (_dev ? ' (device: ' + _dev + ')' : ''),
            '✅ 外の口(アクセラレータ)接続中' + (_dev ? ' (device: ' + _dev + ')' : ''));
          _accSt.style.color = 'var(--success, #15803d)';
        } else {
          _accSt.textContent = lj(
            '⚠️ Accelerator not reachable — next embedding will fall back to local',
            '⚠️ 外の口(アクセラレータ)に到達できません。次回の埋め込みはローカルへ退避します');
          _accSt.style.color = 'var(--warning, #b45309)';
        }
        _accSt.style.display = '';
      } else {
        _accSt.style.display = 'none';
      }
      // §9-4: 索引の埋め込み識別との食い違い警告 (モデル版が索引作成時と違う場合)
      if (emb.identity && emb.identity.match === false) {
        _accSt.textContent = (_accSt.textContent ? _accSt.textContent + ' / ' : '') + lj(
          '🚫 Embedding identity mismatch: index=' + (emb.identity.stored ? emb.identity.stored.model + '@' + emb.identity.stored.revision : '?') + ' current=' + (emb.identity.current ? emb.identity.current.model + '@' + emb.identity.current.revision : '?') + ' — adding documents now would corrupt search ranking',
          '🚫 索引の埋め込み識別と現在の経路が食い違っています (索引=' + (emb.identity.stored ? emb.identity.stored.model + '@' + emb.identity.stored.revision : '?') + ' / 現在=' + (emb.identity.current ? emb.identity.current.model + '@' + emb.identity.current.revision : '?') + ')。このまま追加取り込みすると検索順位が壊れます');
        _accSt.style.color = 'var(--danger, #b91c1c)';
        _accSt.style.display = '';
      }
      // identity-unreachable-20260727: 口へ到達できず突き合わせが成立しなかった状態。
      // 従来はこの状態でも判定が「一致」に倒れており、画面には何も出なかった。
      else if (emb.identity && emb.identity.match === null
               && (emb.identity.current || {}).source === 'external_unreachable') {
        _accSt.textContent = (_accSt.textContent ? _accSt.textContent + ' / ' : '') + lj(
          '❓ Embedding identity could not be verified — the accelerator is unreachable, so the current route was never read. This is NOT a match.',
          '❓ 埋め込み識別を確認できません。外の口へ到達できず、現在の経路の識別を読み取れていません（「一致」ではありません）');
        _accSt.style.color = 'var(--warning, #b45309)';
        _accSt.style.display = '';
      }
    }
  } catch (e) { /* ignore */ }
  // #06: 比較モード 第2モデル / コンテキスト長 設定の復元
  try { await loadCompareBSettings(); } catch (e) {}
  // #06: 詳細設定 (モデルパラメータ + RAG設定) の復元
  try { await loadAdvancedSettings(State.settings); } catch (e) {}
  // Phase 2 Step 3-5 (Step毎に追加される予定の loader を呼び出す)
  try { if (typeof loadVectorStoreSettings === 'function') await loadVectorStoreSettings(); } catch (e) {}
  try { if (typeof loadClassifierSettings === 'function') await loadClassifierSettings(); } catch (e) {}
  try { if (typeof loadRerankerSettings === 'function') await loadRerankerSettings(); } catch (e) {}
  // BLOCK 2: ユーザー管理（adminのみエンドポイントが通る）
  if (State.user && State.user.role === 'admin') {
    try { await renderAdminUsers(); } catch (e) {}
    // BLOCK 3: バックアップ一覧
    try { await renderBackupList(); } catch (e) {}
  }
}

async function applyCompareBSettings() {
  const endpoint = ($('cmp-b-endpoint')?.value || '').trim();
  const model    = ($('cmp-b-model')?.value || '').trim();
  const ctxLen   = parseInt($('cmp-b-ctx-len')?.value || '0', 10) || 0;
  // Task 3: Provider 明示選択 (auto = 従来の URL 自動判定)
  const providerSel = ($('cmp-b-provider')?.value || 'auto').trim();
  const result   = $('cmp-b-result');
  if (!endpoint || !model) {
    if (result) {
      result.textContent = t('line3216');
      result.className = 'set-result error';
    }
    return;
  }
  try {
    // 既存のユーザー登録プロバイダーを取得し、compare_b を上書き登録する
    const presets = await API.get('/api/llm/presets');
    const existing = (presets.custom || []).filter(p => p.id !== 'compare_b');
    // Task 3: Provider が auto なら URL 自動判定、明示指定があればそれを優先
    const provider = (providerSel && providerSel !== 'auto')
      ? providerSel
      : (endpoint.includes('11434') ? 'openai_compat' : 'lmstudio');
    existing.push({
      id: 'compare_b',
      label: lj('Compare 2nd model', '比較 第2モデル'),
      provider,
      base_url: endpoint,
      model,
    });
    await API.put('/api/llm/providers', existing);
    // ctx_len は別キーで保存（DB settings）
    await API.put('/api/settings', { 'compare_b.ctx_len': String(ctxLen || '') });
    if (result) {
      result.textContent = t('line3237');
      result.className = 'set-result success';
    }
    // RAG Chat の model-b-sel を再構築
    _llmPresets = [];
    if (typeof loadModelPresets === 'function') await loadModelPresets();
  } catch (e) {
    if (result) {
      result.textContent = `❌ ${e.message}`;
      result.className = 'set-result error';
    }
  }
}

async function applyAdvancedSettings() {
  const result = $('adv-result');
  const body = {};
  _ADV_KEYS.forEach(([elId, k]) => {
    const v = ($(elId)?.value ?? '').trim();
    body[k] = v;  // 空欄はクリアとして保存
  });
  try {
    await API.put('/api/settings', body);
    if (result) {
      result.textContent = t('line3274');
      result.className = 'set-result success';
    }
  } catch (e) {
    if (result) {
      result.textContent = `❌ ${e.message}`;
      result.className = 'set-result error';
    }
  }
}

async function loadAdvancedSettings(settings) {
  // settings は呼び出し元から渡してもよいし、未指定なら取得する
  let s = settings;
  if (!s) {
    try { s = await API.get('/api/settings'); } catch { s = {}; }
  }
  _ADV_KEYS.forEach(([elId, k]) => {
    const v = s[k];
    if (v != null) {
      const el = $(elId);
      if (el) el.value = v;
    }
  });
}

async function applyAllLlmSettings() {
  const result = $('llm-master-result');
  if (result) {
    result.textContent = t('line3304');
    result.className = 'set-result';
  }
  const errors = [];
  // 1) 主設定 (provider / base_url / model / api_key)
  try { await applyLlmSettings(); } catch (e) { errors.push(lj(`LLM main settings: ${e.message}`, `LLM主設定: ${e.message}`)); }
  // 2) 比較第2モデル (空ならスキップ)
  try {
    const ep = ($('cmp-b-endpoint')?.value || '').trim();
    const md = ($('cmp-b-model')?.value || '').trim();
    if (ep && md) await applyCompareBSettings();
  } catch (e) { errors.push(lj(`Compare 2nd model: ${e.message}`, `比較第2モデル: ${e.message}`)); }
  // 3) 詳細設定 (Temperature / Top P / chunking 等) — 第1モデル用
  try { await applyAdvancedSettings(); } catch (e) { errors.push(lj(`Advanced settings: ${e.message}`, `詳細設定: ${e.message}`)); }
  // 3-2) GUI修正(2026-05-01) #5: 第2モデル用パラメータを保存 (空欄なら継承)
  try { await applyCompareBParams(); } catch (e) { errors.push(lj(`2nd model parameters: ${e.message}`, `第2モデルパラメータ: ${e.message}`)); }
  // 4) 1台目 ctx長
  try { await applyLlmCtxLen(); } catch (e) { errors.push(lj(`Context length: ${e.message}`, `コンテキスト長: ${e.message}`)); }

  if (result) {
    if (errors.length === 0) {
      result.textContent = t('line3325');
      result.className = 'set-result success';
    } else {
      result.textContent = lj(`⚠️ Partially failed: ${errors.join(' / ')}`, `⚠️ 一部失敗: ${errors.join(' / ')}`);
      result.className = 'set-result error';
    }
  }
}

async function loadCompareBSettings() {
  try {
    const presets = await API.get('/api/llm/presets');
    const compareB = (presets.presets || []).find(p => p.id === 'compare_b');
    if (compareB) {
      const ep = $('cmp-b-endpoint'); if (ep) ep.value = compareB.base_url || '';
      const md = $('cmp-b-model');    if (md) md.value = compareB.model    || '';
      // Task 3: 保存済み provider をプルダウンに復元 (未設定なら auto)
      const pv = $('cmp-b-provider'); if (pv) pv.value = compareB.provider || 'auto';
    }
    const settings = await API.get('/api/settings');
    const ctx = settings['compare_b.ctx_len'];
    if (ctx) {
      const el = $('cmp-b-ctx-len'); if (el) el.value = ctx;
    }
    const llmCtx = settings['llm.ctx_len'];
    if (llmCtx) {
      const el = $('llm-ctx-len'); if (el) el.value = llmCtx;
    }
    // GUI修正(2026-05-01) #5: 第2モデル個別パラメータの復元
    _CMP_B_PARAM_KEYS.forEach(([elId, k]) => {
      const v = settings[k];
      if (v != null && v !== '') {
        const el = $(elId);
        if (el) el.value = v;
      }
    });
  } catch (e) { /* ignore */ }
}

async function applyLlmCtxLen() {
  const v = parseInt($('llm-ctx-len')?.value || '0', 10) || 0;
  const result = $('llm-ctx-len-result');
  try {
    await API.put('/api/settings', { 'llm.ctx_len': String(v || '') });
    if (result) {
      result.textContent = t('line3388');
      result.className = 'set-result success';
    }
  } catch (e) {
    if (result) {
      result.textContent = `❌ ${e.message}`;
      result.className = 'set-result error';
    }
  }
}

async function applyLlmSettings() {
  // #09 Step F: モデル切替時に会話履歴がある場合は確認ダイアログを出す
  const newModel = $('llm-model').value.trim();
  const hasHistory = (() => {
    const cm = document.getElementById('chat-messages');
    return !!(cm && cm.children && cm.children.length > 0);
  })();
  if (hasHistory) {
    let currentModel = '';
    try {
      const cur = await API.get('/api/settings/llm');
      currentModel = cur.model || '';
    } catch {}
    if (newModel && newModel !== currentModel) {
      // v3.5.0 Stage1 (B3): 旧文言は実挙動と不一致だった (applyLlmSettings は
      // chat-messages / session_id をクリアしないため履歴は保持される)。正確な文言へ是正。
      const ok = confirm(lj(
        '🔄 Switching the model. The current conversation history is kept as-is, and the new model will be used for subsequent responses.\nContinue?',
        '🔄 モデルを切り替えます。現在の会話履歴はそのまま保持され、以降の応答に新しいモデルが使われます。\n続行しますか？'
      ));
      if (!ok) return;
    }
  }
  const _keyEl = $('llm-api-key');
  const _llmKey = _keyEl?.value || '';
  const body = {
    provider: $('llm-provider').value,
    base_url: $('llm-base-url').value.trim(),
    model: newModel,
  };
  // token-persist-fix-20260628: マスク表示(disabled '****')は未送信=「保持」。編集可能(未設定欄/「変更」後)
  //   なら値を送信し、空送信=「削除」を明示する(バックエンドは body の api_key 有無で 保持/設定/削除 を判定)。
  const _keyEditable = !!_keyEl && !_keyEl.disabled && _llmKey !== '****';
  if (_keyEditable) body.api_key = _llmKey;
  const result = $('llm-result');
  try {
    const res = await API.post('/api/settings/llm', body);
    result.textContent = lj(`✅ Applied (provider=${res.provider}, model=${res.model || '(auto)'})`, `✅ 適用完了 (provider=${res.provider}, model=${res.model || '(自動)'})`);
    result.className = 'set-result success';
    $('llm-api-key-status').textContent = res.api_key_set ? lj('✅ API key set', '✅ API key 設定済み') : lj('⚠️ API key not set', '⚠️ API key 未設定');
    // BETA: 保存後は masked 表示に戻す
    const _llmWrap2 = document.getElementById('llm-api-key-wrap');
    if (_llmWrap2) _llmWrap2.innerHTML = _renderApiKeyField(
      'llm-api-key', res.api_key_set === true,
      lj('(this session only, not saved)', '(このセッションのみ・保存しません)'));
    // ctx_length の再取得をトリガー
    _sessionStats.ctx_length = 0;
    // settings-reflect-cachebust-fix-20260628 (F2): 保存後に GET /api/settings/llm を読み直し、
    // サーバが永続/正規化した値でフォーム欄(#llm-provider/#llm-base-url/#llm-model)と
    // api_key_set 表示を描き直す。手入力直後≠保存済みの取り違えをその場で解消する。
    try {
      const saved = await API.get('/api/settings/llm');
      if (typeof _llmDefaultBaseUrl !== 'undefined' && saved && saved.default_base_url) _llmDefaultBaseUrl = saved.default_base_url;
      $('llm-provider').value = saved.provider || 'lmstudio';
      $('llm-base-url').value = saved.base_url || '';
      $('llm-model').value = saved.model || '';
      $('llm-api-key-status').textContent = saved.api_key_set ? lj('✅ API key set', '✅ API key 設定済み') : lj('⚠️ API key not set', '⚠️ API key 未設定');
      const _llmWrapR = document.getElementById('llm-api-key-wrap');
      if (_llmWrapR) _llmWrapR.innerHTML = _renderApiKeyField(
        'llm-api-key', saved.api_key_set === true,
        lj('(this session only, not saved)', '(このセッションのみ・保存しません)'));
      // 再描画起因の onLlmProviderChange が保存 Base URL を上書きしないようガード(renderSettings と同様)。
      if (typeof _llmSettingsLoading !== 'undefined') _llmSettingsLoading = true;
      try { onLlmProviderChange(); } finally { if (typeof _llmSettingsLoading !== 'undefined') _llmSettingsLoading = false; }
      // llmprovider-simplify-20260628: 保存値で再描画した後も二択と表示の出し分けを保存 provider に同期。
      if (typeof _syncLlmProviderModeUI === 'function') _syncLlmProviderModeUI();
    } catch (e) { /* 再描画失敗は保存自体には影響しない */ }
    // settings-unsaved-indicator-fix-20260628: 保存値で再描画した後の値を新しい基準として確定し、
    // 「未適用の変更があります」ラベルを消す。
    _setLlmBaseline();
    // settings-reflect F2: チャット上部のプロバイダー表示・モデル一覧を保存値へ同期する
    // (renderChat 時のみ走っていた _syncChatLlmFromSettings を保存時にも走らせる)。
    if (typeof _syncChatLlmFromSettings === 'function') {
      try { await _syncChatLlmFromSettings(); } catch (e) { /* チャット未描画時は no-op */ }
    }
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

async function applyEmbSettings() {
  const _embKey = $('emb-api-key')?.value || '';
  const body = {
    provider: $('emb-provider').value,
    model: $('emb-model').value.trim(),
    base_url: $('emb-base-url').value.trim(),
  };
  if (_embKey && _embKey !== '****') body.api_key = _embKey;
  const result = $('emb-result');
  try {
    const res = await API.post('/api/settings/embedding', body);
    result.textContent = lj(`✅ Applied (${res.provider} / ${res.model || ''})`, `✅ 適用完了 (${res.provider} / ${res.model || ''})`);
    result.className = 'set-result success';
    const _embWrap2 = document.getElementById('emb-api-key-wrap');
    if (_embWrap2) _embWrap2.innerHTML = _renderApiKeyField(
      'emb-api-key', res.api_key_set === true,
      lj('(only if env var CYNOVELA_EMBEDDING_API_KEY is absent)', '(環境変数 CYNOVELA_EMBEDDING_API_KEY が無い場合のみ)'));
    if (res.warning) showToast(res.warning, 'warning');
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

async function loadVectorStoreSettings() {
  const vs = await API.get('/api/settings/vector-store');
  $('vs-provider').value = vs.provider || 'chromadb';
  if (vs.url) $('vs-qdrant-url').value = vs.url;
  onVsProviderChange();
}

async function loadClassifierSettings() {
  const c = await API.get('/api/settings/classifier');
  $('cls-provider').value = c.provider || 'rule_based';
  if (c.api_url) $('cls-api-url').value = c.api_url;
  const _clsWrap = document.getElementById('cls-api-key-wrap');
  if (_clsWrap) _clsWrap.innerHTML = _renderApiKeyField(
    'cls-api-key', c.api_key_set === true,
    lj('(only if env var CYNOVELA_CLASSIFIER_API_KEY is absent)', '(環境変数 CYNOVELA_CLASSIFIER_API_KEY が無い場合のみ)'));
  onClsProviderChange();
}

async function applyClsSettings() {
  const _clsKey = $('cls-api-key')?.value || '';
  const body = {
    provider: $('cls-provider').value,
    api_url: $('cls-api-url').value.trim(),
  };
  if (_clsKey && _clsKey !== '****') body.api_key = _clsKey;
  const result = $('cls-result');
  try {
    const res = await API.post('/api/settings/classifier', body);
    result.textContent = lj(`✅ Applied (${res.provider})`, `✅ 適用完了 (${res.provider})`);
    result.className = 'set-result success';
    const _clsWrap2 = document.getElementById('cls-api-key-wrap');
    if (_clsWrap2) _clsWrap2.innerHTML = _renderApiKeyField(
      'cls-api-key', res.api_key_set === true,
      lj('(only if env var CYNOVELA_CLASSIFIER_API_KEY is absent)', '(環境変数 CYNOVELA_CLASSIFIER_API_KEY が無い場合のみ)'));
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

async function loadRerankerSettings() {
  const r = await API.get('/api/settings/reranker');
  $('rr-provider').value = r.provider || 'none';
  $('rr-model').value = r.model || '';
  $('rr-base-url').value = r.base_url || '';
  $('rr-topn').value = r.top_n || 5;
  const _rrWrap = document.getElementById('rr-api-key-wrap');
  if (_rrWrap) _rrWrap.innerHTML = _renderApiKeyField(
    'rr-api-key', r.api_key_set === true,
    lj('(only if env var CYNOVELA_RERANKER_API_KEY is absent)', '(環境変数 CYNOVELA_RERANKER_API_KEY が無い場合のみ)'));
  onRrProviderChange();
  // ga-finish-20260727: 再ランクの現在の実行場所/状態を表示する (埋め込みの mas-status と同型)。
  // 外の口へ届かないときの退避 (本体内 / 再ランクなし) は黙って挙動が変わらないよう画面へ出す。
  const _rrSt = document.getElementById('rr-accel-status');
  if (_rrSt) {
    if (r.provider === 'none') {
      _rrSt.textContent = lj('ℹ️ Rerank: disabled (results returned in search order)',
        'ℹ️ 再ランク: 無効 (検索順のまま返します)');
      _rrSt.style.color = 'var(--muted, #64748b)';
      _rrSt.style.display = '';
    } else if (r.provider === 'external_accelerator') {
      const _fbA = r.fallback && r.fallback.active;
      const _reachA = r.accelerator && r.accelerator.reachable;
      if (_fbA) {
        const _tgt = r.fallback.target || '';
        _rrSt.textContent = lj(
          '⚠️ Rerank: accelerator unreachable — falling back to ' + _tgt + ' since ' + (r.fallback.since || ''),
          '⚠️ 再ランク: 外の口(アクセラレータ)に届かないため退避中 — 経路=' + _tgt + ' (' + (r.fallback.since || '') + ' から)');
        _rrSt.style.color = 'var(--warning, #b45309)';
      } else if (_reachA) {
        const _rrDev = (r.accelerator.detail && (r.accelerator.detail.reranker_device || r.accelerator.detail.device)) || '';
        _rrSt.textContent = lj(
          '✅ Rerank: external accelerator connected' + (_rrDev ? ' (device: ' + _rrDev + ')' : ''),
          '✅ 再ランク: 外の口(アクセラレータ)で実行' + (_rrDev ? ' (device: ' + _rrDev + ')' : ''));
        _rrSt.style.color = 'var(--success, #15803d)';
      } else {
        _rrSt.textContent = lj(
          '⚠️ Rerank: accelerator not reachable — next rerank falls back (in-process if weights exist, otherwise no rerank)',
          '⚠️ 再ランク: 外の口(アクセラレータ)に到達できません。次回は退避します (重みがあれば本体内・無ければ再ランクなし)');
        _rrSt.style.color = 'var(--warning, #b45309)';
      }
      _rrSt.style.display = '';
    } else {
      _rrSt.textContent = lj('✅ Rerank: running in-process (' + (r.provider || '') + ')',
        '✅ 再ランク: 本体内で実行 (' + (r.provider || '') + ')');
      _rrSt.style.color = 'var(--success, #15803d)';
      _rrSt.style.display = '';
    }
  }
}

async function applyRrSettings() {
  const _rrKey = $('rr-api-key')?.value || '';
  const body = {
    provider: $('rr-provider').value,
    model: $('rr-model').value.trim(),
    base_url: $('rr-base-url').value.trim(),
    top_n: parseInt($('rr-topn').value) || 5,
  };
  if (_rrKey && _rrKey !== '****') body.api_key = _rrKey;
  const result = $('rr-result');
  try {
    const res = await API.post('/api/settings/reranker', body);
    result.textContent = lj(`✅ Applied (${res.provider}${res.model ? ` / ${res.model}` : ''})`, `✅ 適用完了 (${res.provider}${res.model ? ` / ${res.model}` : ''})`);
    result.className = 'set-result success';
    const _rrWrap2 = document.getElementById('rr-api-key-wrap');
    if (_rrWrap2) _rrWrap2.innerHTML = _renderApiKeyField(
      'rr-api-key', res.api_key_set === true,
      lj('(only if env var CYNOVELA_RERANKER_API_KEY is absent)', '(環境変数 CYNOVELA_RERANKER_API_KEY が無い場合のみ)'));
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

async function testRrConnection() {
  const result = $('rr-result');
  result.textContent = lj('🔍 Testing connection...', '🔍 接続テスト中...');
  result.className = 'set-result';
  try {
    const res = await API.post('/api/settings/reranker/test', {});
    if (res.status === 'connected' || res.status === 'configured' || res.status === 'ok') {
      result.textContent = `✅ ${res.status} (${res.provider})`;
      result.className = 'set-result success';
    } else if (res.status === 'warning' || res.status === 'not_implemented') {
      result.textContent = `⚠️ ${res.status} ${res.error || res.message || ''}`;
      result.className = 'set-result warning';
    } else {
      result.textContent = `❌ ${res.status} ${res.error || ''}`;
      result.className = 'set-result error';
    }
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
  }
}

async function testLlmConnection() {
  const result = $('llm-result');
  result.textContent = lj('🔍 Testing connection...', '🔍 接続テスト中...');
  result.className = 'set-result';
  try {
    // llmprovider-simplify-20260628: 接続テスト=入力直叩き。保存値ではなく画面入力 (provider/base/api_key/model)
    //   を送る。適用前でも OpenRouter+入力トークンで本物の接続テストができる。マスク '****' は送らない。
    const _keyEl = $('llm-api-key');
    const _formKey = (_keyEl && !_keyEl.disabled && _keyEl.value !== '****') ? (_keyEl.value || '') : '';
    const _body = {
      provider: $('llm-provider')?.value || 'lmstudio',
      base_url: ($('llm-base-url')?.value || '').trim(),
      model: ($('llm-model')?.value || '').trim(),
    };
    if (_formKey) _body.api_key = _formKey;
    const res = await API.post('/api/settings/test-connection', _body);
    if (res.status === 'connected') {
      result.textContent = lj(`✅ Connected (models=${res.models}${res.current_model ? `, current=${res.current_model}` : ''})`, `✅ 接続OK (models=${res.models}${res.current_model ? `, current=${res.current_model}` : ''})`);
      result.className = 'set-result success';
      // modelchat-ui-v3-20260628 spec1: 接続テスト成功を記録。クラウドはこの後のみモデル一覧取得可。
      if (typeof _markLlmConnTest === 'function') _markLlmConnTest(true);
    } else if (res.status === 'warning') {
      result.textContent = `⚠️ ${res.error || 'warning'}`;
      result.className = 'set-result warning';
      if (typeof _markLlmConnTest === 'function') _markLlmConnTest(false);
    } else {
      result.textContent = `❌ ${res.error || res.status}`;
      result.className = 'set-result error';
      if (typeof _markLlmConnTest === 'function') _markLlmConnTest(false);
    }
  } catch (e) {
    result.textContent = `❌ ${e.message}`;
    result.className = 'set-result error';
    if (typeof _markLlmConnTest === 'function') _markLlmConnTest(false);
  }
}

async function renderComplianceChecklist() {
  const host = document.getElementById('compliance-checklist');
  if (!host) return;
  let data;
  try {
    data = await API.get('/api/compliance/checklist');
  } catch (e) {
    host.innerHTML = '';
    return;
  }
  const items = (data && data.items) || [];
  // Bugfix: 各 li で icon + .en / .ja の両方を確実にレンダリング.
  const lis = items.map(it => {
    const icon = it.ok ? '✅' : '⚠️';
    const cls  = it.ok ? 'compliance-ok' : 'compliance-warn';
    const le = escapeHtml(it.label_en || it.id || '');
    const lj = escapeHtml(it.label_ja || it.id || '');
    return `<li class="${cls}">
      <span class="compliance-icon" aria-hidden="true">${icon}</span>
      <span class="compliance-text">
        <span class="en">${le}</span><span class="ja">${lj}</span>
      </span>
    </li>`;
  }).join('');
  // Bugfix-6: デフォルトで折りたたみ表示にする (<details>)
  const okCount = items.filter(i => i.ok).length;
  const total = items.length;
  host.innerHTML = `
    <details class="compliance-collapsible">
      <summary class="compliance-summary">
        <span aria-hidden="true">✅</span>
        <span class="en">Compliance Checklist (${okCount}/${total} OK)</span><span class="ja">コンプライアンスチェックリスト (${okCount}/${total} OK)</span>
        <span class="compliance-hint en"> — click to expand</span>
        <span class="compliance-hint ja"> — クリックで展開</span>
      </summary>
      <ul class="compliance-items">${lis}</ul>
    </details>`;
}

// A-2: loadModels() / testConnection() は Legacy #set-model カード専用だったため、カード撤去に伴い削除。
//      正規経路のモデル一覧取得は main.js の fetchLlmProviderModels(/api/llm/list-models) に一本化済み。

async function loadImageMode() {
  try {
    const s = await API.get('/api/settings');
    const sel = $('image-processing-mode');
    const mdl = $('image-vlm-model');
    if (sel && s && s.image_processing_mode) sel.value = s.image_processing_mode;
    if (mdl && s && s.image_vlm_model) mdl.value = s.image_vlm_model;
  } catch (e) { console.warn('loadImageMode failed:', e); }
}

function showAppSettings(ev) {
  // 既存ドロップダウンがあればトグル
  let dd = document.getElementById('app-settings-dropdown');
  if (dd) { dd.remove(); return; }
  const s = _getAppSettings();
  dd = document.createElement('div');
  dd.id = 'app-settings-dropdown';
  dd.className = 'app-settings-dropdown';
  // クリック元の⚙️ボタンの直下に配置
  const btn = ev?.currentTarget || (ev && ev.target) ||
              document.querySelector('button[onclick*="showAppSettings"]');
  const rect = btn?.getBoundingClientRect?.();
  if (rect) {
    dd.style.position = 'absolute';
    dd.style.top  = (rect.bottom + window.scrollY + 4) + 'px';
    dd.style.right = (window.innerWidth - rect.right) + 'px';
  }
  dd.innerHTML = `
    <div class="app-settings-row">
      <label>💬 ${bi('Conversation history to keep', '会話履歴の保持件数')}</label>
      <select id="p3-set-max-turns">
        ${[5,10,20,50,9999].map(n => {
          const lbl = (n === 9999) ? lj('All', '全件') : lj(`${n} turns`, `${n}件`);
          return `<option value="${n}" ${s.max_turns===n?'selected':''}>${lbl}</option>`;
        }).join('')}
      </select>
    </div>
    <div class="app-settings-actions">
      <button class="btn btn-primary btn-sm" onclick="saveAppSettings()">${bi('Save', '保存')}</button>
      <button class="btn btn-ghost btn-sm" onclick="document.getElementById('app-settings-dropdown')?.remove()">${bi('Close', '閉じる')}</button>
    </div>
    <hr style="margin:8px 0;border:none;border-top:1px solid #e2e8f0;">
    <div style="font-size:13px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;padding:4px 0 6px;">🔍 ${bi('RAG detail mode', 'RAG詳細モード')}</div>
    <div data-sg="rag-mode" style="display:flex;gap:4px;flex-wrap:wrap;">
      <button class="btn btn-sm btn-ghost" data-val="normal"    onclick="setRagDisplayMode('normal');_highlightSettingsBtn(this)">OFF</button>
      <button class="btn btn-sm btn-ghost" data-val="explain"   onclick="setRagDisplayMode('explain');_highlightSettingsBtn(this)">${bi('Explain','解説')}</button>
      <button class="btn btn-sm btn-ghost" data-val="developer" onclick="setRagDisplayMode('developer');_highlightSettingsBtn(this)">${bi('Developer','開発者')}</button>
    </div>
    <div style="font-size:13px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;padding:8px 0 6px;">🔤 ${bi('Font size', 'フォントサイズ')}</div>
    <div style="display:flex;gap:4px;">
      <button class="btn btn-sm btn-ghost" onclick="adjustChatFontSize(-1)">A−</button>
      <button class="btn btn-sm btn-ghost" onclick="adjustChatFontSize(1)">A+</button>
    </div>
    <div style="font-size:13px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;padding:8px 0 6px;">⚡ ${bi('RAG preset', 'RAGプリセット')}</div>
    <div data-sg="rag-preset" style="display:flex;gap:4px;flex-wrap:wrap;">
      <button class="btn btn-sm btn-ghost" data-val="lite"     onclick="setRagPreset('lite');_highlightSettingsBtn(this)">🚀 ${bi('Performance','パフォーマンス')}</button>
      <button class="btn btn-sm btn-ghost" data-val="standard" onclick="setRagPreset('standard');_highlightSettingsBtn(this)">⚖️ ${bi('Balanced','バランス')}</button>
      <button class="btn btn-sm btn-ghost" data-val="hq"       onclick="setRagPreset('hq');_highlightSettingsBtn(this)">🎯 ${bi('Quality','品質優先')}</button>
      <button class="btn btn-sm btn-ghost" data-val="general"  onclick="setRagPreset('general');_highlightSettingsBtn(this)">🌐 ${bi('General','一般知識')}</button>
    </div>
    <!-- #5 slimdown: 信頼度しきい値 (conf-threshold) UI は撤去。値は /api/settings の既定(0.40)を使用。 -->
    <!-- item3-3b (2026-05-23): 「Response Style」セクションは普段画面のロール切替バーと
         同じ誤ラベル (admin/editor/reader) のため歯車側からも撤去。ロールはログインロール
         基準で一本化 (スリム化決定 page_id 36694ef8-...814f-...)。本物の厳格度モード
         (厳密↔ブレスト) は未実装のため本タスクではタッチしない。 -->
    <div style="font-size:13px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;padding:8px 0 6px;">💫 ${bi('Streaming (experimental)','ストリーミング応答（実験的）')}</div>
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:14px;color:#334155;padding:2px 0;">
      <input type="checkbox" id="chat-streaming-toggle"
             ${(localStorage.getItem('chat_streaming') === '1') ? 'checked' : ''}
             onchange="try { localStorage.setItem('chat_streaming', this.checked ? '1' : '0'); } catch(_) {}"
             style="width:15px;height:15px;cursor:pointer;">
      <span>${bi('Show 9-stage progress + token stream','9段階の進捗とトークン逐次表示')}</span>
    </label>
    <!-- item3-3b (2026-05-23): 「Demo Mode」セクション (ロール切替 checkbox + 4 ボタン) を
         歯車から撤去。ロールはログインロール基準で一本化 (スリム化決定
         page_id 36694ef8-...814f-...)。デモロール切替は本来デモ専用 (--demo --mock) の
         BLOCK B-2 「ロール切替デモ」 WS で実演する想定であり、歯車から常時露出させない。 -->
    <hr style="margin:8px 0;border:none;border-top:1px solid #e2e8f0;">
    <div class="app-settings-actions">
      <button class="btn btn-danger btn-sm" style="width:100%;"
              onclick="document.getElementById('app-settings-dropdown')?.remove(); doLogout();">
        🚪 ${bi('Logout','ログアウト')}
      </button>
    </div>`;
  document.body.appendChild(dd);
  // 初期ハイライト: パネルを開いた時点の現在値を反映
  setTimeout(() => {
    const _ragMode   = (typeof ragDisplayMode !== 'undefined') ? ragDisplayMode : 'normal';
    const _ragPreset = (typeof getRagPreset === 'function') ? getRagPreset() : (localStorage.getItem('rag_preset') || 'standard');
    const _styleRole = (typeof State !== 'undefined' && State.styleRole) ? State.styleRole : '';
    const _demoRole  = (typeof State !== 'undefined' && State.demoRole) ? State.demoRole : '';
    // item3-3b: style-role / demo-role は歯車から撤去済 (data-sg セレクタが null になる
     // ため init 対象から除外)。
    [
      {sg: 'rag-mode',   val: _ragMode},
      {sg: 'rag-preset', val: _ragPreset},
    ].forEach(({sg, val}) => {
      if (val === null) return;
      const grp = dd.querySelector(`[data-sg="${sg}"]`);
      if (!grp) return;
      grp.querySelectorAll('button[data-val]').forEach(b => {
        const active = b.dataset.val === val;
        b.style.background   = active ? '#0f172a' : '';
        b.style.color        = active ? '#fff'    : '';
        b.style.borderColor  = active ? '#0f172a' : '';
      });
    });
  }, 0);
  // 外部クリックで閉じる
  setTimeout(() => {
    const onDocClick = (e) => {
      if (!dd.contains(e.target) && e.target !== btn) {
        dd.remove();
        document.removeEventListener('mousedown', onDocClick, true);
      }
    };
    document.addEventListener('mousedown', onDocClick, true);
  }, 0);
}

function saveAppSettings() {
  const dd = document.getElementById('app-settings-dropdown');
  const mt = parseInt(dd?.querySelector('#p3-set-max-turns')?.value || '5', 10) || 5;
  try {
    localStorage.setItem('cynovela_max_turns', String(mt));
  } catch {}
  if (dd) dd.remove();
  showToast(lj('Settings saved','設定を保存しました'), 'success');
}

// #5 slimdown: saveConfidenceThreshold / 信頼度しきい値 UI は撤去済み。
// 値は settings テーブルの既定 (0.40) を chat.py が読む。

function _highlightSettingsBtn(btn) {
  const parent = btn.parentElement;
  parent.querySelectorAll('button').forEach(b => {
    b.style.background = '';
    b.style.color = '';
    b.style.borderColor = '';
  });
  btn.style.background = '#0f172a';
  btn.style.color = '#fff';
  btn.style.borderColor = '#0f172a';
}

function _settingsCardSection(card) {
  // 既存カードの h3 テキストでセクションを判定する
  const title = (card.querySelector('h3')?.textContent || '').trim();
  // AI / LLM
  if (/LLM Provider|Embedding Provider|Reranker|LLMプロバイダー管理/.test(title)) return 'ai';
  // チャンキング / RAG (Settings本体の Chunking セクションは loadChunkingMainSection で動的注入される)
  if (/Chunking|チャンキング/.test(title)) return 'rag';
  // Vector Store はチャンキング系として扱う
  if (/Vector Store|Vector store|ベクター/.test(title)) return 'rag';
  // MCP
  if (/MCP|🧩/.test(title)) return 'mcp';
  // セキュリティ系
  if (/ユーザー管理|Classifier|PII検出|🛡️|バックアップ|アーカイブ/.test(title)) return 'security';
  // ログ / 診断 (MCPはここから除外)
  if (/ヘルスチェック|🔍|診断|処理ログ|フィードバック分析/.test(title)) return 'logs';
  // その他は AI へ
  return 'ai';
}

function initSettingsAccordion() {
  const page = document.getElementById('page-settings');
  if (!page) return;
  // 既に振り分け済みなら open/close 復元のみ
  const cards = page.querySelectorAll(':scope > .card.settings-card, :scope > details.card.settings-card');
  cards.forEach(card => {
    const sec = _settingsCardSection(card);
    const host = page.querySelector(`[data-section-host="${sec}"]`);
    if (host && card.parentElement !== host) host.appendChild(card);
  });
  // localStorage から開閉状態を復元
  const saved = (() => {
    try { return JSON.parse(localStorage.getItem('cynovela_settings_open_sections') || '["ai"]'); }
    catch (_) { return ['ai']; }
  })();
  page.querySelectorAll('.settings-acc').forEach(acc => {
    const key = acc.dataset.accKey;
    if (key === 'ai') return; // デフォルトで開く
    acc.open = saved.includes(key);
    acc.addEventListener('toggle', () => {
      const open = Array.from(page.querySelectorAll('.settings-acc[open]'))
        .map(a => a.dataset.accKey);
      localStorage.setItem('cynovela_settings_open_sections', JSON.stringify(open));
    });
  });
  // ai セクションも toggle イベント登録
  const ai = page.querySelector('[data-acc-key="ai"]');
  if (ai && !ai._uiToggleBound) {
    ai._uiToggleBound = true;
    ai.addEventListener('toggle', () => {
      const open = Array.from(page.querySelectorAll('.settings-acc[open]'))
        .map(a => a.dataset.accKey);
      localStorage.setItem('cynovela_settings_open_sections', JSON.stringify(open));
    });
  }
}

async function loadRemoteAccessSection() {
  const host = document.getElementById('remote-access-host');
  if (!host) return;
  try {
    const info = await API.get('/api/settings/remote-access');
    const ts = info.tailscale_ip ? `<div>TailScale IP: <code>${escapeHtml(info.tailscale_ip)}</code></div>` : '';
    const ts_url = info.url_tailscale
      ? `<div style="margin-top:6px;">${bi('Access URL (TailScale):', 'アクセスURL (TailScale):')} <code id="remote-tsurl">${escapeHtml(info.url_tailscale)}</code>
         <button class="btn btn-sm" onclick="navigator.clipboard.writeText(document.getElementById('remote-tsurl').textContent)">📋 ${bi('Copy', 'コピー')}</button></div>` : '';
    const allow = info.active_allowlist && info.active_allowlist.length
      ? `<div>${bi('Allowed subnets:', '許可サブネット:')} ${info.active_allowlist.map(s => `<code>${escapeHtml(s)}</code>`).join(' / ')}</div>` : '';
    host.innerHTML = `
      <div style="font-size:17px;line-height:1.7;">
        <div>${bi('Bind:', 'バインド:')} <code>${escapeHtml(info.host)}:${info.port}</code></div>
        <div>${bi('Access URL (local):', 'アクセスURL (ローカル):')} <code id="remote-localurl">${escapeHtml((typeof window !== 'undefined' && window.location && window.location.origin) || info.url_localhost)}</code>
          <button class="btn btn-sm" onclick="navigator.clipboard.writeText(document.getElementById('remote-localurl').textContent)">📋 ${bi('Copy', 'コピー')}</button></div>
        ${ts}
        ${ts_url}
        ${allow}
        <div style="margin-top:8px;color:#94a3b8;font-size:16px;">
          ${bi('To enable external access, restart with', '外部アクセスを有効化するには')} <code>--allow-tailscale</code> ${bi('or', 'または')} <code>--allow-subnet x.y.z.w/N</code>${bi('.', ' 付きで再起動してください。')}
        </div>
      </div>`;
  } catch (e) {
    host.innerHTML = `<div style="color:#ef4444">${bi('remote-access fetch failed', 'remote-access 取得失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadChunkingPresets() {
  if (_chunkingPresets) return _chunkingPresets;
  try {
    const data = await API.get('/api/settings/presets');
    _chunkingPresets = data.chunking || [];
  } catch (e) {
    console.warn(t('line7419'), e);
    _chunkingPresets = [];
  }
  return _chunkingPresets;
}

// ===== Stage 3: DOMContentLoaded blocks moved from FIX app.js =====

// --- Block #9 (FIX app.js L8987-L8990) ---
document.addEventListener('DOMContentLoaded', () => {
  const cur = getRagPreset();
  setRagPreset(cur);
});
