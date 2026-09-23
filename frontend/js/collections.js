// collections.js - Cynovela v13

function onWsActionCatalog(wsId) {
  // SPAなのでハッシュではなく既存の navigate() を使い、Collections ページに遷移する
  // (Collection 一覧は既に WS 別表示の動的フィルタを持っていないため、Collection ページへ単純遷移)
  navigate('collections');
  // 将来的にWS絞り込みフィルタを実装する場合はここで適用する
  setTimeout(() => {
    // ヒント Toast
    showToast(lj('Navigated to Collections page','Collections ページに遷移しました'), 'info');
  }, 100);
}

async function createCollection() {
  const name = $('new-col-name').value.trim();
  const wsId = $('new-col-ws').value;
  const access = $('new-col-access').value;
  // P5-A: ACLロール選択
  const aclRoles = [...document.querySelectorAll('#col-acl-roles input:checked')].map(i => i.value);
  if (!name) return showToast(lj('Please enter a name','名前を入力してください'), 'warning');
  if (!aclRoles.length) return showToast(lj('Please select at least one ACL role','少なくとも1つのACLロールを選択してください'), 'warning');

  // S-5: アクティブタブを判定して送信内容を切り替え
  const isClassifyTab = document.getElementById('col-tab-classify')?.classList.contains('active');
  let body;
  if (isClassifyTab) {
    const filter = [...document.querySelectorAll('#col-classify-list input:checked')].map(i => i.value);
    if (!filter.length) return showToast(lj('Please select at least one category','カテゴリを1つ以上選択してください'), 'warning');
    body = {
      name, workspace_id: wsId, classification_filter: filter, file_ids: [],
      access_level: access, allowed_roles: aclRoles,
    };
  } else {
    const fileIds = [...document.querySelectorAll('#col-file-checks input:checked')].map(i => i.value);
    body = {
      name, workspace_id: wsId, file_ids: fileIds,
      access_level: access, allowed_roles: aclRoles,
    };
  }
  try {
    await API.post('/api/collections', body);
    closeFormModal();
    showToast(lj(`Collection "${name}" created`,`Collection「${name}」を作成しました`), 'success');
    await refreshAllData();
    renderCollections();
  } catch (e) { showToast(lj(`Create failed: ${e.message}`,`作成失敗: ${e.message}`), 'error'); }
}

function _startPublishPoll(colId, jobId, options = {}) {
  // 既に同じ collection の polling が動いていたら何もしない (二重起動防止)
  if (_publishPolls[colId]) return;
  const intervalId = setInterval(() => _pollPublishJob(colId, jobId), 2000);
  _publishPolls[colId] = {
    jobId, intervalId, lastStage: null,
    lastProgress: 0, lastProgressAt: Date.now(),
    options,
  };
  // 即時1回 fetch (2秒待たずに進捗反映を始める)
  _pollPublishJob(colId, jobId);
}

function deleteCollection(id) {
  // GUI修正2 #35: アーカイブ優先（即時削除は完全削除のみ）
  confirmAction(lj('Archive Collection','Collectionをアーカイブ'),
    lj('This Collection will be archived. It will be excluded from RAG search, but you can restore it from "Archived" in Settings.\n\nNote: permanent deletion cannot be undone.','このCollectionをアーカイブします。RAG検索対象から除外されますが、Settingsの「アーカイブ済み」から復元できます。\n\nなお完全削除すると復元不可になります。'),
    '🗄️', async () => {
    try {
      await API.post(`/api/archived/collection/${id}/archive`, {});
      showToast(lj('Collection archived','Collectionをアーカイブしました'), 'success');
      await refreshAllData();
      renderCollections();
    } catch (e) { showToast(lj(`Archive failed: ${e.message}`,`アーカイブ失敗: ${e.message}`), 'error'); }
  });
}

function showPublishSummaryCard(data) {
  // SSE done event: {chunk_count, pii_count, excluded_count, elapsed_seconds, file_count, classification_summary, collection_id, ...}
  const chunks   = parseInt(data.chunk_count || 0, 10);
  // ga-close-v3 PartX: 伏字件数は数え直さない。サーバの唯一の口 publish-summary の
  //   pii_count (= 伏字が当たった塊数) をそのまま出す。以前は内訳(pii_labels)を画面で
  //   足し合わせて別の単位(伏字の総件数)を「PII検出」として出していたため、同じ資料でも
  //   画面ごとに数が食い違っていた。
  const pii      = parseInt(data.pii_count || 0, 10);
  const piiTotal = pii;
  const excluded = parseInt(data.excluded_count || 0, 10);
  const elapsed  = Number(data.elapsed_seconds || 0);
  const fileCnt  = parseInt(data.file_count || 0, 10);
  // Collection 名を State から取得（completion modal に表示）
  const _col = data.collection_id
    ? (State.collections || []).find(c => c.id === data.collection_id)
    : null;
  const colName = data.collection_name || (_col && _col.name) || '';
  // P5-B: 分類サマリー
  const cls      = data.classification_summary || {sensitivity:{}, doc_type:{}, department:{}};
  // フェーズ2: Contextual Chunking
  const ctxCount = parseInt(data.contextual_count || 0, 10);
  const ctxSample = (data.contextual_sample || '').toString();
  // vision-placeholder-warn-20260727: 中身が1文字も入らなかったファイル。
  // 従来はサーバログにしか出ず、画面は正常な受領書のまま成功に見えていた。
  const phCount = parseInt(data.placeholder_only_count || 0, 10);
  const phFiles = Array.isArray(data.placeholder_only_files) ? data.placeholder_only_files : [];

  // マスキング ラベル別内訳。publish-summary EP の実集計のみを表示（捏造しない）。
  // ga-close-v3 PartX: 画面側の許可リスト(5種)で絞るのをやめ、返ってきたキーを全部出す。
  //   5種だけを数えていた頃は URL / IPV4 / PASSPORT / SSN / IBAN / 資格情報しか
  //   当たっていない塊が画面上 0 件として落ちていた。型名は増える前提で書く。
  const _piiLabels = data.pii_labels || null;
  const _labelRows = _piiLabels
    ? Object.keys(_piiLabels)
        .filter((k) => parseInt(_piiLabels[k] || 0, 10) > 0)
        .sort((a, b) => (parseInt(_piiLabels[b], 10) || 0) - (parseInt(_piiLabels[a], 10) || 0))
        .map((k) => [k, `${piiTypeIcon(k)} ${escapeHtml(piiTypeLabel(k))}`])
        .map(([k, disp]) => `
          <div style="display:flex;justify-content:space-between;align-items:center;
                      background:#fff;border:1px solid #fde68a;border-radius:6px;padding:6px 12px;">
            <span style="font-size:16px;color:#92400e;">${disp}</span>
            <span style="font-size:16px;color:#92400e;font-weight:700;">${parseInt(_piiLabels[k] || 0, 10)}${bi(' items',' 件')}</span>
          </div>`)
        .join('')
    : '';
  const maskingBreakdownHtml = `
    <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;">
      <div style="font-size:17px;color:#334155;font-weight:700;margin-bottom:8px;">
        🧩 ${bi('Ingestion process breakdown','取り込み処理の内訳')}
      </div>
      <div style="font-size:15px;color:#475569;line-height:1.8;">
        ${bi('① Load: ingested','① 読み込み:')} ${fileCnt > 0 ? `${fileCnt}${bi(' files',' ファイル')}` : bi('target files','対象ファイル')}${bi('','を取り込み')}<br>
        ${bi('② Guardrail masking: detected and masked personal information','② ガードレール伏字: 個人情報を検出してマスキング')}${_piiLabels ? '' : bi(' (breakdown shown from next Publish onward)','（内訳は次回 Publish 以降に表示）')}<br>
        ${bi('③ Conversion for search: indexed','③ 検索用への変換:')} ${chunks} ${bi('chunks','チャンクを索引化')}${excluded > 0 ? bi(` (${excluded} excluded by policy)`,`（ポリシーで ${excluded} 件除外）`) : ''}
      </div>
      ${_labelRows ? `
        <div style="margin-top:10px;display:flex;flex-direction:column;gap:6px;">
          <div style="font-size:15px;color:#92400e;font-weight:600;">${bi('Masked labels and counts','マスキングしたラベルと件数')}</div>
          ${_labelRows}
        </div>` : ''}
      <div style="margin-top:10px;font-size:14px;color:#64748b;background:#eef2ff;
                  border:1px solid #c7d2fe;border-radius:6px;padding:8px 10px;">
        🔐 ${bi('The original is stored encrypted and can only be decrypted by authorized administrators.','原本は暗号化して保管され、権限を持つ管理者のみが復号できます。')}
        ${bi('Masking operations are recorded in the audit log.','マスキング処理は監査ログに記録されます。')}
      </div>
    </div>`;

  const card = (label, value, sub, palette) => `
    <div style="background:${palette.bg};border:1px solid ${palette.border};border-radius:10px;
                padding:14px 16px;text-align:center;min-width:120px;flex:1;">
      <div style="font-size:26px;font-weight:800;color:${palette.fg};line-height:1.1;">${value}</div>
      <div style="font-size:16px;color:${palette.fg};margin-top:6px;font-weight:600;">${label}</div>
      ${sub ? `<div style="font-size:16px;color:${palette.fg};opacity:0.7;margin-top:2px;">${sub}</div>` : ''}
    </div>`;

  // V3.5.0: 受領書(選んだ値=ガバナンス証跡)。IngestViz 経由のときのみ表示。
  const _rcpt = data._receipt || null;
  const receiptHtml = _rcpt ? `
    <div style="background:#f0fdfa;border:1px solid #99f6e4;border-radius:8px;padding:12px 14px;">
      <div style="font-size:17px;color:#0f766e;font-weight:700;margin-bottom:6px;">🧾 ${bi('Receipt (record of this ingestion)','受領書（この取り込みの記録）')}</div>
      <div style="font-size:15px;color:#334155;line-height:1.8;">
        ${_rcpt.collection ? `${bi('Collection','コレクション')}: <b>${escapeHtml(_rcpt.collection)}</b><br>` : ''}
        ${_rcpt.folder ? `${bi('Source','取り込み元')}: ${escapeHtml(_displaySourcePath(_rcpt.folder))}<br>` : ''}
        ${_rcpt.qualityLabel ? `${bi('Ingestion quality','取り込み品質')}: <b>${escapeHtml(_rcpt.qualityLabel)}</b><br>` : ''}
        ${bi('Applied policy','適用ポリシー')}: <b>${escapeHtml(_rcpt.policyLabel || lj('Default / Workspace settings','既定 / ワークスペース設定'))}</b><br>
        ${bi('Recorded at','記録時刻')}: ${escapeHtml((_rcpt.ts || '').replace('T',' ').slice(0,19))}
      </div>
    </div>` : '';

  const grey   = {bg:'#f8fafc', border:'#e2e8f0', fg:'#475569'};
  const green  = {bg:'#f0fdf4', border:'#bbf7d0', fg:'#15803d'};
  const amber  = {bg:'#fffbeb', border:'#fde68a', fg:'#92400e'};
  const purple = {bg:'#fdf4ff', border:'#e9d5ff', fg:'#7e22ce'};
  const blue   = {bg:'#f0f9ff', border:'#bae6fd', fg:'#0369a1'};

  const html = `
    <div style="display:flex;flex-direction:column;gap:14px;">
      <div style="font-size:18px;color:#15803d;font-weight:700;">
        📦 ${bi('Published','')}${colName ? `<strong style="color:#1e293b;">${escapeHtml(colName)}</strong> ` : ''}${fileCnt > 0 ? `(${fileCnt}${bi(' files','ファイル')}) ` : ''}${bi('','を Publish しました')}
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        ${card(bi('Chunks','チャンク数'), chunks, null, green)}
        ${card(bi('PII detected','PII検出'), piiTotal, piiTotal > 0 ? bi('Auto-masked','自動マスク') : bi('None detected','検出なし'), piiTotal > 0 ? amber : grey)}
        ${card(bi('RAG excluded','RAG除外'), excluded, excluded > 0 ? bi('Policy applied','ポリシー適用') : '—', excluded > 0 ? purple : grey)}
        ${card(bi('Elapsed time','所要時間'), `${elapsed.toFixed(1)}s`, null, blue)}
      </div>
      ${phCount > 0 ? `
        <div style="font-size:16px;color:#991b1b;background:#fef2f2;border:2px solid #fca5a5;
                    border-radius:8px;padding:12px;">
          <div style="font-weight:700;margin-bottom:6px;">
            ⚠ ${bi(`${phCount} file(s) were indexed without their contents.`,`${phCount} ファイルは中身が取り込まれていません。`)}
          </div>
          ${bi('The image processing mode is none / filename_only, so only the file names entered the index. Set it to lm_studio / caption in Settings and publish again.','画像処理モードが none / filename_only のため、ファイル名だけが索引に入りました。設定の画像処理モードを lm_studio / caption にして取り込み直してください。')}
          ${phFiles.length ? `<div style="margin-top:8px;font-family:monospace;font-size:14px;
                        background:#fff;border:1px solid #fecaca;border-radius:4px;padding:8px;
                        white-space:pre-wrap;word-break:break-all;">${phFiles.map(f => escapeHtml(String(f))).join('<br>')}</div>` : ''}
        </div>` : ''}
      ${pii > 0 ? `
        <div style="font-size:16px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;
                    border-radius:8px;padding:10px;">
          🛡️ ${bi(`Detected PII in ${pii} chunks.`,`${pii}件のチャンクから PII を検出しました。`)}
          ${bi('It has been automatically processed according to policy — it will not be directly included in RAG answers.','ポリシーに従って自動処理されています — RAGの回答に直接含まれることはありません。')}
        </div>` : ''}
      ${receiptHtml}
      ${maskingBreakdownHtml}
      ${ctxCount > 0 ? `
        <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:12px;">
          <div style="font-size:17px;color:#0369a1;font-weight:700;margin-bottom:6px;">
            📝 ${bi('Contextual Chunking applied','Contextual Chunking 適用')}: ${ctxCount} ${bi('chunks','チャンク')}
          </div>
          ${ctxSample ? `
            <div style="font-size:16px;color:#475569;background:#fff;padding:8px 10px;
                        border-radius:4px;border:1px solid #e2e8f0;font-family:monospace;
                        white-space:pre-wrap;word-break:break-all;">
              ${escapeHtml(ctxSample)}…
            </div>
          ` : ''}
          <div style="font-size:16px;color:#94a3b8;margin-top:6px;">
            ${bi('Each chunk was embedded with its file name, type, sensitivity, department, and position prepended.','各チャンクの冒頭にファイル名・種別・感度・部門・位置情報を付加して埋め込みされました。')}
          </div>
        </div>` : `
        <div style="font-size:16px;color:#64748b;background:#f8fafc;padding:8px 12px;border-radius:6px;">
          📝 ${bi('Contextual Chunking: Disabled (can be enabled from Settings)','Contextual Chunking: 無効（Settings から有効化できます）')}
        </div>`}
      ${(Object.keys(cls.sensitivity||{}).length || Object.keys(cls.doc_type||{}).length) ? `
        <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:14px 16px;">
          <div style="font-size:18px;color:#0369a1;font-weight:700;margin-bottom:6px;">
            📂 ${bi('Metadata Engine classification result','メタデータエンジン分類結果')}
          </div>
          <div style="font-size:16px;color:#475569;margin-bottom:10px;">
            ${fileCnt > 1 ? bi(`${fileCnt} files were automatically classified into the following categories:`,`${fileCnt} 件のファイルが以下のカテゴリに自動分類されました：`) : bi('This file was automatically classified into the following categories:','このファイルは以下のカテゴリに自動分類されました：')}
          </div>
          <div style="display:grid;grid-template-columns:auto 1fr;gap:6px 14px;font-size:17px;">
            ${_renderMetaCategoryRow(lj('Sensitivity level','感度レベル'), cls.sensitivity, _sensitivityLabels(), fileCnt)}
            ${_renderMetaCategoryRow(lj('Document type','ドキュメント種別'), cls.doc_type, _docTypeLabels(), fileCnt)}
            ${Object.keys(cls.department||{}).filter(d => d && d !== '—').length > 0
              ? _renderMetaCategoryRow(lj('Target department','対象部門'), cls.department, {}, fileCnt, true)
              : ''}
          </div>
        </div>` : ''}
      <div style="display:flex;justify-content:flex-end;">
        <button class="btn btn-primary publish-close-btn" id="publish-close-btn" onclick="closeP3Modal()" style="padding:10px 28px;font-size:18px;font-weight:600;">
          ✅ ${bi('Close','閉じる')}
        </button>
      </div>
    </div>`;
  showP3Modal(lj('✅ Publish complete','✅ Publish 完了'), html, {lockBgClose: true});
}

async function renderDashboardRow1(summary, collections) {
  const host = document.getElementById('dashboard-row1');
  if (!host) return;

  // AI Readiness スコア計算（summary フィールド名に合わせる）
  const totalFiles = summary.total_files || 0;
  const chunks = summary.total_chunks || 0;
  const vecChunks = summary.vectorized_chunks || chunks;
  // ga-close-v3 PartX: pii_unreviewed_count は DEPRECATED (サーバ側で
  //   pii_detections_total の写し = 「レビュー」という実体が無い)。読むのをやめ、
  //   実測値2つ (伏字が当たった塊数 / 伏字の総件数) だけを使う。
  const piiTotal = summary.pii_detections_total || 0;
  const maskedSpans = summary.masked_spans_total || 0;
  const wsWithoutPolicy = summary.ws_without_policy_count || summary.ws_without_policy || 0;
  const totalWs = summary.total_workspaces || summary.workspaces || 1;
  const vectorRate = chunks > 0 ? Math.round((vecChunks / chunks) * 100) : 0;
  // ga-close-v3 PartX: 「対処率」は測っていないので計算できない。実際に測れているのは
  //   「伏字が当たった塊があるのに伏字スパンが1件も記録されていない」= 伏字が効いていない、
  //   という失敗形の有無。これを保護の点にする (検出0なら満点)。
  const piiScore = piiTotal > 0 ? (maskedSpans > 0 ? 100 : 0) : 100;
  const policyScore = totalWs > 0 ? Math.round((1 - wsWithoutPolicy / totalWs) * 100) : 100;
  const aiScore = Math.round(vectorRate * 0.5 + piiScore * 0.3 + policyScore * 0.2);
  const scoreColor = aiScore >= 70 ? '#22c55e' : aiScore >= 40 ? '#f59e0b' : '#ef4444';

  // Pipeline ドット + 機能バッジ
  const classified = summary.classified_files || 0;
  const todayQ = summary.total_queries_today || 0;
  const features = summary.features || {};
  const pipelineItems = [
    {
      label: 'Ingest',
      value: lj(`${totalFiles.toLocaleString()} files・${vectorRate}% complete`,`${totalFiles.toLocaleString()} files・${vectorRate}% 完了`),
      color: '#22c55e',
      badge: null,
    },
    {
      label: 'Classify',
      value: lj(`${classified}/${totalFiles} classified`,`${classified}/${totalFiles} 分類済み`),
      color: classified < totalFiles ? '#f59e0b' : '#22c55e',
      badge: features.metadata_engine !== false ? { text: lj('Metadata ON','メタデータON'), bg: '#dcfce7', fg: '#166534' } : { text: lj('Metadata OFF','メタデータOFF'), bg: '#fef2f2', fg: '#991b1b' },
    },
    {
      label: 'Guard',
      value: lj(`${totalWs - wsWithoutPolicy}/${totalWs} WS applied`,`${totalWs - wsWithoutPolicy}/${totalWs} WS 適用済み`),
      color: wsWithoutPolicy > 0 ? '#f59e0b' : '#22c55e',
      badge: features.data_guardrails !== false ? { text: 'Guardrail ON', bg: '#dcfce7', fg: '#166534' } : { text: 'Guardrail OFF', bg: '#fef2f2', fg: '#991b1b' },
    },
    {
      label: 'Query',
      value: lj(`${todayQ} queries today`,`本日 ${todayQ} クエリ`),
      color: '#22c55e',
      badge: features.session_history !== false ? { text: lj('History ON','履歴ON'), bg: '#dcfce7', fg: '#166534' } : { text: lj('History OFF','履歴OFF'), bg: '#fef2f2', fg: '#991b1b' },
    },
  ];

  // 最終Publish時刻と直近24h取り込み（summaryから取得）
  const lastPubRaw = summary.last_publish_at || summary.last_scan_time || summary.last_scanned_at || '';
  const lastPubLabel = lastPubRaw ? new Date(lastPubRaw).toLocaleString(CYNOVELA_LANG === 'ja' ? 'ja-JP' : 'en-US', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }) : '—';
  const recent24h = summary.ingest_24h || summary.recent_ingested_24h || summary.files_added_24h || 0;
  const scanHtml = `
    <div style="margin-top:10px;padding-top:8px;border-top:1px solid #e2e8f0;">
      <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
        <span style="font-size:11px;color:#9ca3af;">${bi('Last Publish','最終Publish')}</span>
        <span style="font-size:11px;font-weight:500;color:#6b7280;">${escapeHtml(lastPubLabel)}</span>
      </div>
      <div style="display:flex;justify-content:space-between;">
        <span style="font-size:11px;color:#9ca3af;">${bi('Ingested in last 24h','直近24h 取り込み')}</span>
        <span style="font-size:11px;font-weight:500;color:#6b7280;">+${recent24h}${bi(' items',' 件')}</span>
      </div>
    </div>`;

  // Collections リスト（最新3件 ready）
  const readyCols = (collections || []).filter(c => c.status === 'ready').slice(0, 3);
  const totalCols = (collections || []).length;
  const readyCount = (collections || []).filter(c => c.status === 'ready').length;
  const colProgress = totalCols > 0 ? Math.round(readyCount / totalCols * 100) : 0;

  // PII (ga-close-v3 PartX: 「レビュー済み率」は実体が無いので出さない)

  host.innerHTML = `
    <!-- カード1: AI Readiness -->
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid #3b82f6;border-radius:12px;padding:14px 16px;">
      <div style="font-size:13px;font-weight:600;color:#3b82f6;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">AI READINESS</div>
      <div style="font-size:36px;font-weight:700;line-height:1.1;">${aiScore}<span style="font-size:18px;">%</span></div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:10px;">${bi('Overall score across vectorization, classification, protection, and response','ベクトル化・分類・保護・応答の総合スコア')}</div>
      <div style="background:#f8fafc;border-radius:4px;height:5px;overflow:hidden;margin-bottom:12px;">
        <div style="width:${aiScore}%;height:100%;background:${scoreColor};border-radius:4px;transition:width .5s;"></div>
      </div>
      ${pipelineItems.map(p => `
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:4px;">
          <div style="width:8px;height:8px;border-radius:50%;background:${p.color};flex-shrink:0;"></div>
          <span style="font-size:13px;font-weight:500;color:#111827;width:56px;">${p.label}</span>
          <span style="font-size:13px;color:#6b7280;flex:1;">${escapeHtml(p.value)}</span>
          ${p.badge ? `<span style="font-size:10px;font-weight:500;padding:1px 6px;border-radius:4px;background:${p.badge.bg};color:${p.badge.fg};white-space:nowrap;">${escapeHtml(p.badge.text)}</span>` : ''}
        </div>`).join('')}
      ${scanHtml}
    </div>

    <!-- カード2: Collections -->
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid #22c55e;border-radius:12px;padding:14px 16px;">
      <div style="font-size:13px;font-weight:600;color:#22c55e;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">COLLECTIONS</div>
      <div style="font-size:28px;font-weight:600;color:#111827;line-height:1.1;">${readyCount}<span style="font-size:16px;color:#9ca3af;">/${totalCols}</span></div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:8px;">${readyCount} Ready${bi(', ','・')}${totalCols - readyCount} Draft</div>
      <div style="background:#f8fafc;border-radius:3px;height:4px;overflow:hidden;margin-bottom:10px;">
        <div style="width:${colProgress}%;height:100%;background:#22c55e;border-radius:3px;"></div>
      </div>
      ${readyCols.map(c => `
        <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-bottom:1px solid #e2e8f0;">
          <span style="font-size:12px;color:#111827;">${escapeHtml(c.name || '')}</span>
          <span style="font-size:11px;padding:1px 7px;border-radius:10px;background:${c.status==='ready'?'#dcfce7':'#f1f5f9'};color:${c.status==='ready'?'#166534':'#475569'};">${escapeHtml(c.status || '')}</span>
        </div>`).join('')}
    </div>

    <!-- カード3: PII -->
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid #f59e0b;border-radius:12px;padding:14px 16px;">
      <div style="font-size:13px;font-weight:600;color:#f59e0b;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">${bi('PII detected','PII 検出')}</div>
      <div style="font-size:28px;font-weight:600;color:#f59e0b;line-height:1.1;">${piiTotal.toLocaleString()}</div>
      <div style="font-size:11px;color:#9ca3af;margin-bottom:8px;">${bi('chunks containing personal information','個人情報を含む塊')}</div>
      <div style="font-size:11px;color:#6b7280;">${bi('Masked items (total)','伏字の総件数')} ${maskedSpans.toLocaleString()}${bi('',' 件')}</div>
      ${piiTotal > 0 && maskedSpans === 0 ? `<div style="margin-top:8px;"><span style="font-size:11px;padding:2px 8px;border-radius:10px;background:#fef3c7;color:#92400e;">⚠ ${bi('Detected but nothing was masked','検出はあるが伏字が0件')}</span></div>` : ''}
    </div>

    <!-- カード4: Guardrail -->
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid #8b5cf6;border-radius:12px;padding:14px 16px;">
      <div style="font-size:13px;font-weight:600;color:#8b5cf6;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;">GUARDRAIL</div>
      <div id="dashboard-guardrail-content">
        <div style="font-size:11px;color:#9ca3af;">${bi('Loading data...','データ取得中...')}</div>
      </div>
    </div>
  `;

  // Guardrailデータを非同期で取得
  _renderGuardrailCard();
}
