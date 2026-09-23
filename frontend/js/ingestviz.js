// ingestviz.js — Cynovela V3.5.0 取り込み可視化
// 設計の正本: Notion「取り込み可視化UI 設計メモ(2026-06-19)」+「指示書一本化・プロ点検(2026-06-22)」
//  - チャンキング中の秒単位ポップアップ連発(フリッカー)を「進捗バー」で置換する。
//  - 完了表示は毎回の定型文にしない: 動的(伏字件数/分類・機微度)だけを最大3行。
//    固定のガバナンス3点(端末内処理・暗号化保管・監査記録)は常設バッジ1個に圧縮。
//    詳しい動作文/分類の意味は「受領書(詳細)を開いたとき」だけ。
//  - 左右分割パネル: 左=進捗バー+要約 / 右=生ストリーミングログ(出来事とラベルのみ・平文PII非表示)。
//  - 二つの見せ方: ①クイックスタート=左右分割パネルの通し案内 / ②個別操作=受領書+ジョブ一覧(来歴)。
// フロントのみ・APIレスポンス形式/DBスキーマ/保護コアは不変。

const IngestViz = (function () {
  const _tracked = {};          // colId -> {ctx, lastStage, lastDecile, events:[], overlay:bool}
  const HIST_KEY = 'cynovela_ingest_history';
  const HIST_MAX = 50;

  function _now() { return new Date(); }
  function _hhmmss(d) {
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }
  function _esc(s) { return (typeof escapeHtml === 'function') ? escapeHtml(String(s ?? '')) : String(s ?? ''); }

  // ラベル別件数を「👤氏名×2 📞携帯電話×1」の短い内訳に。pii_labels が無ければ null。
  // ga-close-v3 PartX: 画面側の許可リスト(5種)で絞るのをやめ、返ってきたキーを全部出す。
  //   型名の表示は state.js の1か所 (piiTypeIcon / piiTypeLabel) から取る。
  function _labelBreakdown(piiLabels) {
    if (!piiLabels) return null;
    const parts = Object.keys(piiLabels)
      .map((k) => [k, parseInt(piiLabels[k] || 0, 10)])
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => `${piiTypeIcon(k)}${piiTypeLabel(k)}×${n}`);
    return parts.length ? parts : null;
  }

  // 分類サマリーから上位カテゴリ/機微度を1つ取り出す(実在分のみ・捏造しない)。
  function _topOf(obj) {
    if (!obj) return null;
    const keys = Object.keys(obj).filter((k) => k && k !== '—');
    if (!keys.length) return null;
    keys.sort((a, b) => (obj[b] || 0) - (obj[a] || 0));
    return keys[0];
  }

  // ===== overlay (左右分割パネル) =====
  function _overlayEl() { return document.getElementById('ingestviz-overlay'); }

  function _buildOverlay(colId, ctx) {
    let ov = _overlayEl();
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'ingestviz-overlay';
    ov.className = 'iv-overlay';
    ov.dataset.colId = colId;
    const flow = (typeof createPublishFlowHtml === 'function') ? createPublishFlowHtml(colId) : '';
    ov.innerHTML = `
      <div class="iv-card" role="dialog" aria-label="${_esc(lj('Ingest visualization', '取り込み可視化'))}">
        <div class="iv-head">
          <div class="iv-title">📦 ${_esc(ctx.title || lj('Ingesting', '取り込み中'))}</div>
          <button class="iv-close" title="${_esc(lj('Close', '閉じる'))}" onclick="IngestViz.close()">✕</button>
        </div>
        <div class="iv-body">
          <div class="iv-left">
            <div class="iv-section-label">${bi('Progress', '進捗')}</div>
            ${flow}
            <div class="publish-progress-wrap" data-col-id="${colId}">
              <div class="publish-progress"><div class="publish-progress-bar"></div></div>
              <div class="publish-progress-row">
                <div class="publish-progress-text">${bi('Preparing…', '準備中…')}</div>
                <span class="iv-eta" id="iv-eta-${colId}"></span>
              </div>
            </div>
            <div class="iv-pct" id="iv-pct-${colId}"></div>
            <div class="iv-mainlog-label">${bi('Status', '主要ログ')} <span class="iv-loghint">${bi('(events and labels only)', '(出来事とラベルのみ)')}</span></div>
            <div class="iv-mainlog" id="iv-mainlog-${colId}"></div>
            <div class="iv-summary" id="iv-summary-${colId}"></div>
          </div>
          <div class="iv-right">
            <div class="iv-section-label">${bi('Processing log', '処理ログ')} <span class="iv-loghint">${bi('(detailed live log per file)', '(ファイルごとの詳細な逐次ログ)')}</span></div>
            <div class="iv-log" id="iv-log-${colId}"></div>
          </div>
        </div>
        <div class="iv-foot">
          <span class="iv-gov-badge" title="${_esc(lj('Originals are encrypted at rest and decryptable only by authorized users. Masking is recorded in the audit log. No external transmission by default.', '原本は暗号化して保管し、復号は権限者のみ。マスキングは監査ログに記録されます。外部送信は既定で行いません。'))}">🔒 ${bi('On-device processing · Encrypted storage · Audit logging', '端末内処理・暗号化保管・監査記録')}</span>
          <span class="iv-foot-actions"><button class="btn btn-sm" onclick="IngestViz.showHistory()">🧾 ${bi('History', '取り込み来歴')}</button></span>
        </div>
      </div>`;
    document.body.appendChild(ov);
    return ov;
  }

  // logswap: 振り分け先を交換。_logLine(=節目/イベント・ラベル)は左「主要ログ」(iv-mainlog)へ。
  // DOM ID・関数名は不変。中身(振り分け先)のみ入れ替え。
  function _logLine(colId, icon, text, cls) {
    const host = document.getElementById(`iv-mainlog-${colId}`);
    if (!host) return;
    const row = document.createElement('div');
    row.className = 'iv-log-row' + (cls ? ' ' + cls : '');
    row.innerHTML = `<span class="iv-log-ts">${_hhmmss(_now())}</span>` +
      `<span class="iv-log-ic">${icon}</span>` +
      `<span class="iv-log-tx">${_esc(text)}</span>`;
    host.appendChild(row);
    host.scrollTop = host.scrollHeight;
  }

  // logswap: 振り分け先を交換。_mainLogLine(=細かい逐次 data.message)は右「処理ログ」(iv-log)へ。
  // DOM ID・関数名は不変。中身(振り分け先)のみ入れ替え。
  function _mainLogLine(colId, text) {
    const host = document.getElementById(`iv-log-${colId}`);
    if (!host) return;
    const row = document.createElement('div');
    row.className = 'iv-mainlog-row';
    row.innerHTML = `<span class="iv-mainlog-ts">${_hhmmss(_now())}</span>${_esc(text)}`;
    host.appendChild(row);
    while (host.childElementCount > 80) host.removeChild(host.firstChild);
    host.scrollTop = host.scrollHeight;
  }

  // fix-ingestui B-2: 残り時間の目安を秒→人間可読へ。
  function _fmtDuration(sec) {
    if (!isFinite(sec) || sec < 0) return '';
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return `${m}m${String(s).padStart(2, '0')}s`;
    const h = Math.floor(m / 60);
    return `${h}h${String(m % 60).padStart(2, '0')}m`;
  }

  // fix-ingestui B-2: 見込み時間(移動平均)。直近最大5サンプルの平均速度から残りを推定し、
  //   瞬間速度の生値でブレさせない。ステージ内 cur/total ベース(バー値・n/total は不変)。
  function _updateEta(colId, t, stage, cur, total) {
    const el = document.getElementById(`iv-eta-${colId}`);
    if (!el) return;
    if (!total || cur <= 0 || cur >= total) { el.textContent = ''; return; }
    const now = _now();
    t.etaSamples = t.etaSamples || [];
    if (t.etaLastCur === undefined || cur !== t.etaLastCur) {
      if (t.etaLastCur !== undefined && now > t.etaLastAt) {
        const rate = (cur - t.etaLastCur) / ((now - t.etaLastAt) / 1000); // units/sec
        if (rate > 0 && isFinite(rate)) {
          t.etaSamples.push(rate);
          if (t.etaSamples.length > 5) t.etaSamples.shift();
        }
      }
      t.etaLastCur = cur; t.etaLastAt = now;
    }
    if (!t.etaSamples.length) { el.textContent = ''; return; }
    const avg = t.etaSamples.reduce((a, b) => a + b, 0) / t.etaSamples.length;
    if (avg <= 0) { el.textContent = ''; return; }
    const remain = _fmtDuration(Math.round((total - cur) / avg));
    el.textContent = remain ? lj(`~${remain} left (est.)`, `残り約 ${remain}（目安）`) : '';
  }

  // ===== public API =====

  // 取り込み開始: 左右分割パネルを開いて追跡開始(quickstart=overlay)。
  function start(colId, ctx) {
    ctx = ctx || {};
    _tracked[colId] = { ctx, lastStage: null, lastDecile: -1, overlay: !!ctx.overlay, done: false };
    if (ctx.overlay) {
      _buildOverlay(colId, ctx);
      if (ctx.fileCount != null) {
        _logLine(colId, '▶️', `${lj('Ingest started', '取り込み開始')}: ${ctx.colName || ''}${ctx.fileCount ? ` (${ctx.fileCount}${lj(' files', 'ファイル')})` : ''}`, 'iv-start');
      } else {
        _logLine(colId, '▶️', `${lj('Ingest started', '取り込み開始')}: ${ctx.colName || ''}`, 'iv-start');
      }
      if (ctx.policyLabel) _logLine(colId, '📋', `${lj('Policy', 'ポリシー')}: ${ctx.policyLabel}`);
      if (ctx.qualityLabel) _logLine(colId, '🎚️', `${lj('Ingest quality', '取り込み品質')}: ${ctx.qualityLabel}`);
    }
    return _tracked[colId];
  }

  // クイックスタートの前段イベント(WS作成/スキャン/コレクション作成)をログに残す。
  function event(colId, icon, text) {
    if (!_tracked[colId]) return;
    _logLine(colId, icon, text);
  }

  function isTracking(colId) { return !!_tracked[colId] && !_tracked[colId].done; }

  // finalround B-6: 進行中(未完了)の取り込みが1件でもあるか。離脱警告(beforeunload)の判定に使用。
  //   _tracked は done/fail/stop/close で done:true + delete されるため、無処理時は false（誤警告しない）。
  function anyActive() { return Object.keys(_tracked).some(function (k) { return _tracked[k] && !_tracked[k].done; }); }

  // 進捗反映。進捗バー本体は既存 updateProgressUI が driving。ここでは生ログへ段の変化を出す。
  function progress(colId, data) {
    const t = _tracked[colId];
    if (!t || !data) return;
    const stage = data.stage || '';
    const cur = data.current || 0;
    const total = data.total || 0;
    if (stage && stage !== t.lastStage) {
      t.lastStage = stage;
      t.lastDecile = -1;
      t.lastTickCur = 0;
      t.etaSamples = []; t.etaLastCur = undefined;  // fix-ingestui B-2: ステージ変更で見込み速度をリセット(単位が変わるため)
      // Fix1 (ingestfix4-20260627): 段が切り替わったら大きい数字(iv-pct)を前段の値(100%等)から
      //   新段の 0/total へ即リセット。更新ガード lastTickCur は直上で 0 済み。増加方向のみの
      //   ガードが新段 current=0 を弾き前段100%が居座る問題を断つ。total 未知のうちは 0% 起点で表示。
      {
        const _pctEl0 = document.getElementById(`iv-pct-${colId}`);
        if (_pctEl0) {
          const _unit0 = stage === 'embedding' ? lj('chunks', 'チャンク') : lj('files', 'ファイル');
          const _verb0 = stage === 'embedding' ? lj('Vectorizing', 'ベクター化') : lj('Processing', '処理');
          _pctEl0.textContent = total > 0 ? `${_verb0} 0/${total} ${_unit0} · 0%` : `${_verb0} 0%`;
        }
      }
      if (stage === 'chunking') _logLine(colId, '✂️', lj('Chunking started', 'チャンク分割を開始'));
      else if (stage === 'embedding') _logLine(colId, '🧬', lj('Vectorization started', 'ベクター化を開始') + (total ? ` (${total} ${lj('chunks', 'チャンク')})` : ''));
      else if (stage === 'storing' || stage === 'store') _logLine(colId, '💾', lj('Writing to vector DB', 'ベクターDBへ書き込み'));
    }
    // 実ジョブの進捗が前回tickより進むたびに1行流す。ポーリング(2s刻み)が自然に律速するため
    // 秒次フリッカーにはならず、右ログが「流れ続ける」。件数で出す(右ログ=出来事と件数/
    // バー=全体%。%を二重表示してバーと食い違わせない)。
    // logswap+percent: 左(節目側)は1行ずつ刻まず、進捗を「N/M 単位 · X%」の1要素へ in-place 更新
    // (処理済み/総数を母数にした%。embedding 時は総チャンク数が分母)。成長リスト化を解消。
    if (total > 0 && cur > (t.lastTickCur || 0) && cur <= total) {
      t.lastTickCur = cur;
      const unit = stage === 'embedding' ? lj('chunks', 'チャンク') : lj('files', 'ファイル');
      const verb = stage === 'embedding' ? lj('Vectorizing', 'ベクター化') : lj('Processing', '処理');
      const pct = Math.max(0, Math.min(100, Math.round((cur / total) * 100)));
      const pctEl = document.getElementById(`iv-pct-${colId}`);
      if (pctEl) pctEl.textContent = `${verb} ${cur}/${total} ${unit} · ${pct}%`;
    }
    // fix-ingestui B-2: 左「主要ログ」へ data.message(状態文字列)を流す(変化時のみ集約)。
    const _msg = (data.message || '').trim();
    if (_msg && _msg !== t.lastMainMsg) { t.lastMainMsg = _msg; _mainLogLine(colId, _msg); }
    // fix-ingestui B-2: 見込み時間(移動平均ベース・瞬間値でブレさせない)
    _updateEta(colId, t, stage, cur, total);
  }

  // 完了。compact(最大3行)+ガバナンスバッジ。詳細/受領書は別途展開。来歴に追記。
  function done(colId, data) {
    const t = _tracked[colId];
    const ctx = (t && t.ctx) || {};
    data = data || {};
    const chunks = parseInt(data.chunk_count || 0, 10);
    const fileCnt = parseInt(data.file_count || ctx.fileCount || 0, 10);
    const piiLabels = data.pii_labels || null;
    // ga-close-v3 PartX: 件数は画面で数え直さない。サーバの唯一の口が返す
    //   pii_count (= 伏字が当たった塊数) をそのまま使う。
    const piiTotal = parseInt(data.pii_count || 0, 10);
    const breakdown = _labelBreakdown(piiLabels);
    const cls = data.classification_summary || {};
    const topDoc = _topOf(cls.doc_type);
    const topSens = _topOf(cls.sensitivity);

    // 受領書/履歴用に選択値も束ねる(showPublishSummaryCard が受領書区画を出せるように)。
    const receipt = {
      collection: ctx.colName || data.collection_name || '',
      folder: ctx.folder || '',
      policyLabel: ctx.policyLabel || '',
      qualityLabel: ctx.qualityLabel || '',
      chunks, fileCnt, piiTotal,
      labels: piiLabels || null,
      ts: _now().toISOString(),
    };
    _pushHistory(receipt);

    // ---- 最大3行の動的サマリー(定型文を毎回繰り返さない) ----
    // 行1: 伏字件数+内訳 / 行2: 分類・機微度(or チャンク数) / 行3: ガバナンス小バッジ
    const line1 = piiTotal > 0
      ? `🛡 ${lj(`Masked personal information in <b>${piiTotal}</b> chunk(s)`, `個人情報を含む塊 <b>${piiTotal}件</b> を伏字にしました`)}${breakdown ? `（${breakdown.join(' ')}）` : ''}`
      : `🛡 ${lj('No personal information detected', '個人情報は検出されませんでした')}`;
    const line2 = (topDoc || topSens)
      ? `📂 ${lj('Classification', '分類')}: <b>${_esc(_classLabel('doc_type', topDoc))}</b> ／ ${lj('Sensitivity', '機微度')}: <b>${_esc(_classLabel('sensitivity', topSens))}</b>`
      : `📊 ${lj(`Indexed ${chunks} chunks`, `${chunks} チャンクを索引化`)}${fileCnt ? ` ／ ${lj(`${fileCnt} files`, `${fileCnt} ファイル`)}` : ''}`;

    // DD-CYN-0091 C: 飛ばしたファイルの一覧 (ファイル名+理由) を完了サマリーに出す
    const _skDetails = Array.isArray(data.skipped_details) ? data.skipped_details : [];
    const lineSkip = _skDetails.length
      ? `<div class="iv-done-line" style="color:#b45309;">⚠ ${lj(`Skipped ${_skDetails.length} file(s)`, `${_skDetails.length} ファイルを飛ばしました`)}: ${_esc(_skDetails.slice(0, 10).map(d => `${d.file}（${d.reason}）`).join(' / '))}${_skDetails.length > 10 ? ' …' : ''}</div>`
      : '';
    const detailData = Object.assign({}, data, { collection_id: colId, _receipt: receipt });
    const summaryHtml = `
      <div class="iv-done">
        <div class="iv-done-line">${line1}</div>
        <div class="iv-done-line">${line2}</div>
        ${lineSkip}
        <div class="iv-done-line"><span class="iv-gov-badge">🔒 ${bi('On-device processing · Encrypted storage · Audit logging', '端末内処理・暗号化保管・監査記録')}</span></div>
        <div class="iv-done-actions">
          <button class="btn btn-sm" onclick='IngestViz.detail(${JSON.stringify(detailData)})'>📄 ${bi('Receipt / details', '詳細・受領書')}</button>
          ${ctx.gotoChat ? `<button class="btn btn-sm btn-primary" onclick="IngestViz.close();navigate('chat')">💬 ${bi('Go to chat', 'チャットへ')}</button>` : ''}
        </div>
      </div>`;

    if (t) t.done = true;
    _setJobStatus(colId, 'completed');
    const ov = _overlayEl();
    if (t && t.overlay && ov && ov.dataset.colId === String(colId)) {
      _logLine(colId, '✅', `${lj('Complete', '完了')}: ${lj(`Indexed ${chunks} chunks`, `${chunks} チャンクを索引化`)}`, 'iv-ok');
      if (piiTotal > 0 && breakdown) _logLine(colId, '🛡', `${lj('Masked', '伏字')}: ${breakdown.join(' ')}`, 'iv-ok');
      _logLine(colId, '🔐', lj('Originals stored encrypted · masking recorded in audit log', '原本は暗号化保管・マスキングは監査ログに記録'), 'iv-gov');
      const host = document.getElementById(`iv-summary-${colId}`);
      if (host) host.innerHTML = summaryHtml;
    } else {
      // 個別操作(コレクション公開等)=受領書を小さいモーダルで(最大3行・定型文圧縮)。
      if (typeof showP3Modal === 'function') {
        showP3Modal('✅ ' + lj('Ingest complete', '取り込み完了'),
          `<div class="iv-done iv-done-modal">
             <div class="iv-done-line">${line1}</div>
             <div class="iv-done-line">${line2}</div>
             ${lineSkip}
             <div class="iv-done-line"><span class="iv-gov-badge">🔒 ${bi('On-device processing · Encrypted storage · Audit logging', '端末内処理・暗号化保管・監査記録')}</span></div>
             <div class="iv-done-actions">
               <button class="btn btn-sm" onclick='IngestViz.detail(${JSON.stringify(detailData)})'>📄 ${bi('Receipt / details', '詳細・受領書')}</button>
               <button class="btn btn-sm btn-primary" onclick="closeP3Modal()">${bi('Close', '閉じる')}</button>
             </div>
           </div>`, { lockBgClose: false });
      }
    }
    delete _tracked[colId];
  }

  function fail(colId, msg) {
    const t = _tracked[colId];
    if (t && t.overlay) {
      _logLine(colId, '❌', `${lj('Failed', '失敗')}: ${msg || ''}`, 'iv-err');
      const host = document.getElementById(`iv-summary-${colId}`);
      if (host) host.innerHTML = `<div class="iv-done"><div class="iv-done-line" style="color:#b91c1c;">❌ ${_esc(msg || lj('Failed', '失敗しました'))}</div>
        <div class="iv-done-actions"><button class="btn btn-sm" onclick="IngestViz.close()">${bi('Close', '閉じる')}</button></div></div>`;
    } else if (typeof showToast === 'function') {
      showToast((CYNOVELA_LANG === 'ja' ? '取り込み失敗: ' : 'Ingest failed: ') + (msg || ''), 'error');
    }
    if (t) t.done = true;
    _setJobStatus(colId, 'failed');
    delete _tracked[colId];
  }

  function stop(colId, msg) {
    const t = _tracked[colId];
    if (t && t.overlay) {
      _logLine(colId, '⏹', lj('Stopped', '停止しました'), 'iv-warn');
      const host = document.getElementById(`iv-summary-${colId}`);
      if (host) host.innerHTML = `<div class="iv-done"><div class="iv-done-line">⏹ ${_esc(msg || lj('Stopped', '停止しました'))}</div>
        <div class="iv-done-actions"><button class="btn btn-sm" onclick="IngestViz.close()">${bi('Close', '閉じる')}</button></div></div>`;
    } else if (typeof showToast === 'function') {
      showToast(CYNOVELA_LANG === 'ja' ? '取り込みを停止しました' : 'Ingest stopped', 'warning');
    }
    if (t) t.done = true;
    _setJobStatus(colId, 'stopped');
    delete _tracked[colId];
  }

  function close() {
    const ov = _overlayEl();
    if (ov) ov.remove();
  }

  // 詳細・受領書: 既存の詳細カード(全情報)を開く。固定の説明文はここでだけ出す。
  function detail(data) {
    if (typeof showPublishSummaryCard === 'function') {
      try { showPublishSummaryCard(data); return; } catch (e) { /* fall through */ }
    }
    if (typeof showP3Modal === 'function') showP3Modal('📄 ' + lj('Details', '詳細'), `<div>${bi('Cannot display details', '詳細を表示できません')}</div>`);
  }

  // ===== 来歴(ジョブ一覧) =====
  function _pushHistory(rec) {
    try {
      const arr = JSON.parse(localStorage.getItem(HIST_KEY) || '[]');
      arr.unshift(rec);
      localStorage.setItem(HIST_KEY, JSON.stringify(arr.slice(0, HIST_MAX)));
    } catch (e) { /* ignore */ }
  }
  function getHistory() {
    try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch (e) { return []; }
  }
  // 来歴を一覧表示(コレクション公開1件=1行・マージしない)。
  function showHistory() {
    const arr = getHistory();
    const rows = arr.length ? arr.map((r) => {
      const bd = _labelBreakdown(r.labels);
      const ts = (r.ts || '').replace('T', ' ').slice(0, 16);
      return `<div class="iv-hist-row">
        <div class="iv-hist-main"><b>${_esc(r.collection || '(no name)')}</b>
          <span class="iv-hist-sub">${ts}</span></div>
        <div class="iv-hist-meta">📊 ${r.chunks || 0}ch ／ ${r.fileCnt || 0}file ／ 🛡 ${r.piiTotal || 0}${bd ? `（${bd.join(' ')}）` : ''}${r.policyLabel ? ` ／ 📋 ${_esc(r.policyLabel)}` : ''}${r.qualityLabel ? ` ／ 🎚️ ${_esc(r.qualityLabel)}` : ''}</div>
      </div>`;
    }).join('') : `<div style="color:#94a3b8;padding:14px;">${bi('No ingest history yet.', '取り込み来歴はまだありません。')}</div>`;
    // Fix4 (ingestfix4-20260627): 再表示導線。進行中=ライブ再アタッチ(全ユーザー・フロント完結)、
    //   完了=永続記録(admin 限定・既存 admin EP 読み出しのみ)。非 admin には永続記録ボタンを出さない。
    const actions = `<div class="iv-hist-actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
        <button class="btn btn-sm" onclick="IngestViz.reattachLive()">🔄 ${bi('Show running progress', '進行中の進捗を表示')}</button>
        ${_isAdmin() ? `<button class="btn btn-sm" onclick="IngestViz.showPersistentLog()">🗄 ${bi('Persistent record (admin)', '永続記録（管理者）')}</button>` : ''}
      </div>`;
    if (typeof showP3Modal === 'function') {
      showP3Modal('🧾 ' + lj('Ingest history', '取り込み来歴'),
        `<div class="iv-hist">${actions}${rows}</div>`);
    }
  }

  // latestlog-20260627: 最新ログ — 直近1件の取り込み受領書だけを再表示する(来歴一覧は出さない=最新のみ)。
  //   出どころ= localStorage HIST_KEY の先頭(done() が完了時に push 済み)。進捗ポップアップを閉じた後でも、
  //   非admin を含む全ユーザーがフロント完結で参照できる(admin限定の永続記録EPには依存しない)。
  //   描画は showHistory の1行と同じ markup を単一受領書で。タイトルは lj()(escape安全・生タグ非表示)。
  function showLatest() {
    if (typeof showP3Modal !== 'function') return;
    const arr = getHistory();
    const r = (arr && arr.length) ? arr[0] : null;
    if (!r) {
      showP3Modal('🧾 ' + lj('Latest ingest log', '最新の取り込みログ'),
        `<div style="color:#94a3b8;padding:14px;">${bi('No ingest yet.', 'まだ取り込みはありません。')}</div>`);
      return;
    }
    const bd = _labelBreakdown(r.labels);
    const ts = (r.ts || '').replace('T', ' ').slice(0, 16);
    const body = `<div class="iv-hist"><div class="iv-hist-row">
        <div class="iv-hist-main"><b>${_esc(r.collection || '(no name)')}</b>
          <span class="iv-hist-sub">${ts}</span></div>
        <div class="iv-hist-meta">📊 ${r.chunks || 0}ch ／ ${r.fileCnt || 0}file ／ 🛡 ${r.piiTotal || 0}${bd ? `（${bd.join(' ')}）` : ''}${r.policyLabel ? ` ／ 📋 ${_esc(r.policyLabel)}` : ''}${r.qualityLabel ? ` ／ 🎚️ ${_esc(r.qualityLabel)}` : ''}</div>
      </div></div>`;
    showP3Modal('🧾 ' + lj('Latest ingest log', '最新の取り込みログ'), body);
  }

  // 分類キーの日本語表示(state.js の既存ラベル辞書を流用・無ければそのまま)。
  function _classLabel(kind, key) {
    if (!key) return '—';
    try {
      if (kind === 'sensitivity' && typeof _sensitivityLabels === 'function') return _sensitivityLabels()[key] || key;
      if (kind === 'doc_type' && typeof _docTypeLabels === 'function') return _docTypeLabels()[key] || key;
    } catch (e) { /* ignore */ }
    return key;
  }

  // ===== ingest-resilience v1: C(裏で継続案内) / D(リロード復帰) / E(前回ログ・続きから) =====
  const JOBS_KEY = 'cynovela_ingest_jobs';
  const JOBS_MAX = 30;
  function _getJobs() { try { return JSON.parse(localStorage.getItem(JOBS_KEY) || '[]'); } catch (e) { return []; } }
  function _putJobs(a) { try { localStorage.setItem(JOBS_KEY, JSON.stringify(a.slice(0, JOBS_MAX))); } catch (e) { /* quota */ } }

  // Publish 開始時に jobId を記録 (フルリロード後も /api/jobs/{id} で進捗を引ける=新規スキーマ不要)。
  function registerJob(colId, jobId, meta) {
    meta = meta || {};
    const a = _getJobs().filter((j) => j.colId !== colId);
    a.unshift({
      colId, jobId,
      colName: meta.colName || (_tracked[colId] && _tracked[colId].ctx.colName) || '',
      status: 'running', overlay: !!meta.overlay, ts: _now().toISOString(),
    });
    _putJobs(a);
    // finalround B-6: サーバ側では裏で継続するが、進行中はタブを閉じる/離れる操作で beforeunload 警告を出す
    //   (state.js の _cynovelaIngestInProgress)。案内文も「閉じると進捗表示が失われる」旨に統一。
    const t = _tracked[colId];
    if (t && t.overlay) {
      _logLine(colId, 'ℹ️', lj('Ingest continues on the server (closing this tab loses the live progress view)', '取り込みはサーバ側で継続します（このタブを閉じると進捗表示は失われます）'), 'iv-gov');
    } else if (typeof showToast === 'function') {
      showToast(CYNOVELA_LANG === 'ja'
        ? '取り込みはサーバ側で継続します（このタブを閉じると進捗表示は失われます）'
        : 'Ingest continues on the server (closing this tab loses the live progress view)', 'info');
    }
  }
  function _setJobStatus(colId, status) {
    const a = _getJobs(); const j = a.find((x) => x.colId === colId);
    if (j) { j.status = status; j.endedAt = _now().toISOString(); _putJobs(a); }
  }

  // D: リロード/再ログイン時に進行中ジョブを再アタッチ (既存ポーリングで inline 進捗を復帰)。
  let _resumed = false;
  async function resumeOnLoad() {
    if (_resumed) return; _resumed = true;
    const jobs = _getJobs(); if (!jobs.length) return;
    for (const j of jobs) {
      if (j.status !== 'running') continue;
      try {
        const job = await API.get(`/api/jobs/${j.jobId}`);
        if (job && (job.status === 'running' || job.status === 'pending')) {
          if (typeof _startPublishPoll === 'function' && typeof _publishPolls !== 'undefined' && !_publishPolls[j.colId]) {
            start(j.colId, { colName: j.colName, overlay: false });
            if (typeof ensureProgressUI === 'function') ensureProgressUI(j.colId);
            _startPublishPoll(j.colId, j.jobId);
          }
        } else {
          _setJobStatus(j.colId, (job && job.status) || 'failed');
        }
      } catch (e) { _setJobStatus(j.colId, 'failed'); }
    }
    _renderRecentBanner();
  }

  // E: 前回の取り込み (完了/失敗/中断) 一覧 + 失敗/中断行から「再公開（続きから）」導線。
  function _renderRecentBanner() {
    const term = _getJobs().filter((j) => j.status && j.status !== 'running').slice(0, 5);
    if (!term.length) return;
    let host = document.getElementById('iv-recent-banner');
    if (!host) { host = document.createElement('div'); host.id = 'iv-recent-banner'; host.className = 'iv-recent'; document.body.appendChild(host); }
    const lab = (s) => s === 'completed' ? '✅ ' + lj('Complete', '完了') : (s === 'stopped' ? '⏹ ' + lj('Interrupted (stopped)', '中断（停止）') : '⚠️ ' + lj('Failed/interrupted', '失敗/中断'));
    host.innerHTML = `<div class="iv-recent-head">🧾 ${bi('Last ingests', '前回の取り込み')}<button class="iv-recent-x" onclick="this.closest('.iv-recent').remove()">✕</button></div>` +
      term.map((j) => `<div class="iv-recent-row"><span class="iv-recent-name">${_esc(j.colName || j.colId)}</span><span>${lab(j.status)}</span>${j.status !== 'completed' ? `<button class="btn btn-sm" onclick="IngestViz.republish('${_esc(j.colId)}')">▶ ${bi('Re-publish (resume)', '再公開（続きから）')}</button>` : ''}</div>`).join('');
  }

  // 再公開（続きから）= 同コレクションを再 publish。済/未変更ファイルは file-hash dedup で飛ばし、
  // 途中だったファイルのみ決定的 ID で再処理(=上書き、孤児化しない)。
  async function republish(colId) {
    const _c = (typeof State !== 'undefined' && State.collections || []).find((c) => c.id === colId);
    const nm = (_c && _c.name) || '';
    try {
      start(colId, { colName: nm, overlay: false });
      const job = await API.post(`/api/collections/${colId}/publish/async`, {});
      if (job && job.job_id) {
        registerJob(colId, job.job_id, { colName: nm });
        if (typeof ensureProgressUI === 'function') ensureProgressUI(colId);
        if (typeof _startPublishPoll === 'function') _startPublishPoll(colId, job.job_id);
        document.getElementById('iv-recent-banner')?.remove();
      } else { fail(colId, lj('Failed to start re-publish (job_id not obtained)', '再公開の開始に失敗しました (job_id 未取得)')); }
    } catch (e) { fail(colId, (e && e.message) || lj('Re-publish failed', '再公開に失敗しました')); }
  }

  // ===== Fix4 (ingestfix4-20260627): 来歴の再表示 — 進行中=ライブ再アタッチ / 完了=永続記録(admin) =====
  // admin 判定は state.js と同じ規則(State.demoRole || State.user.role を小文字化して 'admin')。表示抑止のみに使用。
  function _isAdmin() {
    try {
      const role = ((typeof State !== 'undefined' && (State.demoRole || (State.user && State.user.role))) || '').toLowerCase();
      return role === 'admin';
    } catch (e) { return false; }
  }

  // latestlog-dualbtn-20260627: 「最新ログ」ボタンの一本化(走行中なら再アタッチ / 非走行なら完了記録)。
  //   走行中判定は同一ブラウザ側だけで成立: ①メモリ live=_cynovelaIngestInProgress(state.js・API不要)
  //   ②localStorage の running ジョブ(_getJobs・リロード後も残存=閉→再オープン対応)。どちらかが真なら
  //   既存 reattachLive() で進捗・ログへ再アタッチ。何も走っていなければ従来どおり直近の完了記録1件
  //   (showLatest)。新EP/スキーマ無・既存関数の再利用+分岐のみ(DOM ID/fetch URL/関数名 不変)。
  function showLatestOrReattach() {
    let running = false;
    try { running = (typeof _cynovelaIngestInProgress === 'function') && _cynovelaIngestInProgress(); } catch (e) { /* ignore */ }
    if (!running) { try { running = _getJobs().some((j) => j && j.status === 'running'); } catch (e) { /* ignore */ } }
    if (running) { reattachLive(); return; }
    showLatest();
  }

  // 進行中ジョブへ手動で再アタッチ。resumeOnLoad は _resumed で一度きりのため、手動再表示用に
  //   同ガードを解いて再走査する(_startPublishPoll は二重起動を弾く=冪等)。フロント完結・全ユーザー。
  //   新EP/スキーマ・保護対象に不接触(既存 /api/jobs/{id} ポーリング再利用)。
  async function reattachLive() {
    _resumed = false;
    try { await resumeOnLoad(); } catch (e) { /* ignore */ }   // 既存ポーリング/inline を復帰(全 running ジョブ)
    const running = _getJobs().filter((j) => j.status === 'running');
    const active = running.length;
    // latestlog-reattach-fix-20260627: 進行中なら進捗の可視化パネル(#ingestviz-overlay)を前面に開く。
    //   従来は inline 復帰 + トーストのみでパネルが前面に出なかった。既存 start({overlay:true}) で
    //   パネルを構築し、resumeOnLoad が張った既存ポーリングが進捗を流し込む(新EP/DOM ID 無)。
    //   進捗バーは updateProgressUI が .publish-progress-wrap[data-col-id] の先頭一致へ出すため、
    //   overlay 内ラッパーが受けるよう overlay 外の同 colId inline ラッパーは除去する。
    if (active) {
      const j = running[0];
      try {
        start(j.colId, { colName: j.colName, title: j.colName, overlay: true });
        document.querySelectorAll(`.publish-progress-wrap[data-col-id="${j.colId}"]`).forEach((w) => {
          if (!w.closest('#ingestviz-overlay')) { const _tr = w.closest('tr.publish-progress-tr'); (_tr || w).remove(); }
        });
      } catch (e) { /* ignore */ }
      // F-2 (modelchat-ui-20260628): 閉じる前の逐次ログを再アタッチ時に復元する(admin・読み出しのみ)。
      //   既存 admin EP /api/admin/processing-logs を再利用し、collection_id 一致行を時系列(古い順)で
      //   主要ログへ流す。新EP/スキーマ・DOM ID・関数名は不変。非 admin はスキップ(showPersistentLog と同ガード)。
      if (typeof _isAdmin === 'function' && _isAdmin()) {
        try {
          const r = await API.get('/api/admin/processing-logs?log_type=ingest&limit=300');
          const rows = (Array.isArray(r) ? r : ((r && (r.logs || r.items)) || []))
            .filter((x) => { try { return JSON.parse(x.metadata_json || '{}').collection_id === j.colId; } catch (e) { return false; } })
            .reverse();
          if (rows.length) {
            _logLine(j.colId, '🕘', lj('— restored log from before reopen —', '— 再表示前の記録を復元 —'));
            rows.forEach((x) => {
              const ts = (x.timestamp || '').replace('T', ' ').slice(11, 19);
              const cls = x.level === 'error' ? 'iv-err' : (x.level === 'success' ? 'iv-ok' : (x.level === 'warning' ? 'iv-warn' : ''));
              _logLine(j.colId, '·', `[${ts}] ${x.message || ''}`, cls);
            });
          }
        } catch (e) { /* ignore */ }
      }
    }
    if (typeof showToast === 'function') {
      showToast(active
        ? (CYNOVELA_LANG === 'ja' ? '進行中の取り込みの進捗を表示します' : 'Showing the progress of the active ingest')
        : (CYNOVELA_LANG === 'ja' ? '進行中の取り込みはありません' : 'No active ingests'),
        active ? 'info' : 'warning');
    }
  }

  // 完了ジョブの永続記録(操作ログ)を既存 admin EP から読み出して表示(admin 限定・読み出しのみ)。
  //   GET /api/admin/processing-logs?log_type=ingest&limit=N — 新EP/スキーマは作らない。永続ログは
  //   段名・件数・ファイル名・時刻のみ(PII CLEAN・台帳 事実4)。非 admin は導線非表示+二重ガードで抑止。
  async function showPersistentLog() {
    if (!_isAdmin()) {
      if (typeof showToast === 'function') showToast(CYNOVELA_LANG === 'ja' ? '永続記録の表示は管理者のみです' : 'Persistent record is admin only', 'warning');
      return;
    }
    let logs = [];
    try {
      const r = await API.get('/api/admin/processing-logs?log_type=ingest&limit=100');
      logs = Array.isArray(r) ? r : ((r && (r.logs || r.items)) || []);
    } catch (e) {
      if (typeof showP3Modal === 'function') showP3Modal('🗄 ' + lj('Persistent record', '永続記録'),
        `<div style="color:#b91c1c;padding:14px;">${bi('Failed to load', '取得に失敗しました')}: ${_esc((e && e.message) || '')}</div>`);
      return;
    }
    const prows = logs.length ? logs.map((r) => {
      let meta = {};
      try { meta = r.metadata_json ? JSON.parse(r.metadata_json) : {}; } catch (e) { meta = {}; }
      const stage = meta.stage || '';
      const ts = (r.timestamp || '').replace('T', ' ').slice(0, 19);
      const lvl = r.level === 'error' ? 'iv-err' : (r.level === 'warning' ? 'iv-warn' : '');
      const detail = [];
      if (meta.chunk_count != null) detail.push(`📊 ${_esc(meta.chunk_count)}ch`);
      if (meta.current != null && meta.total != null) detail.push(`${_esc(meta.current)}/${_esc(meta.total)}`);
      if (stage) detail.push(`〔${_esc(stage)}〕`);
      return `<div class="iv-hist-row ${lvl}">
        <div class="iv-hist-main"><b>${_esc(r.message || '')}</b>
          <span class="iv-hist-sub">${_esc(ts)}</span></div>
        ${detail.length ? `<div class="iv-hist-meta">${detail.join(' ／ ')}</div>` : ''}
      </div>`;
    }).join('') : `<div style="color:#94a3b8;padding:14px;">${bi('No persistent ingest record yet.', '永続の取り込み記録はまだありません。')}</div>`;
    if (typeof showP3Modal === 'function') {
      showP3Modal('🗄 ' + lj('Persistent ingest record (admin)', '取り込み永続記録（管理者）'),
        `<div class="iv-hist">${prows}</div>`);
    }
  }

  return { start, event, isTracking, anyActive, progress, done, fail, stop, close, detail, getHistory, showHistory, showLatest,
           showLatestOrReattach, registerJob, resumeOnLoad, republish, reattachLive, showPersistentLog };
})();
