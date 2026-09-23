// chat.js - Cynovela v13

function adjustChatFontSize(delta) {
  _chatFontSize = Math.max(11, Math.min(32, _chatFontSize + delta));
  const cm = document.getElementById('chat-messages');
  // E-3: CSS変数で子バブル (.chat-bubble 等) の font-size を駆動。
  //      style.fontSize 直接設定だと子要素の固定 CSS rule に override されていた。
  if (cm) cm.style.setProperty('--chat-font-size', _chatFontSize + 'px');
  try { localStorage.setItem('chat_font_size', String(_chatFontSize)); } catch (_) { /* */ }
}

function toggleChatFullscreen() {
  const pane = document.getElementById('page-chat');
  if (!pane) return;
  pane.classList.toggle('chat-fullscreen-mode');
}

function popoutChat() {
  if (typeof navigate === 'function') {
    try { navigate('chat'); } catch (_) { /* */ }
  }
}

// alpha §段4: toggleChatTemplates 撤去 (質問テンプレ自体を撤去したため不要)

async function renderChat() {
  // U-4: _maybeShowRoleSwitchBar() は撤去済 DOM (#role-switch-bar/#style-role-bar) 専用の
  // 死んだ関数だったため呼び出しを削除 (State の更新は行わない no-op だった)。
  // PHASE UI-7: マルチチャットタブを描画 (localStorage から復元)
  if (typeof renderChatTabs === 'function') renderChatTabs();
  // #05: ロールに応じて比較モードトグルの可視性を制御する
  _applyCompareModeVisibility();
  // #09 Layer 1: コンテキスト長を取得して残量バーを準備
  _ensureCtxLength().then(() => _renderCtxUsageBar()).catch(()=>{});
  // P5-A: 保存済みACLロールを復元（#04 で UI 削除済み — demoRole への復元のみ維持）
  try {
    const saved = localStorage.getItem('cynovela_acl_role') || '';
    if (saved) State.demoRole = saved;
  } catch {}
  // P6-E: モデルプリセットをロード（初回のみ・比較モードの #model-a/b-sel 用）
  loadModelPresets().catch(()=>{});
  // ragchat-single-source-20260628: 単一チャットの #provider-sel / #model-sel は Settings の
  //   保存設定 (単一の源) から引く。chat 入場ごとに呼び、ブラウザ更新後の Settings 変更を反映する。
  if (typeof _syncChatLlmFromSettings === 'function') _syncChatLlmFromSettings().catch(()=>{});
  // GUI修正3 #40: 質問テンプレート折りたたみ状態を sessionStorage から復元
  _restoreChatTemplatesState();
  // P1-6: localStorage から RAG モード復元
  ['normal','explain','developer'].forEach(m => {
    const btn = document.getElementById(`mode-btn-${m}`);
    if (btn) btn.classList.toggle('active', m === ragDisplayMode);
  });
  const sel = $('chat-ws-sel');
  const currentVal = sel.value;
  // BETA: selectable エンドポイントから取得（N+1ゼロ・全件返却）
  if (!State._selectableWS) {
    try {
      State._selectableWS = await API.get('/api/workspaces/selectable');
    } catch (e) {
      // DD-CYN-0089 §6-B: 読めないと選択肢が空になり、公開済みでも「選べるものが無い」
      //   ように見える。読めなかったことを出す。
      State._selectableWS = [];
      const _m = (e && e.message) || '';
      showToast(lj(`Could not read the list of workspaces: ${_m}`,
        `作業場所の一覧を読めませんでした: ${_m}`), 'error');
    }
  }
  const userWS = (State._selectableWS || []).filter(
    ws => ws.user_accessible && (ws.published_collections || 0) > 0
  );
  // BETA: 最近使った WS を上部に表示
  let recent = [];
  try { recent = JSON.parse(localStorage.getItem('cynovela_recent_ws') || '[]'); } catch {}
  const recentWS = userWS.filter(ws => recent.includes(ws.id))
    .sort((a, b) => recent.indexOf(a.id) - recent.indexOf(b.id));
  const otherWS  = userWS.filter(ws => !recent.includes(ws.id));
  let opts = '<option value="">— ' +
    (CYNOVELA_LANG === 'ja' ? '選択' : 'Select') + ' —</option>';
  if (recentWS.length) {
    opts += `<optgroup label="${lj('Recent', '最近使用')}">${
      recentWS.map(ws => `<option value="${ws.id}">${escapeHtml(ws.name)}</option>`).join('')
    }</optgroup>`;
    opts += `<optgroup label="${lj('All', 'すべて')}">${
      otherWS.map(ws => `<option value="${ws.id}">${escapeHtml(ws.name)}</option>`).join('')
    }</optgroup>`;
  } else {
    opts += otherWS.map(ws => `<option value="${ws.id}">${escapeHtml(ws.name)}</option>`).join('');
  }
  sel.innerHTML = opts;
  if (currentVal && userWS.some(w => w.id === currentVal)) sel.value = currentVal;
  onChatWSChange();
}

async function applyCompareBParams() {
  const body = {};
  _CMP_B_PARAM_KEYS.forEach(([elId, k]) => {
    const v = ($(elId)?.value ?? '').trim();
    body[k] = v;  // 空欄はクリアとして保存 → 第1モデルの設定を継承
  });
  await API.put('/api/settings', body);
}

// ga-close-v3 PartX: ドラッグ&ドロップ送信 handleFileDrop() を撤去 (UI-8 で D&D 廃止・以後 ondrop 属性は0件)。

// alpha §段4: renderQuestionTemplates / useTpl 撤去 (確定版 5/21、代替は §段6 推奨質問)

function appendChatMessage(role, content, id) {
  const msgs = $('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  if (id) div.id = id;
  const avatars = { user: State.user?.avatar || '👤', assistant: '🤖', system: '🔔', sources: '📄' };
  div.innerHTML = `<div class="chat-avatar">${avatars[role]||''}</div><div class="chat-bubble">${content}</div>`;
  msgs.appendChild(div);
  // P1 §5-6: コードブロックにツールバー (Copy/Download/Preview) を付与
  enhanceCodeBlocks(div);
  msgs.scrollTop = msgs.scrollHeight;
}

async function onProviderChange() {
  // ragchat-single-source-20260628: チャットのプロバイダーは Settings の保存設定に固定
  //   (#provider-sel は保存プロバイダーの単一表示=独立切替なし)。Settings から再同期する。
  if (typeof _syncChatLlmFromSettings === 'function') await _syncChatLlmFromSettings();
}

function onModelSelChange() {
  const modelSel = document.getElementById('model-sel');
  if (modelSel) {
    try { localStorage.setItem('cynovela_model_id', modelSel.value); } catch {}
  }
}

function onCompareModeToggle(on) {
  try { localStorage.setItem('cynovela_compare_on', on ? '1' : '0'); } catch {}
  const host = document.getElementById('model-b-host');
  const hint = document.getElementById('compare-hint');
  if (host) host.style.display = on ? 'inline-flex' : 'none';
  if (hint) hint.style.display = on ? 'block' : 'none';
  const hidden = document.getElementById('compare-mode-toggle'); if (hidden) hidden.checked = on;
  const panelModelB = document.getElementById('settings-compare-model-b');
  if (panelModelB) {
    panelModelB.style.display = on ? 'block' : 'none';
    if (on) {
      const src = document.getElementById('model-b-sel');
      const dst = document.getElementById('model-b-sel-settings');
      if (src && dst && dst.options.length === 0) {
        Array.from(src.options).forEach(o => dst.appendChild(o.cloneNode(true)));
        dst.value = src.value;
      }
    }
  }
}

function isCompareModeOn() {
  return document.getElementById('compare-mode-toggle')?.checked === true;
}

async function sendChatCompare(query, wsId, opts = {}) {
  const modelA = document.getElementById('model-a-sel')?.value || _llmPresets[0]?.id;
  const modelB = document.getElementById('model-b-sel')?.value || _llmPresets[1]?.id;
  // #06: 第2モデルが未設定のときは Settings へ誘導
  if (!modelB || _llmPresets.length < 2) {
    showToast(lj('Second model is not configured. Please set it in Settings','第2モデルが未設定です。Settings で設定してください'), 'warning');
    return;
  }
  if (!modelA) {
    showToast(lj('No comparison models selected','比較モデルが選択されていません'), 'warning');
    return;
  }
  if (modelA === modelB) {
    showToast(lj('Please pick two different models for comparison','比較には異なる2モデルを選んでください'), 'warning');
    return;
  }
  const body = { query, workspace_id: wsId, model_a: modelA, model_b: modelB, temperature: 0.1 };
  if (State.demoRole) body.role_override = State.demoRole;
  // FEATURE 3: 回答スタイルロール (admin/reader) — ACL とは独立
  if (State.styleRole) body.style_role = State.styleRole;
  // #03: 停止ボタン押下で両モデルとも中断するよう AbortSignal を渡す
  const result = await API.post('/api/chat/compare', body, { signal: opts.signal });
  return result;
}

function renderCompareResultsHtml(result) {
  const [r1, r2] = result.results || [];
  const t1 = (r1?.answer) || (r1?.error ? `${lj('Error', 'エラー')}: ${r1.error}` : '');
  const t2 = (r2?.answer) || (r2?.error ? `${lj('Error', 'エラー')}: ${r2.error}` : '');
  // #02: 比較モードの両カラムにも Markdown レンダリングを適用
  const aHtml = renderMarkdownAnswer(t1);
  const bHtml = renderMarkdownAnswer(t2);
  const card = (r, html) => `
    <div style="flex:1;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;min-width:240px;">
      <div style="font-size:16px;color:#64748b;margin-bottom:6px;">
        <strong style="color:#1e293b;">${escapeHtml(r?.label || r?.preset || '?')}</strong>
        ${r?.elapsed_ms != null ? ` <span style="color:#0369a1;">${(r.elapsed_ms/1000).toFixed(2)}${lj('s', '秒')}</span>` : ''}
      </div>
      <div style="font-size:18px;color:#1e293b;line-height:1.7;">${html || `<em style="color:#94a3b8;">${lj('No answer', '回答なし')}</em>`}</div>
    </div>`;
  return `
    <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:6px;">
      ${card(r1, aHtml)}
      ${card(r2, bHtml)}
    </div>
    <div style="font-size:16px;color:#64748b;margin-top:6px;">
      <span style="background:#d1fae5;color:#065f46;padding:1px 6px;border-radius:3px;">${lj('Green', '緑')}</span> ${lj('matching tokens', '一致トークン')}
      <span style="background:#fef3c7;color:#92400e;padding:1px 6px;border-radius:3px;margin-left:6px;">${lj('Yellow', '黄')}</span> ${lj('differing tokens', '食い違いトークン')}
    </div>`;
}

function toggleDebugPanel(btn) {
  const panel = btn.closest('.rag-debug-panel');
  const content = panel.querySelector('.debug-content');
  const isOpen = content.style.display !== 'none';
  content.style.display = isOpen ? 'none' : 'block';
  btn.textContent = isOpen ? lj('🔍 Show search details ▼', '🔍 検索詳細を見る ▼') : lj('🔍 Hide search details ▲', '🔍 検索詳細を閉じる ▲');
}

// Beta GA: ガバナンスサマリーバッジ（誰が・どのtierで・ガードレール/PII 状況）
function buildGovernanceBadge(meta) {
  if (!meta) return '';
  const parts = [];
  if (meta.tier === 'masked') {
    parts.push(`<span class="gov-badge" style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;padding:1px 8px;border-radius:10px;font-size:12px;">🔒 ${lj('Masked', 'マスク済み')}</span>`);
  } else if (meta.tier === 'raw') {
    parts.push(`<span class="gov-badge" style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;padding:1px 8px;border-radius:10px;font-size:12px;">👤 ${lj('Raw data', '生データ')}</span>`);
  }
  const gr = meta.guardrail_applied;
  const grActive = Array.isArray(gr) ? gr.length > 0 : !!gr;
  if (grActive) {
    parts.push(`<span class="gov-badge" style="background:#e0e7ff;color:#3730a3;border:1px solid #c7d2fe;padding:1px 8px;border-radius:10px;font-size:12px;">🛡️ ${lj('Guardrail applied', 'ガードレール適用')}</span>`);
  }
  let piiCount = 0;
  if (typeof meta.input_pii_count === 'number') piiCount = meta.input_pii_count;
  else if (Array.isArray(meta.input_pii)) piiCount = meta.input_pii.length;
  if (piiCount > 0) {
    parts.push(`<span class="gov-badge" style="background:#fee2e2;color:#991b1b;border:1px solid #fecaca;padding:1px 8px;border-radius:10px;font-size:12px;">⚠️ ${lj(`${piiCount} PII detected`, `PII ${piiCount}件検出`)}</span>`);
  }
  const role = meta.user_role || ((typeof State !== 'undefined' && State.user) ? State.user.role : '');
  if (role) {
    parts.push(`<span class="gov-badge" style="background:#f1f5f9;color:#334155;border:1px solid #e2e8f0;padding:1px 8px;border-radius:10px;font-size:12px;">${escapeHtml(String(role))}</span>`);
  }
  return parts.length > 0
    ? `<div class="governance-summary" style="margin-bottom:8px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;">${parts.join('')}</div>`
    : '';
}

function buildRagAnswerHtml(answer, retrievalDetail, meta = null) {
  const govBadge = buildGovernanceBadge(meta);
  let answerHtml = renderMarkdownAnswer(answer);
  // P5-C: [MASKED:TYPE] トークンをホバー説明付きの span に変換
  answerHtml = answerHtml.replace(/\[MASKED:(\w+)\]/g, (_m, type) => {
    // ga-close-v3 PartX: 伏字トークン名は型名と綴りが違うものがあるためここで対応付ける。
    //   2026-07-27 に増えた SSN / IBAN / PASSWORD / APIKEY / PRIVATEKEY を追加。
    //   表に無いトークンは state.js の型ラベル表 → それでも無ければトークン名そのまま。
    const labels = {
      EMAIL: lj('email address', 'メールアドレス'),
      PHONE: lj('phone number', '電話番号'),
      CREDIT: lj('credit card number', 'クレジットカード番号'),
      MYNUM: lj('My Number', 'マイナンバー'),
      PASSPORT: lj('passport number', 'パスポート番号'),
      SSN: lj('Social Security Number', '社会保障番号(米)'),
      IBAN: lj('IBAN', '国際銀行番号'),
      PASSWORD: lj('password', 'パスワード'),
      APIKEY: lj('API token', 'APIトークン'),
      PRIVATEKEY: lj('private key', '秘密鍵'),
      IP: lj('IP address', 'IPアドレス'),
      URL: 'URL'
    };
    const label = labels[type] || (typeof piiTypeLabel === 'function' ? piiTypeLabel(type) : type);
    return `<span class="pii-mask" title="${lj(`The output guardrail masked this ${label}`, `出力Guardrailで ${label} をマスクしました`)}"
            style="background:#fffbeb;color:#92400e;padding:1px 6px;border-radius:4px;
                   font-size:0.92em;border:1px solid #fde68a;cursor:help;">[MASKED:${type}]</span>`;
  });
  if (!retrievalDetail) return govBadge + answerHtml;
  // PHASE 1: 参照一致度はゲート判定と同じ vector_score (0〜1 のコサイン類似度) で 🟢🟡🔴 表示。
  // hybrid_score は RRF 統合スコア (上限 ~0.033) のため類似度判定には不適。
  const hitsHtml = (retrievalDetail.hits || []).map((h, i) => {
    const _vec = Number(h.vector_score) || 0;
    const _vecPct = (_vec * 100).toFixed(0);
    const sc = getScoreColor(_vec);
    return `
    <div class="debug-hit-item">
      <div style="display:flex;gap:8px;align-items:baseline;">
        <span style="font-weight:700;color:#374151;">${i + 1}. ${escapeHtml(h.source_doc || '')}</span>
        <span style="color:#9ca3af;font-size:16px;">${escapeHtml(h.chunk_id || '')}</span>
        ${h.pii_detected ? `<span style="color:#d97706;font-size:16px;">🔒${lj('PII replaced', 'PII置換済み')}</span>` : ''}
      </div>
      <div class="debug-score-bar">
        <span title="${lj(`Vector similarity (gate metric): ${_vec.toFixed(3)}`, `ベクター類似度 (ゲート指標): ${_vec.toFixed(3)}`)}">${sc.icon} <span style="color:${sc.color};font-weight:700;">${lj('Similarity', '類似度')}: ${_vecPct}%</span></span>
        <span title="${lj('RRF fusion score (sum of reciprocal ranks, max ~0.033). Not used for gating.', 'RRF 統合スコア (順位逆数和、上限 ~0.033)。ゲート判定には使わない')}">${lj('RRF fusion', 'RRF統合')}: ${h.hybrid_score}</span>
        <span>BM25: ${h.bm25_score}</span>
        ${(h.rerank_score !== null && h.rerank_score !== undefined && h.rerank_score !== 0) ? `<span>🔀 Rerank: <strong>${h.rerank_score}</strong></span>` : ''}
      </div>
      <div class="chunk-preview" style="margin-top:4px;">${escapeHtml(h.content_preview || '')}…</div>
    </div>`;
  }).join('');
  const timing = retrievalDetail.timing || {};
  const timingHtml = `<div class="debug-timing">
    <span>🔍 ${lj('Search', '検索')}: ${((timing.vector_ms || 0)/1000).toFixed(2)}${lj('s', '秒')}</span>
    <span>🤖 ${lj('Generation', '生成')}: ${((timing.llm_ms || 0)/1000).toFixed(2)}${lj('s', '秒')}</span>
    <span>⏱️ ${lj('Total', '合計')}: ${((timing.total_ms || 0)/1000).toFixed(2)}${lj('s', '秒')}</span>
    <span>${lj('Model', 'モデル')}: ${escapeHtml(retrievalDetail.model_id || '')}</span>
  </div>`;
  return govBadge + `${answerHtml}
    <div class="rag-debug-panel">
      <button class="debug-toggle" onclick="toggleDebugPanel(this)">${lj('🔍 Show search details ▼', '🔍 検索詳細を見る ▼')}</button>
      <div class="debug-content" style="display:none;">
        <div>
          <div class="debug-section-title">${lj(`Chunks used (${retrievalDetail.n_hits || 0})`, `使用したChunk (${retrievalDetail.n_hits || 0}件)`)}</div>
          ${hitsHtml}
        </div>
        <div>
          <div class="debug-section-title">${lj('Prompt sent to LLM', 'LLMに渡したプロンプト')}</div>
          <div class="debug-prompt-box">${escapeHtml(retrievalDetail.prompt_sent || '')}</div>
        </div>
        ${timingHtml}
      </div>
    </div>`;
}

async function triggerHandoff() {
  try {
    const summaryPrompt =
      'Please summarize this conversation in a structured format:\n' +
      '1. Main topic discussed\n' +
      '2. Key decisions or findings\n' +
      '3. Important context for continuing\n' +
      '4. Suggested next questions\n\n' +
      'Keep it concise — this summary will be injected into a new chat session.';
    showToast(
      (CYNOVELA_LANG === 'ja') ? '引き継ぎサマリーを生成中...' : 'Generating handoff summary...',
      'info'
    );
    const wsId = $('chat-ws-sel')?.value || '';
    const ragPreset = (typeof getRagPreset === 'function') ? getRagPreset() : 'standard';
    let summary = '';
    try {
      const res = await API.post('/api/chat', {
        query: summaryPrompt,
        workspace_id: wsId,
        preset: ragPreset,
      });
      summary = (res && res.answer) || '';
    } catch (e) {
      summary = '(summary generation failed: ' + (e && e.message || 'unknown') + ')';
    }
    const colId = (typeof getCurrentCollectionId === 'function')
      ? getCurrentCollectionId() : null;
    const handoffData = {
      summary: summary,
      collection_id: colId,
      workspace_id: wsId,
      rag_preset: ragPreset,
      timestamp: new Date().toISOString(),
    };
    try {
      // handoff-keyfix-20260711: 読取側 _readHandoff() は 'cynovela_handoff' を見るため
      // 書込キーを一致させる。従来は 'cynovela_handoff_context' へ書いており永久に注入されなかった。
      localStorage.setItem('cynovela_handoff', JSON.stringify(handoffData));
    } catch (_) { /* ignore */ }
    if (typeof addChatTab === 'function') {
      addChatTab({ inject_context: true });
    } else if (typeof resetCurrentSession === 'function') {
      resetCurrentSession();
    }
    showToast(
      // handoff-keyfix-20260711: 実挙動は「新タブに前セッション要約バナーを表示」。
      // 旧文言「コンテキストを注入しました」は自動挿入を誤示唆するため実態に沿って是正。
      (CYNOVELA_LANG === 'ja') ? '引き継ぎ完了 — 新しいタブに前セッションの要約を表示しました' : 'Handoff complete — previous session summary shown in the new tab',
      'success'
    );
  } catch (e) {
    showToast(lj('Handoff failed: ', '引き継ぎ失敗: ') + (e && e.message || 'unknown'), 'error');
  }
}

function useFollowup(question) {
  const input = $('chat-input');
  if (!input) return;
  input.value = question;
  input.focus();
  // fix-rag-ui-20260525: チップクリックは「即検索したい」が UX の自然な期待。
  // 旧仕様「自動送信は行わない (ユーザーに確認させる)」は破棄し、
  // disabled でない送信ボタンを自動クリックする。
  const sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn && !sendBtn.disabled) {
    sendBtn.click();
  }
}

function _syncRagDisplayModeUI(mode) {
  ['normal','explain','developer'].forEach(m => {
    const btn = document.getElementById(`mode-btn-${m}`);
    if (btn) btn.classList.toggle('active', m === mode);
  });
  const labelEl = document.getElementById('rag-mode-current');
  if (labelEl) {
    const labels = { normal: lj('📝 Standard mode', '📝 標準モード'), explain: lj('💡 Explain mode', '💡 解説モード'), developer: lj('🔬 Developer mode', '🔬 開発者モード') };
    labelEl.textContent = labels[mode] || labels.normal;
    labelEl.dataset.mode = mode;
  }
}

function getRagPreset() {
  return localStorage.getItem('rag_preset') || 'standard';
}

function setRagPreset(name) {
  if (!['lite', 'standard', 'hq', 'general'].includes(name)) return;
  localStorage.setItem('rag_preset', name);
  document.querySelectorAll('.rag-preset-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.preset === name);
  });
  const cur = document.getElementById('rag-preset-current');
  // P1 §5-2: 言語に応じた表示
  const labels = (CYNOVELA_LANG === 'ja') ? _RAG_PRESET_LABELS_JA : _RAG_PRESET_LABELS_EN;
  const descs  = (CYNOVELA_LANG === 'ja') ? _RAG_PRESET_DESCS_JA  : _RAG_PRESET_DESCS_EN;
  if (cur) cur.textContent = labels[name] || name;
  const desc = document.getElementById('rag-preset-desc');
  if (desc) desc.textContent = descs[name] || '';
}

function loadChatTabs() {
  try {
    const raw = localStorage.getItem(_chatTabStorageKey(_CHAT_TAB_KEY));
    if (!raw) return [{ id: `t-${Date.now()}`, name: lj('New chat', '新しいチャット'), wsId: null, sessionId: null }];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) && arr.length ? arr : [{ id: `t-${Date.now()}`, name: lj('New chat', '新しいチャット'), wsId: null, sessionId: null }];
  } catch (_) {
    return [{ id: `t-${Date.now()}`, name: lj('New chat', '新しいチャット'), wsId: null, sessionId: null }];
  }
}

function renderChatTabs() {
  const bar = document.getElementById('chat-tab-bar');
  if (!bar) return;
  const tabs = loadChatTabs();
  let activeId = _getActiveTabId();
  if (!tabs.find(t => t.id === activeId)) {
    activeId = tabs[0].id;
    saveChatTabs(tabs, activeId);
  }
  bar.innerHTML = tabs.map(t => {
    const active = t.id === activeId ? 'chat-tab-active' : '';
    const closeBtn = tabs.length > 1
      ? `<button class="chat-tab-close" onclick="event.stopPropagation();closeChatTab('${t.id}')" title="${lj('Close tab', 'タブを閉じる')}">×</button>` : '';
    const label = t.name || (t.wsId ? `WS ${t.wsId.slice(0, 6)}` : lj('New chat', '新しいチャット'));
    return `
      <div class="chat-tab ${active}" onclick="switchChatTab('${t.id}')" data-tab-id="${t.id}">
        <span class="chat-tab-label">${escapeHtml(label)}</span>
        ${closeBtn}
      </div>`;
  }).join('') + `
    <button class="chat-tab-add" onclick="addChatTab()" title="${lj('New chat', '新しいチャット')}">＋</button>`;
}

function addChatTab(opts) {
  const tabs = loadChatTabs();
  if (tabs.length >= _CHAT_TAB_MAX) {
    // 最古を閉じる
    tabs.shift();
  }
  const id = `t-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
  tabs.push({ id, name: lj('New chat', '新しいチャット'), wsId: null, sessionId: null });
  saveChatTabs(tabs, id);
  renderChatTabs();
  // チャット表示エリアをクリア
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.innerHTML = '';
  // session を切る
  if (typeof State !== 'undefined') State.sessionId = null;
  // handoff-keyfix-20260711: inject_context 指定時(引き継ぎ)は新タブに
  // 前セッション要約バナーを確実に描画する。従来は引数を無視していた。
  if (opts && opts.inject_context && typeof checkAndInjectHandoff === 'function') {
    checkAndInjectHandoff();
  }
}

function closeChatTab(tabId) {
  let tabs = loadChatTabs();
  if (tabs.length <= 1) return;  // 最後の1つは消さない
  tabs = tabs.filter(t => t.id !== tabId);
  let active = _getActiveTabId();
  if (active === tabId) active = tabs[0].id;
  saveChatTabs(tabs, active);
  renderChatTabs();
  switchChatTab(active);
}

function _readHandoff() {
  try {
    const s = localStorage.getItem('cynovela_handoff');
    if (!s) return null;
    const obj = JSON.parse(s);
    if (!obj || !obj.summary) return null;
    // 1 時間以上経過していたら破棄
    const age = Date.now() - new Date(obj.timestamp || 0).getTime();
    if (age > 3600000) {
      localStorage.removeItem('cynovela_handoff');
      return null;
    }
    return obj;
  } catch (_) { return null; }
}

function checkAndInjectHandoff() {
  const handoff = _readHandoff();
  if (!handoff) return;
  const host = document.getElementById('chat-messages');
  if (!host) return;
  // 既に表示済みなら何もしない
  if (host.querySelector('.handoff-banner')) return;
  const banner = document.createElement('div');
  banner.className = 'handoff-banner';
  banner.innerHTML = `
    <strong>
      <span class="en">💬 Previous session context available</span><span class="ja">💬 前のセッションの文脈があります</span>
    </strong>
    <div class="handoff-summary">${escapeHtml(handoff.summary)}</div>
    <div class="handoff-actions">
      <button class="btn btn-primary btn-sm" onclick="applyHandoff()">
        <span class="en">Continue from here</span><span class="ja">ここから続ける</span>
      </button>
      <button class="btn btn-sm" onclick="dismissHandoff()">
        <span class="en">Start fresh</span><span class="ja">新規で始める</span>
      </button>
    </div>`;
  host.prepend(banner);
}

function applyHandoff() {
  const handoff = _readHandoff();
  if (!handoff) return;
  // chat-input に文脈情報を簡潔に注入する (ユーザーが続きを書く前提)
  const inp = document.getElementById('chat-input');
  if (inp) {
    inp.placeholder = (CYNOVELA_LANG === 'ja')
      ? '前のセッションの続きを質問してください...'
      : 'Continue from the previous session...';
  }
  // ws/preset の復元 (空欄なら現状維持)
  if (handoff.workspace_id) {
    const ws = document.getElementById('chat-ws-sel');
    if (ws) {
      ws.value = handoff.workspace_id;
      if (typeof onChatWSChange === 'function') onChatWSChange();
    }
  }
  if (handoff.rag_preset && typeof setRagPreset === 'function') {
    setRagPreset(handoff.rag_preset);
  }
  showToast(
    (CYNOVELA_LANG === 'ja')
      ? '前のセッションの文脈を引き継ぎました'
      : 'Context from previous session applied',
    'success'
  );
  dismissHandoff();
}

function dismissHandoff() {
  try { localStorage.removeItem('cynovela_handoff'); } catch (_) { /* */ }
  const b = document.querySelector('.handoff-banner');
  if (b && b.parentNode) b.parentNode.removeChild(b);
}

function getPinnedWidgets() {
  try {
    return JSON.parse(localStorage.getItem(_PINNED_KEY) || '[]');
  } catch { return []; }
}

function togglePin(widgetId) {
  const pinned = getPinnedWidgets();
  const idx = pinned.indexOf(widgetId);
  if (idx >= 0) pinned.splice(idx, 1);
  else pinned.push(widgetId);
  try { localStorage.setItem(_PINNED_KEY, JSON.stringify(pinned)); } catch (_) { /* */ }
  renderMyDashboard();
  updatePinButtons();
}

// ===== Stage 3: DOMContentLoaded blocks moved from FIX app.js =====

// --- Block #11 (FIX app.js L9863-L9870) ---
document.addEventListener('DOMContentLoaded', () => {
  // fix-s3-2: 構造化回答モード ボタン トグル + Custom 欄展開
  document.querySelectorAll('.answer-mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.answer-mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const customArea = document.getElementById('custom-prompt-area');
      if (customArea) customArea.style.display = btn.dataset.mode === 'custom' ? 'block' : 'none';
    });
  });
});

// U-1: 詳細回答モード (Auto/Normal 以外の7モード) の折りたたみトグル
function toggleAdvancedAnswerModes() {
  const el = document.getElementById('answer-mode-advanced');
  if (!el) return;
  const hidden = (el.style.display === 'none' || !el.style.display);
  el.style.display = hidden ? 'inline-flex' : 'none';
  const tog = document.getElementById('answer-mode-more-toggle');
  if (tog) tog.textContent = hidden ? lj('Details ▴', '詳細 ▴') : lj('Details ▾', '詳細 ▾');
}

// fix062: Shift+Enter to send (Phase 3 split で app.js:6824-6830 から移植漏れだった handler を復元)
document.addEventListener('keydown', function(e) {
  if (e.target && e.target.id === 'chat-input' && e.key === 'Enter' && e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

// 入力欄 auto-grow（CSS max-height 120px 上限まで自動拡大、空になったらベースへ戻す）
function _resetChatInputHeight() {
  const el = document.getElementById('chat-input');
  if (!el) return;
  el.style.height = '';
}
window._resetChatInputHeight = _resetChatInputHeight;

// F2-B: 文字数カウンター。3500 字超で黄色、4000 字超で赤＋送信ボタン無効化。
const CHAT_MAX_CHARS = 4000;
function _updateChatCharCounter() {
  const el = document.getElementById('chat-input');
  const counter = document.getElementById('chat-char-counter');
  if (!el || !counter) return;
  const len = el.value.length;
  counter.textContent = `${len}/${CHAT_MAX_CHARS}${lj(' chars', '文字')}`;
  const sendBtn = document.getElementById('chat-send-btn');
  if (len > CHAT_MAX_CHARS) {
    counter.style.color = '#ef4444';
    if (sendBtn) sendBtn.disabled = true;
  } else if (len > 3500) {
    counter.style.color = '#f59e0b';
    if (sendBtn && !isSending) sendBtn.disabled = false;
  } else {
    counter.style.color = '#94a3b8';
    if (sendBtn && !isSending) sendBtn.disabled = false;
  }
}
window._updateChatCharCounter = _updateChatCharCounter;

document.addEventListener('input', function(e) {
  if (!(e.target && e.target.id === 'chat-input')) return;
  const el = e.target;
  el.style.height = 'auto';
  // CSS の max-height (120px) は overflow で受ける。scrollHeight を直接代入し、120px を超えたら scroll に任せる
  const next = el.scrollHeight;
  el.style.height = next + 'px';
  _updateChatCharCounter();
});

// 段G: SSE 受信ユーティリティ。POST /api/workspaces/{id}/chat/stream を fetch+ReadableStream で読み、
// data: 行をパースしてイベントごとに onEvent(obj) を呼ぶ。EventSource は GET 限定で本 EP（POST）には不可。
// 既存 sendChat() には接続しない（additive）。今後のサブステージで配線する。
async function sendChatStream(query, workspaceId, opts = {}) {
  const onEvent = opts.onEvent || (() => {});
  const onDone = opts.onDone || (() => {});
  const onError = opts.onError || (() => {});
  const body = {
    query,
    temperature: opts.temperature != null ? opts.temperature : 0.1,
    style_role: opts.styleRole || '',
  };
  // fix-s3-2: 構造化回答モード (SSE 経路)
  if (opts.answerMode) body.answer_mode = opts.answerMode;
  if (opts.answerMode === 'custom' && opts.customPrompt) body.custom_prompt = opts.customPrompt;
  // ga-finish-20260727 (Part2-3): SSE 経路にも RAG プリセット (lite/standard/hq) を送る。
  // 非ストリーム /api/chat と同じ body キー (preset)。バックエンドが未対応でも無害 (無視される)。
  try { body.preset = (typeof getRagPreset === 'function') ? getRagPreset() : 'standard'; } catch (e) { /* ignore */ }
  // ragchat-single-source-20260628: 単一チャットは Settings の保存設定 (get_current_adapter)
  //   を唯一の源とする。preset_id は送らず (独立プロバイダー切替なし=食い違い解消)、モデル選択のみ送る。
  //   backend は preset_id 無し時 get_current_adapter() の provider/endpoint/api_key を維持し、
  //   model のみ上書きする (routers/chat.py _chat_model_override)。
  const _modelSel = document.getElementById('model-sel');
  if (_modelSel?.value) body.model = _modelSel.value;
  const res = await fetch(`${API.base}/api/workspaces/${encodeURIComponent(workspaceId)}/chat/stream`, {
    method: 'POST',
    headers: API.headers(),
    body: JSON.stringify(body),
    signal: opts.signal,
  });
  if (res.status === 401) { API._handleSessionExpired(); const err = new Error('HTTP 401: Session expired'); err.status = 401; throw err; }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buf = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE: イベント区切りは "\n\n"
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of raw.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const json = line.slice(5).trim();
          if (!json) continue;
          try {
            const obj = JSON.parse(json);
            if (obj && obj.type === 'error') { onError(obj); }
            else if (obj && obj.type === 'done') { onEvent(obj); onDone(obj); }
            else { onEvent(obj); }
          } catch { /* malformed line — skip */ }
        }
      }
    }
  } catch (e) {
    onError({ type: 'error', message: e && e.message || String(e) });
    throw e;
  }
}
window.sendChatStream = sendChatStream;

// 段G 実配線: sendChat() から呼び分けるストリーミング UI 統合関数。
// localStorage.chat_streaming === '1' のときに state.js:sendChat から委譲される。
// 既存 chat-loading バブルの内側を「進捗ライン + 回答領域」に差し替え、
// stage event は進捗ラインに反映、token event は回答領域に追記、
// done で進捗を畳んで完了。citations / pipeline_detail は本実装の範囲外（次サブステージ）。
async function _sendChatStreamingUI(text, wsId, opts = {}) {
  const onFinally = opts.onFinally || (() => {});
  const STAGE_LABEL = {
    received: lj('Received', '質問受信'),
    rbac_filter: lj('ACL filtering', 'アクセス権確認'),
    pii_check: lj('PII check', '個人情報チェック'),
    guardrail: lj('Guardrail', 'ガードレール'),
    semantic_search: lj('Vector search', 'ベクトル検索'),
    keyword_search: lj('Keyword search', 'キーワード検索'),
    fusion: lj('Fusion', '結果統合'),
    retrieval: lj('Retrieval', '取得'),
    citations: lj('Citations', '引用整形'),
    llm_inference: lj('LLM generation', 'LLM 生成'),
    complete: lj('Complete', '完了'),
  };
  const loadingDiv = document.getElementById('chat-loading');
  const bubble = loadingDiv?.querySelector('.chat-bubble');
  if (!bubble) {
    // フォールバック: ローディングバブルが見つからない場合は新規追加
    appendChatMessage('assistant',
      '<div class="sse-progress" style="font-size:12px;color:#64748b;"></div><div class="sse-answer" style="margin-top:6px;"></div>',
      'chat-loading');
  } else {
    bubble.innerHTML =
      '<div class="sse-progress" style="font-size:12px;color:#64748b;line-height:1.6;"></div>' +
      '<div class="sse-answer" style="margin-top:6px;"></div>';
  }
  const progressEl = () => document.querySelector('#chat-loading .sse-progress');
  const answerEl = () => document.querySelector('#chat-loading .sse-answer');
  const stages = [];
  const renderStages = () => {
    const el = progressEl();
    if (!el) return;
    el.innerHTML = stages.map(s => `<span style="display:inline-block;padding:1px 6px;margin:1px 2px;border-radius:3px;background:${s.done?'#dcfce7':'#fef3c7'};color:${s.done?'#166534':'#92400e'};">${s.done?'✅':'⏳'} ${escapeHtml(s.label)}</span>`).join(' ');
  };
  let tokenText = '';
  let _lcSuggestions = null;  // B: SSE low_confidence で受け取る推奨質問
  // ga-close-v3 PartX: 低信頼度フォールバックの実測値 (max_score / threshold)。
  //   非ゼロなら「答えられなかった」回であり、本文は画面側の日英テンプレで描く。
  let _lcInfo = null;
  let _citations = null;      // fix-s2: SSE citations イベントを保持し done で描画
  let _retrievalPreviews = []; // provider3way-suggestq-20260629: retrieval イベントのチャンクプレビュー(フォローアップのコーパス照合に使う)
  try {
    await sendChatStream(text, wsId, {
      temperature: opts.temperature,
      styleRole: opts.styleRole,
      answerMode: opts.answerMode,
      customPrompt: opts.customPrompt,
      signal: opts.signal,
      onEvent: (obj) => {
        if (obj.type === 'stage') {
          // 直前の stage を done にして新規 stage を pending で push
          if (stages.length > 0) stages[stages.length - 1].done = true;
          stages.push({ label: STAGE_LABEL[obj.stage] || obj.stage, done: false });
          renderStages();
        } else if (obj.type === 'token') {
          // ga-close-v3 PartX: 低信頼度フォールバックのとき、サーバは英語の定型文を token で流す。
          //   非ストリーム経路 (state.js) は以前からこの英語に依らず画面側の日英テンプレを出していたが、
          //   既定 UI である SSE 経路だけが英語のまま画面に出ていた。ここでは定型文を積まず、
          //   done で同じテンプレを描く。サーバ側の文言そのものは本ランでは変更しない。
          if (_lcInfo) return;
          tokenText += String(obj.content || '');
          const ans = answerEl();
          if (ans) {
            // 安全のため escapeHtml を毎回かけて innerHTML 再設定
            ans.innerHTML = (typeof renderMarkdownSafe === 'function'
              ? renderMarkdownSafe(tokenText)
              : escapeHtml(tokenText).replace(/\n/g, '<br>'));
          }
        } else if (obj.type === 'retrieval') {
          // provider3way-suggestq-20260629: チャンクプレビューを保持し、成功時フォローアップの
          //   コーパス照合に渡す (既に届いている previews を再利用・新規取得はしない)。
          _retrievalPreviews = Array.isArray(obj.chunks)
            ? obj.chunks.map(c => (c && c.preview) || '').filter(Boolean) : [];
          // 取得チャンク件数だけ進捗に簡易表示
          const el = progressEl();
          if (el) {
            const tail = document.createElement('div');
            tail.style.fontSize = '11px';
            tail.style.color = '#94a3b8';
            tail.textContent = lj(`Retrieved ${obj.n_hits || 0} chunks`, `${obj.n_hits || 0} 件のチャンク取得`);
            el.appendChild(tail);
          }
        } else if (obj.type === 'low_confidence') {
          // B: 低信頼度フォールバックの推奨質問を受け取り、done でチップ描画する（非ストと同一中身）。
          _lcSuggestions = Array.isArray(obj.suggestions) ? obj.suggestions : [];
          // ga-close-v3 PartX: 本文テンプレ描画に使う実測値も保持する。
          _lcInfo = { max_score: obj.max_score || 0, threshold: obj.threshold || 0 };
        } else if (obj.type === 'citations') {
          // fix-s2: SSE 経路でも出典(citations)を保持し done で描画する
          //（backend chat.py:2382 が type:citations を送出・非スト result.citations と同一形）。
          _citations = Array.isArray(obj.citations) ? obj.citations : [];
        } else if (obj.type === 'reasoning') {
          const prog = bubble.querySelector('.sse-progress');
          if (prog && obj.content) {
            const det = document.createElement('details');
            det.className = 'reasoning-details';
            det.innerHTML = '<summary>Reasoning</summary><pre class="reasoning-pre"></pre>';
            det.querySelector('.reasoning-pre').textContent = obj.content;
            prog.appendChild(det);
          }
        } else if (obj.type === 'done') {
          // 全 stage を done にして、進捗を畳む
          stages.forEach(s => s.done = true);
          renderStages();
          const prog = progressEl();
          if (prog) {
            prog.style.opacity = '0.5';
            prog.style.fontSize = '11px';
          }
          // Beta GA: ガバナンスサマリーバッジを回答上部に常時表示
          const _ansEl = answerEl();
          if (_ansEl && typeof buildGovernanceBadge === 'function') {
            const _badge = buildGovernanceBadge(obj);
            if (_badge) _ansEl.insertAdjacentHTML('beforebegin', _badge);
          }
          // ga-close-v3 PartX: 答えられなかったときの本文を画面側で描く（非ストリーム state.js と同一文面）。
          if (_ansEl && _lcInfo) {
            const _pct = ((_lcInfo.max_score || 0) * 100).toFixed(0);
            const _thr = ((_lcInfo.threshold || 0) * 100).toFixed(0);
            _ansEl.innerHTML = `
              <div class="confidence-badge low" style="display:inline-block;padding:2px 8px;border-radius:4px;background:#fef2f2;color:#991b1b;font-size:12px;margin-bottom:6px;">
                <span class="en">Low confidence: ${_pct}% (threshold ${_thr}%)</span><span class="ja">低信頼度: ${_pct}% (閾値 ${_thr}%)</span>
              </div>
              <div>
                <span class="en">I could not find a reliable answer based on the available documents. The highest relevance score was ${_pct}%, below the threshold of ${_thr}%. Please try rephrasing your question or check if the relevant documents are published.</span><span class="ja">利用可能な資料の中から、根拠のある回答を見つけられませんでした。最も高い関連度は ${_pct}% (閾値 ${_thr}%) でした。質問を言い換えていただくか、関連する資料が公開されているかをご確認ください。</span>
              </div>`;
          }
          // B (流す方式・低信頼度サジェスト): SSE が low_confidence/suggestions を流すようになったため
          //   (routers/chat.py の SSE 移植)、低信頼度時はその推奨質問チップを描画し followups は出さない
          //   (非スト state.js:2253-2261 と同一中身: 低信頼度=suggestions / 成功=followups の排他)。
          if (_ansEl && Array.isArray(_lcSuggestions) && _lcSuggestions.length) {
            const _lcId = `lcsug-${Date.now()}`;
            const _chips = _lcSuggestions
              .map(q => `<button class="followup-chip" onclick="useFollowup(${escapeHtml(JSON.stringify(q))})">${escapeHtml(q)}</button>`)
              .join('');
            _ansEl.insertAdjacentHTML('afterend',
              `<div id="${_lcId}" class="followup-wrap"><div class="followup-title">${lj('Try one of these', 'こちらをお試しください')}</div><div class="followup-chips">${_chips}</div></div>`);
          } else if (_ansEl && typeof loadFollowupChips === 'function') {
            // C (流す方式): 成功回答(>=20字)に対し followups を取得して回答バブル末尾に挿入。
            // 非ストの state.js:2354-2369 と同じパターン。
            const _ansText = String(tokenText || '').trim();
            if (_ansText.length >= 20) {
              const _followupId = `followup-${Date.now()}`;
              _ansEl.insertAdjacentHTML('afterend', `<div id="${_followupId}"></div>`);
              // provider3way-suggestq-20260629: retrieval イベントのプレビューを優先し、
              //   無ければ citations[].chunk_preview で補完してコーパス照合に渡す。
              let _fuPrev = Array.isArray(_retrievalPreviews) ? _retrievalPreviews.slice() : [];
              if (!_fuPrev.length && Array.isArray(_citations)) {
                _fuPrev = _citations.map(c => (c && c.chunk_preview) || '').filter(Boolean);
              }
              // DD-CYN-0020 U-9: 取得できなかったときも黙って消さない。loadFollowupChips は
              //   必ず表示物 (チップ or 理由の一行) を返すので、そのまま差し込む。
              loadFollowupChips(_ansText, wsId, _fuPrev).then(html => {
                const _fel = document.getElementById(_followupId);
                if (_fel) _fel.innerHTML = html || '';
              }).catch(e => {
                const _fel = document.getElementById(_followupId);
                if (_fel) _fel.innerHTML = _followupNoticeHtml(
                  lj(`Next-question suggestions could not be fetched: ${(e && e.message) || ''}`,
                     `次の質問候補を取得できませんでした: ${(e && e.message) || ''}`));
              });
            }
          }
          // fix-s2 (citations/feedback): SSE 経路でも出典カードと良し悪しボタンを描画する。
          // backend は type:citations と done.message_id を送出済。非ストと同一レンダラ
          // (renderCitations / renderFeedbackButtons) を使用し、DOM ID・関数の新規定義はしない。
          // 二重描画防止に既存 .feedback-row を確認してから feedback を足す。
          if (_ansEl) {
            let _citeFb = '';
            if (Array.isArray(_citations) && _citations.length && typeof renderCitations === 'function') {
              _citeFb += renderCitations(_citations);
            }
            const _bubbleHasFb = !!(bubble && bubble.querySelector('.feedback-row'));
            if (!_bubbleHasFb && obj.message_id && typeof renderFeedbackButtons === 'function') {
              _citeFb += renderFeedbackButtons(obj.message_id);
            }
            if (_citeFb) _ansEl.insertAdjacentHTML('afterend', _citeFb);
          }
        }
      },
      onDone: () => { /* 最終処理は finally で */ },
      onError: (obj) => {
        const ans = answerEl();
        if (ans) ans.innerHTML = `<span style="color:#dc2626;">⚠ ${escapeHtml(obj.message || 'error')}</span>`;
      },
    });
  } finally {
    onFinally();
  }
}
window._sendChatStreamingUI = _sendChatStreamingUI;

// DD-CYN-0020 U-9: 次の質問候補 (フォローアップ) を「黙って消さない」形に置き換える。
//   従来 (workspace.js の同名関数) は、受け口が 403/失敗のとき catch で ''、0 枚のときも ''
//   を返し、呼び出し側も `if (html)` で握り潰していた。この 2 層のため、閲覧者には候補が
//   無表示かつ無告知で消えていた (受け口が管理者限定だったことが表面に出なかった)。
//   ここでは必ず何かを返す: 候補があればチップ、無ければ理由つきの一行を画面へ出す。
//   index.html の読み込み順は state.js → ui.js → workspace.js → chat.js のため、この宣言が
//   後勝ちで有効になり、流す方式 (chat.js) と流さない方式 (state.js) の両方に効く。
function _followupNoticeHtml(msg) {
  return `<div class="followup-wrap followup-note" style="margin-top:6px;font-size:12px;color:#6b7280;">` +
         `${escapeHtml(msg)}</div>`;
}

function _followupReasonText(reason) {
  const map = {
    answer_too_short:      ['the answer was too short', '回答が短いため'],
    llm_endpoint_not_local:['the LLM endpoint is not local', '回答を作るLLMの宛先が自マシン内ではないため'],
    llm_endpoint_unknown:  ['the LLM endpoint could not be determined', '回答を作るLLMの宛先を判定できなかったため'],
    circuit_breaker_open:  ['the LLM is temporarily unavailable', 'LLMが一時的に使えない状態のため'],
    llm_call_failed:       ['the LLM call failed', 'LLMの呼び出しに失敗したため'],
    llm_output_not_json:   ['the LLM output was not in the expected format', 'LLMの出力が期待した形式ではなかったため'],
    llm_output_parse_error:['the LLM output could not be parsed', 'LLMの出力を読み取れなかったため'],
    llm_returned_none:     ['the LLM produced no candidate', 'LLMが候補を作らなかったため'],
    filtered_by_corpus:    ['every candidate was dropped by the corpus check (no basis in the retrieved documents)',
                            '候補が資料との照合ですべて落ちたため (取得した資料に根拠が無い候補だった)'],
  };
  return map[reason] || null;
}

async function loadFollowupChips(answer, workspaceId, previews) {
  if (!answer || answer.length < 20) return '';
  let r = null;
  try {
    // provider3way-suggestq-20260629: 取得済みチャンクのプレビューを渡し、コーパスに根拠が
    //   無い候補(空振り)をサーバ側で落とす。未指定なら従来どおりフィルタ無し(後方互換)。
    const _previews = Array.isArray(previews) ? previews.filter(p => p && String(p).trim()) : [];
    r = await API.post('/api/chat/followups', { answer, workspace_id: workspaceId || '', previews: _previews });
  } catch (e) {
    const _m = (e && e.message) ? String(e.message) : '';
    return _followupNoticeHtml(lj(`Next-question suggestions could not be fetched: ${_m}`,
                                  `次の質問候補を取得できませんでした: ${_m}`));
  }
  const list = (r && r.followups) || [];
  if (!list.length) {
    const _reason = (r && (r.reason || r.error)) || '';
    // DD-CYN-0094 C: モデル不在は本回答側と同じ理由 (モデル名つき) をそのまま出す
    if (r && r.reason === 'model_not_found' && r.error) {
      return _followupNoticeHtml(lj(`No next-question suggestions: ${r.error}`,
                                    `次の質問候補はありません: ${r.error}`));
    }
    const _t = _followupReasonText(_reason);
    if (_t) {
      return _followupNoticeHtml(lj(`No next-question suggestions: ${_t[0]}.`,
                                    `次の質問候補はありません: ${_t[1]}。`));
    }
    return _followupNoticeHtml(lj(`No next-question suggestions${_reason ? ` (${_reason})` : ''}.`,
                                  `次の質問候補はありません${_reason ? `（${_reason}）` : ''}。`));
  }
  const chips = list.map(q => {
    // v3.5.0 Stage1 (B4②): pass via data attribute to avoid JSON double-escaping in the
    // onclick attribute (old JSON.stringify+escapeHtml broke JSON.parse on quotes/CJK).
    return `<button class="followup-chip" data-q="${escapeHtml(q)}" onclick="useFollowup(this.dataset.q)">${escapeHtml(q)}</button>`;
  }).join('');
  return `<div class="followup-chips">${chips}</div>`;
}
window.loadFollowupChips = loadFollowupChips;
