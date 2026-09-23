// pipeline.js - Cynovela v13

function statusTag(status) {
  const labelJa = { idle:'待機', scanning:'スキャン中', completed:'完了', failed:'失敗', draft:'Draft', publishing:'Publishing', ready:'Ready' };
  const labelEn = { idle:'Idle', scanning:'Scanning', completed:'Done', failed:'Failed', draft:'Draft', publishing:'Publishing', ready:'Ready' };
  const label = (CYNOVELA_LANG === 'ja') ? labelJa : labelEn;
  return `<span class="tag status-${status}">${label[status]||status}</span>`;
}

async function renderOverview() {
  // GUI修正8 #05: ダッシュボード v2 — 上段7タイル削除、Readiness Bar 追加、コンポーネントカード刷新
  // 既存 #pipeline-flow は使わず、5-1 Readiness Bar に置換

  // ファイル統計を集計 (既存ロジック流用)
  let totalFiles = 0, piiCount = 0;
  for (const s of State.sources) {
    if (!State.allFiles[s.id]) {
      try { State.allFiles[s.id] = await API.get(`/api/sources/${s.id}/files`); } catch { State.allFiles[s.id] = []; }
    }
    const files = State.allFiles[s.id];
    totalFiles += files.length;
    piiCount += files.filter(f => (f.categories||[]).includes('PII')).length;
  }
  const readyCol = State.collections.filter(c => c.status === 'ready').length;
  const totalCol = State.collections.length;
  const vecRate = totalCol > 0 ? Math.round((readyCol / totalCol) * 100) : 0;

  // /api/dashboard/summary から取得
  let summary = null;
  try {
    summary = await API.get('/api/dashboard/summary');
  } catch (e) {
    // P0-2: summary 取得失敗 (権限/ネットワーク等) でも後続カード描画を止めない。
    // ファイル統計ベースのカードは State.sources から描画継続できるため early return しない。
    const host = document.getElementById('system-readiness-host');
    if (host) host.innerHTML = `<div style="padding:8px;color:#9ca3af;font-size:12px;">${lj('Summary unavailable','サマリー取得不可')}</div>`;
    summary = {};
  }

  // F1-2: 「✅ デモ準備完了」バナーは削除（情報過多のため）
  // renderSystemReadinessBar(summary, totalFiles, readyCol);
  // F3: PII件数をダッシュボード summary (chunk 単位 = pii_detections_total) に統一。
  //     従来は files の 'PII' カテゴリ件数で集計しており、上部 Row1 カードの
  //     pii_detections_total と桁違いに食い違って見えていた（251 vs 63）。
  if (summary && summary.pii_detections_total != null) {
    piiCount = summary.pii_detections_total;
  }
  // ===== allinone F1/F2: A6 Trust Console (iframe) があれば旧ダッシュ描画を全てスキップし iframe へ委譲 =====
  if (document.getElementById('a6-frame')) {
    if (typeof window.__wireA6 === 'function') {
      try { window.__wireA6(summary, { totalFiles, piiCount, readyCol, totalCol, vecRate, catCount: null }); } catch (_) {}
    }
    return;
  }
  // 5-2: AI Readiness Score
  renderAiReadinessScore(summary, totalFiles, readyCol, piiCount);
  // P1p2 §5: catCount を先に計算してパイプラインヘルス Classify カードに上位3件を表示
  const catCount = {};
  Object.values(State.allFiles).flat().forEach(f => {
    (f.categories||[]).forEach(c => { catCount[c] = (catCount[c]||0) + 1; });
  });

  // 5-3: 4コンポーネントカード (renderPipelineComponents)
  renderPipelineComponents(summary, catCount);
  // 5-4: 統計タイル4個 (改良版)
  renderStatsGridV2(totalFiles, piiCount, readyCol, totalCol, vecRate);
  // 5-5: 自動同期セクション → 2026-05-04 WS 詳細画面へ移動済み (Overview からは撤去)
  // renderPollingStatusCard(summary);  // 旧 Overview 描画 (deprecated)

  // Category chart 用バー (既存) — 数値ラベル維持
  const maxCat = Math.max(...Object.values(catCount), 1);
  $('cat-bars').innerHTML = Object.entries(catCount).sort((a,b) => b[1]-a[1]).map(([cat, count]) =>
    `<div class="bar-row">
      <span class="bar-label">${escapeHtml(cat)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(count/maxCat)*100}%"></div></div>
      <span class="bar-count">${count}</span>
    </div>`
  ).join('') || '<div class="cat-map">' + bi('No data', 'データなし') + '</div>';

  // P2 §1: カテゴリ分布横棒グラフ
  try { renderCategoryBarChart(catCount); } catch (_) { /* ignore */ }

  // R2: Recent Activity ウィジェットは Overview から削除済み (Guardrails ページの監査ログを参照)

  // P2 §3: パイプラインヘルス (起動時に 1 回呼び出し)
  if (typeof updatePipelineHealth === 'function') {
    updatePipelineHealth().catch(() => {});
  }
  // P2 §5: ピン留め状態を反映
  if (typeof renderMyDashboard === 'function') renderMyDashboard();
  if (typeof updatePinButtons === 'function') updatePinButtons();

  // ===== 新ダッシュボード Row1〜Row3 描画 =====
  try {
    const chromaInfo = await _getChromaSize();
    let colsData = [];
    try {
      const colR = await API.get('/api/collections');
      colsData = colR?.collections || (Array.isArray(colR) ? colR : []);
    } catch (_) {}
    await renderDashboardRow1(summary, colsData);
    renderDashboardRow2(summary, chromaInfo.size, chromaInfo.freeGb);
    // F2-A: Row3 (カテゴリ分布 / クエリトレンド / モデル統計) は viewer に閲覧権限が無い
    //       admin 限定 EP (/api/audit-logs, /api/stats/model) を叩くため、viewer では
    //       取得失敗の赤字が露出する。エラー描画の代わりにセクション全体を非表示にする。
    const _row3 = document.getElementById('dashboard-row3');
    if (State.user && State.user.role === 'viewer') {
      if (_row3) _row3.style.display = 'none';
    } else {
      if (_row3) _row3.style.display = 'grid';
      renderQueryTrend().catch(() => {});
      // F3: 「N docs」ヘッダはカテゴリ tag の総和(=454, 多重カウント)ではなく
      //     実ファイル数(summary.total_files=148)を表示する。
      renderCategoryBarInRow3(catCount, (summary && summary.total_files) || 0);
      renderModelUsage().catch(() => {});
    }
  } catch (_) { /* fail-safe: 新ダッシュボード描画失敗時も既存UIは継続 */ }
}

// ===== allinone F2: A6 Trust Console へ実データを配線（未計測は N/A・サンプル値禁止）=====
window.__wireA6 = async function(summary){
  summary = summary || {};
  const fN = (n)=> (n==null ? '—' : Number(n).toLocaleString());
  const applyTo = (d, tries)=>{
    const fr = document.getElementById('a6-frame');
    if (fr && fr.contentWindow && fr.contentWindow.A6 && typeof fr.contentWindow.A6.update === 'function') {
      try { fr.contentWindow.A6.update(d); } catch (e) { console.warn('A6 update failed', e); }
    } else if ((tries||0) < 25) { setTimeout(()=>applyTo(d, (tries||0)+1), 300); }
  };
  const tc=summary.total_chunks||0, vc=summary.vectorized_chunks||0;
  const tf=summary.total_files||0, cf=summary.classified_files||0;
  const piiTot=summary.pii_detections_total||0; // pii-fiction fix: 実体なし piiUn(pii_unreviewed_count)参照を撤去
  const tw=summary.total_workspaces||0, wsNo=summary.ws_without_policy_count||0;
  const ingest = tc>0 ? Math.round(vc/tc*100) : 0;
  const classify = tf>0 ? Math.round(cf/tf*100) : 0;
  // 保護軸(枝②): 公開(=伏字・保管済み)カバレッジ = 公開済みコレクション / 全コレクション。
  //   旧 reviewRate(PIIレビュー完了率)は「レビュー」概念が実体なし＋dashboard.py で piiUn==piiTot のため恒等0だったため撤去。
  //   検出比(masked/detected)は全件マスクで構造的100%=偽満点になるため使わない(recon-summary 参照)。
  //   コレクションが1件も無い→null=N/A(測ったふりにしない)。値は実データで変動(下書き/中断コレクションで下がる)。
  const colReadyN = summary.ready_collections||0;
  const colTotN = summary.collections_total_all||0;
  const maskCoverage = (colTotN>0 ? Math.round(colReadyN/colTotN*100) : null);
  const protect = maskCoverage; // 保護=公開カバレッジ(総合スコア・準備度ゲートで使用・レーダーのProtect軸と同値)
  const policy = tw>0 ? Math.round((1-wsNo/tw)*100) : 100; // ポリシー割り当て率(設定済WSの割合)
  // allinone B1/B2: trust/tState/tBand は localInfer(実測ローカル推論)算出後に下で計算する(固定100撤去)。
  let catCount={};
  try{ Object.values(State.allFiles||{}).flat().forEach(f=>(f.categories||[]).forEach(c=>{catCount[c]=(catCount[c]||0)+1;})); }catch(e){}
  const catTotal=Object.values(catCount).reduce((a,b)=>a+b,0)||1;
  const cats=Object.entries(catCount).sort((a,b)=>b[1]-a[1]).slice(0,5).map(([name,count])=>({name,count,pct:Math.round(count/catTotal*100)}));
  let auditTotal=null, faith=null, chromaBytes=null, freeBytes=null;
  try{ const a=await API.get('/api/audit-logs?limit=1'); auditTotal=a&&a.total; }catch(e){}
  try{ const rq=await API.get('/api/stats/rag-quality?days=7'); const fa=((rq&&rq.quality_trend)||[]).map(x=>x.avg_faithfulness).filter(v=>v!=null); if(fa.length) faith=Math.round(fa.reduce((a,b)=>a+b,0)/fa.length*100); }catch(e){}
  try{ const pf=await API.get('/api/stats/performance?days=7'); if(pf&&pf.disk){chromaBytes=pf.disk.chroma_bytes; freeBytes=pf.disk.free_bytes;} }catch(e){}
  const MB=b=> (b==null?'—':(b/1048576).toFixed(1)+' MB');
  const GB=b=> (b==null?'—':(b>=1073741824?(b/1073741824).toFixed(1)+' GB':(b/1048576).toFixed(1)+' MB'));
  const lastPub = summary.last_publish_at ? new Date(summary.last_publish_at) : null;
  // 正直化①: MCP連携ツール数の実数を /api/mcp/config(tools[]) から取得（固定11撤去）。
  //          viewer/権限失敗時は null → setMcp が '—' 表示（測ったふりにしない）。
  let mcpCount=null;
  try{ const mc=await API.get('/api/mcp/config'); if(mc&&Array.isArray(mc.tools)) mcpCount=mc.tools.length; }catch(e){}
  // 正直化④: 文書の鮮度は公開済みコレクションの last_published_at（実値）から生成（未供給の恒久「読み込み中…」を解消）。
  //          公開0件 → [] → A6 側が「公開履歴なし」を表示。一覧取得失敗時は summary.last_publish_at の1行にフォールバック。
  let freshRows=[];
  try{
    const _cols=await API.get('/api/collections');
    const _arr=Array.isArray(_cols)?_cols:((_cols&&_cols.items)||[]);
    freshRows=_arr.filter(c=>c&&c.last_published_at)
      .sort((a,b)=>new Date(b.last_published_at)-new Date(a.last_published_at))
      .slice(0,6).map(c=>{ const days=Math.floor((Date.now()-new Date(c.last_published_at).getTime())/86400000);
        return {name:c.name||lj('(untitled)','(無題)'), age:(days<=0?lj('today','今日'):days+lj('d ago','日前')), ok:days<=30}; });
  }catch(e){
    if(lastPub){ const days=Math.floor((Date.now()-lastPub.getTime())/86400000);
      freshRows=[{name:lj('Last publish','最終公開'), age:(days<=0?lj('today','今日'):days+lj('d ago','日前')), ok:days<=30}]; }
  }
  // allinone B1: ローカル推論軸を実状から毎レンダー算出（固定100撤去・外部宛先で下がる）。
  //   LLM(/api/settings/llm) と 埋め込み(/api/settings/embedding) のローカル性を各50点で合算。
  //   外部 openai_compat の非ローカル base_url（= 生PII外部送信リスク）が混じると減点される。
  //   admin限定EP → viewer/失敗時は null → N/A（測ったふりにしない・既存パターン踏襲）。
  const _isLocalHost=(u)=>{ if(!u) return false; try{ const h=new URL(u).hostname;
      return h==='localhost'||h==='127.0.0.1'||h==='0.0.0.0'||h==='host.containers.internal'
        ||/^10\./.test(h)||/^192\.168\./.test(h)||/^172\.(1[6-9]|2\d|3[01])\./.test(h);
    }catch(_){ return /localhost|127\.0\.0\.1|host\.containers\.internal/.test(String(u)); } };
  let localInfer=null;
  let llmIsLocal=null; // truth-fill: トップバー「Local LLM」ピルを実状で出すための実測ローカル性(固定文言撤去)
  try{
    const _llm=await API.get('/api/settings/llm');
    const _emb=await API.get('/api/settings/embedding');
    const _llmLocal = _llm ? (_llm.provider==='mock' ? true : _isLocalHost(_llm.base_url)) : false;
    const _embLocal = _emb ? ((_emb.provider==='local'||_emb.provider==='mlx') ? true : _isLocalHost(_emb.base_url)) : false;
    llmIsLocal = _llmLocal;
    localInfer = (_llmLocal?50:0) + (_embLocal?50:0);
  }catch(e){ localInfer=null; llmIsLocal=null; }
  // allinone B2: 総合スコアは「計測できた軸」の等重み平均（固定100の偽満点を撤去）。
  //   寄与軸: ローカル推論(実測) / 保護 / 分類 / ポリシー割り当て率。null軸(未計測)は対象外。
  //   重み付けは等重みのまま（黙った再加重なし）＝ disclosure に内訳を明示する。
  const _tAxes=[localInfer,protect,classify,policy].filter(v=>v!=null);
  const trust = _tAxes.length ? Math.round(_tAxes.reduce((a,b)=>a+b,0)/_tAxes.length) : null;
  const tState = trust==null?'—':trust>=80?'EXCELLENT':trust>=60?'GOOD':trust>=40?'GUARDED':'AT RISK';
  const tBand  = trust==null?lj('No measured axes','計測軸なし'):trust>=80?lj('Now in "Excellent" band','いまは「優秀」帯'):trust>=60?lj('Now in "Good" band','いまは「良好」帯'):trust>=40?lj('Now in "Guarded" band','いまは「要対処」帯'):lj('Now in "Risk" band','いまは「危険」帯');
  // radar-fix: 保護軸=保護カバレッジ(maskCoverage・公開済みコレクション/全コレクション・読み取りのみ)。コレクション0→null=N/A。
  //   根拠軸=回答の検索グラウンディング(summary.retrieval_score_avg 0–1)×100。回答0件→null=N/A。
  const maskAxis = maskCoverage;
  const evidence = (summary.retrieval_score_avg==null ? null : Math.round(summary.retrieval_score_avg*100));
  // 正直化③+finalround: レーダーの実測軸すべて（N/A軸は除外）から強み・弱みを再計算。
  const _rax=[{n:lj('Protect','保護'),v:maskAxis},{n:lj('Policy','ポリシー割り当て率'),v:policy},{n:lj('Classify','分類'),v:classify},{n:lj('Evidence','根拠'),v:evidence}].filter(a=>a.v!=null).sort((a,b)=>a.v-b.v);
  const radarWeak=_rax.length?(lj('Weak: ','弱点：')+_rax[0].n+' '+_rax[0].v):lj('Weak: —','弱点：—'), radarStrong=_rax.length?(lj('Strong: ','強み：')+_rax[_rax.length-1].n+' '+_rax[_rax.length-1].v):lj('Strong: —','強み：—');
  // sweep-fix-c-20260711: ボトルネックを固定"Policy"でなく準備度ゲート4種(取込/分類/保護/ポリシー)の
  //   実測最小値から動的に選ぶ。未計測(null)軸は候補から除外。BE不接触・gates と同一データ源。
  const _gateCand=[
    {key:'ingest',  label:lj('Ingest','取込'),   pc:ingest,   neck:lj('▲ Ingest is the bottleneck — add & scan a data source to raise readiness / score','▲ 取込がボトルネック — データソースを追加・スキャンして準備度・スコア向上')},
    {key:'classify',label:lj('Classify','分類'), pc:classify, neck:lj('▲ Classify is the bottleneck — classify more files to raise readiness / score','▲ 分類がボトルネック — ファイル分類を進めて準備度・スコア向上')},
    {key:'protect', label:lj('Protect','保護'),  pc:protect,  neck:lj('▲ Protect is the bottleneck — publish (mask) collections to raise readiness / score','▲ 保護がボトルネック — コレクションを公開(伏字化)して準備度・スコア向上')},
    {key:'policy',  label:lj('Policy','ポリシー'),pc:policy,   neck:'▲ '+wsNo+lj(' workspaces without a policy — set one to raise readiness / score',' WS にポリシー未設定 — 設定で準備度・スコア向上')},
  ].filter(g=>g.pc!=null);
  const _neck=_gateCand.length?_gateCand.slice().sort((a,b)=>a.pc-b.pc)[0]:null;
  const d = {
    lang: CYNOVELA_LANG, // finalround: 親フレームの現在言語をiframeへ伝播（静的テキスト/属性/レーダー軸の出し分け）
    trust:{score:trust, state:tState, band:tBand},
    readiness:{ bottleneck:(_neck?lj('Bottleneck ','ボトルネック ')+_neck.label+' '+_neck.pc+'%':lj('Bottleneck —','ボトルネック —')), neck:(_neck?_neck.neck:lj('▲ No measured gates yet','▲ 計測済みゲートがありません')),
      gates:[ {key:'ingest',label:lj('Ingest','取込'),pc:ingest}, {key:'classify',label:lj('Classify','分類'),pc:classify},
              {key:'protect',label:lj('Protect','保護'),pc:protect}, {key:'policy',label:lj('Policy','ポリシー'),pc:policy} ]},
    // truth-fill: Card①=利用者評価(いいね率%・feedback集計) / Card②=検索スコア平均(最上位cosine 0-1)。
    //   いずれも /api/dashboard/summary の実数。評価/回答データ無し(n=0)は null → A6側が N/A。
    kpi:{ faith:(summary.user_feedback_rate==null?null:summary.user_feedback_rate),
          faithN:(summary.user_feedback_n||0),
          recall:(summary.retrieval_score_avg==null?null:summary.retrieval_score_avg),
          recallBelow:(summary.retrieval_below_threshold_rate==null?null:summary.retrieval_below_threshold_rate),
          recallN:(summary.retrieval_score_n||0),
          pii:piiTot, audit:auditTotal },
    llmLocal: llmIsLocal, // 実測ローカル性(true=ローカル/false=外部宛先/null=未取得)。トップバーのピルへ。
    mcp: mcpCount, // 実数(/api/mcp/config の tools[].length)。外部送信KPI(egress)は撤去。
    radar:[maskAxis,policy,classify,evidence], // radar-4axis: ローカル推論軸を撤去し4軸ダイヤ化(上=保護/右=ポリシー/下=分類/左=根拠)。保護=公開カバレッジ/根拠=検索グラウンディングを実データ配線(該当データ無し→null=N/A維持)。localInfer計算ブロックは緑バッジ駆動のため残置。
    radarTakeaway:{ weak:radarWeak, strong:radarStrong }, // 実測軸のみから算出
    // allinone B2 + finalround: 総合スコアの内訳開示。マスク/根拠はレーダー表示（スコア非算入＝二重計上回避）。
    trustBreakdown:{ axes:[
        {n:lj('Local inference','ローカル推論'), v:localInfer, src:lj('Measured: LLM / embedding locality (penalized for external destinations)','実測: LLM・埋め込みのローカル性（外部宛先で減点）')},
        {n:lj('Protect','保護'), v:protect, src:lj('Protection coverage: ready (published & masked) collections / all collections','保護カバレッジ: 公開済み（伏字・保管済み）コレクション / 全コレクション')},
        {n:lj('Classify','分類'), v:classify, src:lj('Share of classified files','分類済みファイルの割合')},
        {n:lj('Policy assignment rate','ポリシー割り当て率'), v:policy, src:lj('Share of workspaces with an access policy (assignment coverage)','アクセスポリシーを設定したWSの割合（割り当てカバレッジ）')},
      ], note:lj('Equal-weight average of measured axes. Protect = protection coverage (same as the radar Protect axis). Evidence is shown on the radar only (not folded into this score).','計測できた軸の等重み平均。保護＝保護カバレッジ（レーダーの保護軸と同値）。根拠はレーダーのみ表示（このスコアには非算入）。') },
    // truth-fill: しきい値は設定の実値(summary.confidence_threshold)。注記は「集計中」固定文言を撤去し
    //   直近回答のしきい値割れ実数(retrieval_below_threshold_rate)を表示。回答データ無し→件数のみ正直表示。
    threshold:(summary.confidence_threshold!=null?summary.confidence_threshold:0.40),
    thrNote:(summary.retrieval_below_threshold_rate==null
      ? lj('Confidence threshold '+(summary.confidence_threshold!=null?summary.confidence_threshold:0.40)+' (setting · no answer data)','信頼度しきい値 '+(summary.confidence_threshold!=null?summary.confidence_threshold:0.40)+'（設定値・回答データなし）')
      : lj('Threshold '+(summary.confidence_threshold!=null?summary.confidence_threshold:0.40)+' · recent answers below threshold '+Math.round(summary.retrieval_below_threshold_rate*100)+'% (n='+(summary.retrieval_score_n||0)+')','しきい値 '+(summary.confidence_threshold!=null?summary.confidence_threshold:0.40)+' · 直近回答のしきい値割れ '+Math.round(summary.retrieval_below_threshold_rate*100)+'%（n='+(summary.retrieval_score_n||0)+'）')),
    cats:cats,
    store:{ pct: tc>0?Math.round(vc/tc*100):0, used:lj('Used ','使用 ')+MB(chromaBytes), free:lj('Free ','残り ')+GB(freeBytes),
            sub: fN(vc)+lj(' vectorized chunks · BGE-M3 (1024-dim)',' ベクトル化チャンク · BGE-M3（1024次元）') },
    fresh: freshRows, // 公開済みコレクションの実 last_published_at（未供給→恒久「読み込み中…」を解消）
    // truth-fill: 「根拠/マスク/棄権」分布バー・「注目すべき判断」リストは実データ源が無く恒久空枠だったため
    //   要素ごと撤去(a6-console側)。ここは実数の総数(累計メッセージ/セッション/本日クエリ)のみ供給。
    decisions:{ messages:fN(summary.total_messages), sessions:fN(summary.total_sessions), today:fN(summary.total_queries_today) }, // card化: 数字3点を実測値そのまま供給(EPキー不変)。ラベルはカード側に保持。
    // sweep-fix-gen-selfaudit-20260711: 固定「外部送信なし」主張を実測ローカル性(llmIsLocal)に基づく正直表示へ。
    //   外部宛先時は Track G で masked 強制送出のため、その事実を明示(虚偽の「送信なし」を撤去)。null=未取得。
    selfaudit:(llmIsLocal===null
      ? lj('LLM destination unknown','LLM宛先 不明')
      : (llmIsLocal
          ? lj('Local decision (no external transmission)','ローカル判定（外部送信なし）')
          : lj('External LLM destination — masked tier enforced','外部LLM宛先 — masked強制で送出'))),
    selfauditNext: lastPub ? (lj('Last publish ','最終公開 ')+lastPub.toLocaleDateString(CYNOVELA_LANG==='ja'?'ja-JP':'en-US')) : lj('Last publish —','最終公開 —'),
    actions:[
      {sev:'h',sevLabel:'H',title: wsNo+lj(' workspaces without a policy',' WS ポリシー未設定'), note:lj('Strengthen governance via settings','設定でガバナンス強化'), btn:lj('Settings','設定'), nav:'workspaces', gain:true},
      {sev:'l',sevLabel:'L',title:lj('Unclassified files ','未分類ファイル ')+Math.max(0,tf-cf)+lj(' items',' 件'), note:lj('Awaiting classification','分類待ち'), btn:lj('Classify','分類'), nav:'catalog'},
    ],
  };
  applyTo(d, 0);
};

function renderPipelineComponents(s, catCount) {
  // GUI修正8 #05 5-3 / P1p2 §5: 4コンポーネントカードを信号機ステータス付き 4 カラム固定で刷新
  const host = document.getElementById('pipeline-components');
  if (!host) return;
  const features = s.features || {};
  const lastPub = s.last_publish_at
    ? new Date(s.last_publish_at).toLocaleString(CYNOVELA_LANG === 'ja' ? 'ja-JP' : 'en-US')
    : lj('Not run','未実行');
  const sens = s.sensitivity_breakdown || {};
  const sensList = ['public', 'internal', 'confidential', 'restricted']
    .map(k => `${k} ${sens[k] || 0}`).join('  ');

  const totalFiles = s.total_files || 0;
  const classified = s.classified_files || 0;
  const piiTotal = s.pii_detections_total || 0;
  // ga-close-v3 PartX: pii_unreviewed_count (DEPRECATED・実体なし) の参照を撤去。
  const maskedSpans = s.masked_spans_total || 0;
  const wsTotal = s.total_workspaces || 0;
  const wsNoPolicy = s.ws_without_policy_count || 0;
  const wsWithPolicy = Math.max(0, wsTotal - wsNoPolicy);
  const ragBasic = s.rag_basic_count || 0;
  const ragAgentic = s.rag_agentic_count || 0;
  const zeroHit = s.zero_hit_count || 0;
  const totalQ = s.total_messages || 0;
  const todayQ = s.total_queries_today || 0;
  const zeroHitRate = totalQ > 0 ? Math.round((zeroHit / totalQ) * 100) : 0;
  const ingest24h = s.ingest_24h || 0;
  const totalSources = s.total_sources || 0;

  // P1p2 §5: 信号機ステータス
  const signal = {
    ok:    { dot: '🟢', label: lj('OK','正常') },
    warn:  { dot: '🟡', label: lj('Review','要確認') },
    error: { dot: '🔴', label: lj('Error','エラー') },
  };
  const ingestStatus = totalSources === 0 ? 'error' : (totalFiles === 0 ? 'warn' : 'ok');
  const classifyStatus = (features.metadata_engine === false) ? 'error'
                        : (totalFiles > 0 && classified < totalFiles) ? 'warn' : 'ok';
  const guardStatus = (features.data_guardrails === false) ? 'error'
                     : ((piiTotal > 0 && maskedSpans === 0) || wsNoPolicy > 0) ? 'warn' : 'ok';
  const queryStatus = (features.session_history === false) ? 'error'
                     : (zeroHitRate > 20) ? 'warn' : 'ok';

  // 上位3カテゴリ (Classify カード用)
  const topCats = Object.entries(catCount || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  const topCatsLabel = topCats.length
    ? topCats.map(([n, c]) => `${escapeHtml(n)} ${c}`).join('  ')
    : (CYNOVELA_LANG === 'ja' ? 'データなし' : 'No data');

  // P1p2 §5: 信号機 (🟢🟡🔴) を右上に表示
  const card = (palette, icon, title, bodyHtml, statusLabel, sigKey) => {
    const sig = signal[sigKey] || signal.ok;
    return `
    <div class="cynovela-component-card" style="background:${palette.bg};border:1px solid ${palette.border};
                border-radius:10px;padding:14px 16px;min-width:0;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <div style="font-size:22px;">${icon}</div>
        <div style="font-size:15px;font-weight:700;color:${palette.fg};flex:1;">${title}</div>
        <div title="${escapeHtml(sig.label)}" style="font-size:18px;line-height:1;">${sig.dot}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:5px;">${bodyHtml}</div>
      <div style="font-size:14px;color:${palette.fg};opacity:0.78;margin-top:8px;">
        ${escapeHtml(statusLabel)}
      </div>
    </div>`;
  };
  // 行 (label + value) ヘルパー
  const row = (label, value, opts={}) => `
    <div style="display:flex;align-items:center;gap:8px;font-size:17px;">
      <span style="color:#475569;font-weight:600;min-width:100px;">${escapeHtml(label)}</span>
      <span style="color:${opts.color||'#1e293b'};font-weight:${opts.bold===false?500:700};font-size:${opts.size||'14px'};">${value}</span>
    </div>`;

  const bluePal   = {bg:'#f0f9ff', border:'#bae6fd', fg:'#0369a1'};
  const greenPal  = {bg:'#f0fdf4', border:'#bbf7d0', fg:'#15803d'};
  const amberPal  = {bg:'#fffbeb', border:'#fde68a', fg:'#92400e'};
  const purplePal = {bg:'#fdf4ff', border:'#e9d5ff', fg:'#7e22ce'};

  // Guard カード — PIIあり時にクリッカブル
  const guardSig = signal[guardStatus];
  const guardCard = `
    <div class="cynovela-component-card" style="background:${amberPal.bg};border:1px solid ${amberPal.border};
                border-radius:10px;padding:14px 16px;min-width:0;
                ${piiTotal>0?'cursor:pointer;':''}"
         ${piiTotal>0?`onclick="navigate('catalog')"`:''}>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
        <div style="font-size:22px;">🛡️</div>
        <div style="font-size:15px;font-weight:700;color:${amberPal.fg};flex:1;">${CYNOVELA_LANG==='en'?'Guard':'Guard（保護）'}</div>
        <div title="${escapeHtml(guardSig.label)}" style="font-size:18px;line-height:1;">${guardSig.dot}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:5px;">
        ${row(t('pii_label'), `${piiTotal}${piiTotal>0?' <span style="color:#d97706;">('+t('review_required')+')</span>':''}`)}
        ${row(lj('Guardrail events','Guardrail発動'), `${s.guardrail_count || 0}`)}
        ${row(t('guardrail_ws'), `${wsWithPolicy} / ${wsTotal}`)}
      </div>
      <div style="font-size:14px;color:${amberPal.fg};opacity:0.78;margin-top:8px;">
        ${features.data_guardrails !== false ? t('guardrail_active') : 'Guardrail OFF'}
      </div>
    </div>`;

  host.innerHTML = `
    <div style="margin:8px 0 18px 0;">
      <div class="section-label" style="margin-bottom:10px;">${t('cynovela_components')}</div>
      <div class="cynovela-component-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;">
        ${card(bluePal, '📦', CYNOVELA_LANG==='en'?'Ingest':'Ingest（取り込み）', `
          ${row(t('sources_label'), s.total_sources || 0)}
          ${row(t('files_label'), s.total_files || 0)}
          ${row(t('last_scan')||'最終スキャン', escapeHtml(lastPub), {bold:false, size:'13px', color:'#64748b'})}
          ${row(t('last_24h'), `+${ingest24h} ${t('ingested_unit')}`, {bold:false, size:'13px', color:'#64748b'})}
        `, t('scan_chunking_on'), ingestStatus)}
        ${card(greenPal, '🚀', CYNOVELA_LANG==='en'?'Classify':'Classify（分類）', `
          ${row(t('classified'), `${classified} / ${totalFiles}`)}
          ${row(lj('Top categories','上位カテゴリ'), topCatsLabel, {bold:false, size:'12px', color:'#475569'})}
          ${row(t('category_tags'), features.metadata_engine !== false ? t('active') : 'OFF',
                {color: features.metadata_engine !== false ? '#15803d' : '#94a3b8'})}
        `, features.metadata_engine !== false ? t('metadata_engine_on') : t('metadata_engine_off'),
          classifyStatus)}
        ${guardCard}
        ${card(purplePal, '💬', CYNOVELA_LANG==='en'?'Query':'Query（問い合わせ）', `
          ${row(t('todays_queries'), `${todayQ}`)}
          ${row(t('zero_hit_rate'), `${zeroHitRate}%`, {color: zeroHitRate>20?'#d97706':'#1e293b'})}
          ${row('Adaptive RAG', ragBasic+ragAgentic > 0
            ? `Basic ${ragBasic} / Agentic ${ragAgentic}`
            : '—',
            {bold:false, size:'13px', color:'#475569'})}
          ${row(t('total_label'), `${totalQ}`, {bold:false, size:'13px', color:'#64748b'})}
        `, features.session_history !== false ? t('chat_history_on') : t('history_off'),
          queryStatus)}
      </div>
    </div>`;
}

function _colPublishBtnLabel(col) {
  return _colIsPublished(col) ? '🔄 再Publish' : '🚀 Publish';
}

async function _doPublishWithChunking(id) {
  const cb = document.getElementById('publish-contextual-chunking');
  const on = !!(cb && cb.checked);
  // 設定を保存してから Publish を開始 (バックエンドは PATCH 同期反映)
  try {
    await API.patch('/api/chunking-config', { contextual: on });
  } catch (e) {
    // 別Publishが進行中なら 409 で拒否される。これは想定動作なのでサイレント。
    const msg = String(e?.message || '');
    if (!/Cannot change chunking|in progress/i.test(msg)) {
      showToast(`Chunking 設定保存失敗 (続行): ${e.message}`, 'warning');
    }
  }
  // GUI修正(2026-05-01) #6: コレクション単位の上書きを保存
  try {
    const csEl = document.getElementById('col-override-chunk-size');
    const coEl = document.getElementById('col-override-chunk-overlap');
    const rmEl = document.getElementById('col-override-rag-mode');
    const csVal = (csEl?.value ?? '').trim();
    const coVal = (coEl?.value ?? '').trim();
    const rmVal = (rmEl?.value ?? '').trim();
    await API.put(`/api/collections/${id}`, {
      chunk_size: csVal === '' ? null : parseInt(csVal, 10),
      chunk_overlap: coVal === '' ? null : parseInt(coVal, 10),
      rag_mode: rmVal === '' ? null : rmVal,
    });
    // ローカル State も更新しておく
    const col = State.collections.find(c => c.id === id);
    if (col) {
      col.chunk_size = csVal === '' ? null : parseInt(csVal, 10);
      col.chunk_overlap = coVal === '' ? null : parseInt(coVal, 10);
      col.rag_mode = rmVal === '' ? null : rmVal;
    }
  } catch (e) {
    // PUT /api/collections/{id} は draft 以外で 400 を返す。Publish済みや進行中は想定動作なのでサイレント。
    const msg = String(e?.message || '');
    if (!/Can only update draft|draft collections/i.test(msg)) {
      showToast(`Collection 設定保存失敗 (続行): ${e.message}`, 'warning');
    }
  }
  closeP3Modal();
  startPublishStream(id);
}

// DD-CYN-0032 B6: 追うのをやめる条件。
//   _STALL_NOTICE_MS を過ぎたら「遅れています」と1度だけ知らせるが、追うのはやめない。
//   _STALL_GIVEUP_MS を過ぎて初めてやめる。やめるときは、その旨と追い直し方を画面に出す。
//   従来は 90 秒動かないだけで黙って追うのをやめていた (追記200 の実測。取り込みそのものは
//   続いているのに、画面だけが伏字の段で止まって見えた)。
const _STALL_NOTICE_MS = 180000;    // 3分
const _STALL_GIVEUP_MS = 1800000;   // 30分
const _FETCH_FAIL_NOTICE = 5;       // 問い合わせがこの回数続けて失敗したら知らせる

async function _pollPublishJob(colId, jobId) {
  // polling-401-fix (2026-09-21): セッション切れ (API.token が空) の間は問い合わせない。
  //   叩いても 401 になるだけで、2 秒ごとに監査ログへ auth_failed が積もる。タイマー自体は残すので、
  //   再ログインで API.token が戻れば次の周期からそのまま追い直す (取り込みは画面と無関係に進む)。
  if (!API.token) return;
  let job;
  try {
    job = await API.get(`/api/jobs/${jobId}`);
    const _p0 = _publishPolls[colId];
    if (_p0) _p0.fetchFails = 0;
  } catch (e) {
    // DD-CYN-0032 B6: 問い合わせが続けて失敗したときも、黙って追い続けない。
    //   1度だけ知らせて、そのまま追い続ける (取り込みは画面と無関係に進むため)。
    const _pf = _publishPolls[colId];
    if (_pf) {
      _pf.fetchFails = (_pf.fetchFails || 0) + 1;
      if (_pf.fetchFails === _FETCH_FAIL_NOTICE && !_pf.fetchFailNotified) {
        _pf.fetchFailNotified = true;
        showToast(lj(
          'Cannot reach the server for progress. Ingestion keeps running; this screen will keep retrying.',
          '進み具合を問い合わせられません。取り込みは続いています。画面は問い合わせを続けます。'
        ), 'warning');
      }
    }
    return;
  }
  // SSE 互換の data オブジェクトに変換して既存 UI 関数を再利用
  const data = {
    stage: job.stage || job.status,
    current: job.progress || 0,
    total: job.total || 0,
    message: job.message || '',
    chunk_count: job.progress || 0,
  };
  updateProgressUI(colId, data);
  updatePublishFlowFromEvent(colId, data);
  // V3.5.0 取り込み可視化: 生ログ(右ペイン)へ段の変化/10%刻みtickを出す(フリッカー化しない)。
  if (typeof IngestViz !== 'undefined' && IngestViz.isTracking(colId)) {
    IngestViz.progress(colId, data);
  }

  // FIX: スタック検知 — 進捗が 90秒変化しないとタイムアウト扱い
  const poll = _publishPolls[colId];
  if (poll) {
    const now = Date.now();
    // fix-s1: 進捗値だけでなく message の変化も「生存」とみなす。大ファイル1本の処理中は
    //   バー値(ファイル数)が動かなくても backend がハートビート message を出し続けるため、
    //   それを生存合図として扱い、偽の90秒タイムアウトを防ぎつつ「止まって見える」を解消する。
    const _curMsg = job.message || '';
    if (poll.lastMessage === undefined) poll.lastMessage = _curMsg;
    const _moved = (poll.lastProgress !== (job.progress || 0)) || (poll.lastMessage !== _curMsg);
    if (!_moved && job.status === 'running') {
      const _still = now - poll.lastProgressAt;
      // 3分動かない: 1度だけ知らせる。追うのはやめない。
      if (_still > _STALL_NOTICE_MS && !poll.stallNotified) {
        poll.stallNotified = true;
        showToast(lj(
          'Progress has not changed for a while. Ingestion may still be running; this screen keeps following.',
          '進み具合がしばらく変わっていません。取り込みは続いている場合があります。画面は追い続けます。'
        ), 'warning');
      }
      // 30分動かない: ここで初めて追うのをやめる。やめたことと追い直し方を画面に出す。
      if (_still > _STALL_GIVEUP_MS) {
        _finishPublishPoll(colId);
        showToast(lj(
          'This screen stopped following (no change for 30 minutes). Ingestion itself may still be running. ' +
          'Reopen the collection and press “Latest log” to follow it again.',
          '画面が追うのをやめました (30分変わらなかったため)。取り込みそのものは続いている場合があります。' +
          'コレクションを開き直して「最新ログ」を押すと、また追えます。'
        ), 'error');
        finalizeProgressUI(colId, false, lj(
          'Stopped following. Reopen the collection and press “Latest log” to follow again.',
          '追うのをやめました。コレクションを開き直して「最新ログ」を押すと、また追えます。'
        ));
        return;
      }
    }
    if (_moved) {
      poll.lastProgress = job.progress || 0;
      poll.lastMessage = _curMsg;
      poll.lastProgressAt = now;
      poll.stallNotified = false;
    }
    // FIX: onProgress コールバック（クイックスタートのトースト更新等）
    if (typeof poll.options?.onProgress === 'function' && job.status === 'running') {
      try {
        poll.options.onProgress(job.progress || 0, job.total || 0, job.stage || '');
      } catch (e) { console.error('onProgress error:', e); }
    }
  }

  if (job.status === 'completed') {
    const opts = (_publishPolls[colId] && _publishPolls[colId].options) || {};
    const _ivTrack = (typeof IngestViz !== 'undefined') && IngestViz.isTracking(colId);
    _finishPublishPoll(colId);
    finalizeProgressUI(colId, true);
    // v3.5.0 Phase2: 完了ログにマスキング件数・ラベル内訳を反映する。
    // 読み取り専用 publish-summary EP から実集計を取得し、取得失敗時は最小情報で表示。
    // V3.5.0 取り込み可視化: 追跡中なら IngestViz(3行サマリー/受領書)へ、未追跡なら従来表示。
    (async () => {
      let summary = {};
      try {
        summary = await API.get(`/api/collections/${job.collection_id}/publish-summary`);
      } catch (e) { summary = {}; }
      const cardData = {
        chunk_count: (summary.chunk_count != null ? summary.chunk_count : job.progress),
        collection_id: job.collection_id,
        pii_count: summary.pii_count || 0,
        excluded_count: summary.excluded_count || 0,
        // DD-CYN-0091 C: 飛ばしたファイルの一覧 (ファイル名+理由) を完了表示へ渡す
        skipped_details: summary.skipped_details || [],
        file_count: summary.file_count || 0,
        pii_labels: summary.pii_labels || null,
        classification_summary: summary.classification_summary || null,
        // receiptfix-20260723: async+poll 経路でも所要時間を受領書へ渡す (EP が additive に返す)
        elapsed_seconds: (summary.elapsed_seconds != null ? summary.elapsed_seconds : 0),
      };
      if (_ivTrack) {
        IngestViz.done(colId, cardData);
      } else {
        showToast(`Publish完了: ${job.progress}チャンク`, 'success');
        showPublishSummaryCard(cardData);
      }
    })();
    refreshAllData().then(() => renderCollections());
    if (typeof opts.onComplete === 'function') {
      try { opts.onComplete(job); } catch (e) { console.error('onComplete error:', e); }
    }
  } else if (job.status === 'failed') {
    const opts = (_publishPolls[colId] && _publishPolls[colId].options) || {};
    const _ivTrack = (typeof IngestViz !== 'undefined') && IngestViz.isTracking(colId);
    _finishPublishPoll(colId);
    finalizeProgressUI(colId, false, job.error || job.message);
    if (_ivTrack) IngestViz.fail(colId, job.error || job.message);
    else showToast(`Publish失敗: ${job.error || job.message}`, 'error');
    refreshAllData().then(() => renderCollections());
    if (typeof opts.onFail === 'function') {
      try { opts.onFail(job); } catch (e) { console.error('onFail error:', e); }
    }
  } else if (job.status === 'stopped') {
    const opts = (_publishPolls[colId] && _publishPolls[colId].options) || {};
    const _ivTrack = (typeof IngestViz !== 'undefined') && IngestViz.isTracking(colId);
    _finishPublishPoll(colId);
    finalizeProgressUI(colId, false, job.message || '停止しました');
    if (_ivTrack) IngestViz.stop(colId, job.message || '停止しました');
    else showToast('Publishを停止しました', 'warning');
    refreshAllData().then(() => renderCollections());
    if (typeof opts.onFail === 'function') {
      try { opts.onFail(job); } catch (e) { console.error('onFail error:', e); }
    }
  }
}

function _finishPublishPoll(colId) {
  const e = _publishPolls[colId];
  if (e) {
    clearInterval(e.intervalId);
    delete _publishPolls[colId];
  }
}

function _reattachPublishProgress() {
  for (const colId of Object.keys(_publishPolls)) {
    ensureProgressUI(colId);
  }
}

function createPublishFlowHtml(colId) {
  return `
    <div class="publish-flow-widget" data-col-id="${colId}"
         style="margin:8px 0 10px 0;padding:10px 12px;background:#0d1117;
                border-radius:8px;border:1px solid #21262d;">
      <div style="font-size:16px;color:#94a3b8;margin-bottom:8px;font-weight:600;">
        📦 ${bi('Publish pipeline','Publish パイプライン')}
      </div>
      <div style="display:flex;align-items:flex-start;gap:6px;overflow-x:auto;">
        ${PUBLISH_STEPS.map((s, i) => `
          <div class="pub-step ${s.id}" data-step="${s.id}"
               style="display:flex;flex-direction:column;align-items:center;
                      flex:1 1 0;min-width:86px;padding:6px 4px;">
            <div class="pub-icon"
                 style="position:relative;width:42px;height:42px;border-radius:50%;
                        background:#1a1a2e;border:2px solid #2d2d44;
                        display:flex;align-items:center;justify-content:center;font-size:19px;
                        transition:all 0.3s;flex-shrink:0;">
              ${s.icon}
            </div>
            <div style="font-size:12px;line-height:1.25;color:#94a3b8;margin-top:6px;text-align:center;white-space:nowrap;">
              ${_publishStepLabel(s)}
            </div>
            <div class="pub-info"
                 style="font-size:11px;color:#64748b;margin-top:1px;text-align:center;min-height:12px;">
              &nbsp;
            </div>
          </div>
          ${i < PUBLISH_STEPS.length-1 ? `
            <div class="pub-arrow pub-arr-${i}"
                 style="color:#2d2d44;font-size:15px;flex:0 0 auto;align-self:flex-start;padding-top:9px;">
              →
            </div>` : ''}
        `).join('')}
      </div>
    </div>`;
}

function _findPubFlow(colId) {
  return document.querySelector(`.publish-flow-widget[data-col-id="${colId}"]`);
}

function updatePublishFlowFromEvent(colId, data) {
  // SSEイベント (data.stage) から該当ステップを推定して点灯
  if (!data || !data.stage) return;
  const total = data.total || 1;
  const current = data.current || 0;
  // DD-CYN-0032 B6: 分母を実数に合わせる。
  //   分子は段によって単位が変わる (塊に切る段=ファイル数 / ベクター化の段=塊数)。
  //   ここは唯一の歯止めの無い書き込み口だったため、段が移ったあとも前の段の値が残り、
  //   隣に別の単位の値が並んで [177/39] のように壊れて見えることがあった。
  //   ①分子を分母で切り詰める ②段が移ったら前の段の表示を「その段の全部」で締める。
  const _shown = Math.max(0, Math.min(current, total));
  if (data.stage === 'chunking') {
    // 最初のファイル: ドキュメント読込→チャンク分割
    setPublishStep(colId, 'pub-parse', current > 0 ? 'done' : 'active');
    setPublishStep(colId, 'pub-chunk', 'active', `${_shown}/${total}`);
    // 段が移ったときに前段を締めるため、この段の分母 (ファイル数) を覚えておく。
    if (_publishPolls[colId]) _publishPolls[colId].lastChunkTotal = total;
  } else if (data.stage === 'embedding') {
    setPublishStep(colId, 'pub-parse', 'done');
    // 塊に切る段は終わっている。前段の分母 (ファイル数) で締め切って残さない。
    const _files = (_publishPolls[colId] && _publishPolls[colId].lastChunkTotal) || null;
    setPublishStep(colId, 'pub-chunk', 'done', _files ? `${_files}/${_files}` : null);
    setPublishStep(colId, 'pub-embed', 'active', `${_shown}/${total}`);
  } else if (data.stage === 'done') {
    ['pub-parse','pub-chunk','pub-embed','pub-store'].forEach(s => setPublishStep(colId, s, 'done'));
    setPublishStep(colId, 'pub-done', 'done', `${data.chunk_count || total}件`);
  } else if (data.stage === 'error' || data.stage === 'stopped') {
    // 現在 active のステップに error を反映
    const widget = _findPubFlow(colId);
    if (widget) {
      const active = widget.querySelector('.pub-icon[style*="rgba(59,130,246"]');
      if (active) {
        const stepEl = active.closest('.pub-step');
        const stepId = stepEl?.dataset?.step;
        if (stepId) setPublishStep(colId, stepId, 'error');
      }
    }
  }
}

function updateProgressUI(colId, data) {
  // wrap は card 配下とは限らない (リスト表示では別 <tr> に挿入されるため)
  const wrap = document.querySelector(`.publish-progress-wrap[data-col-id="${colId}"]`);
  if (!wrap) return;
  const bar = wrap.querySelector('.publish-progress-bar');
  const text = wrap.querySelector('.publish-progress-text');
  if (!bar || !text) return;
  const total = data.total || 1;
  const current = data.current || 0;
  const frac = Math.max(0, Math.min(1, current / total));
  // 実フェーズを副レンジへ写像して反映(時間予測はしない=裏のジョブ状態を映すだけ)。
  // chunking の current/total はファイル単位のため、小入力で 0% 固着していた問題を解消し、
  // フェーズ進行(読込→チャンク→ベクター化)が必ずバーに出るようにする。
  const stage = data.stage || '';
  let pct;
  if (stage === 'embedding') pct = Math.round(35 + frac * 64);   // 35%→99%
  else if (stage === 'done') pct = 100;
  else if (stage === 'chunking') pct = Math.round(frac * 35);     // 0%→35%
  else pct = Math.round(frac * 100);                              // 後方互換(未知stage)
  pct = Math.max(0, Math.min(100, pct));
  bar.style.width = `${pct}%`;
  // バー直下は言語中立な % のみ(詳細件数は右の生ログ側に出る)。
  // 旧実装は backend の日本語 message を出していたため英語UIに日本語が混じっていた。
  text.textContent = `${pct}%`;
}

function finalizeProgressUI(colId, success, errMsg) {
  const wrap = document.querySelector(`.publish-progress-wrap[data-col-id="${colId}"]`);
  if (!wrap) return;
  if (!success) {
    const text = wrap.querySelector('.publish-progress-text');
    if (text) text.textContent = `エラー: ${errMsg || ''}`;
  }
  setTimeout(() => {
    wrap.classList.add('fade-out');
    setTimeout(() => {
      // 親が <td> の場合はラッパー <tr.publish-progress-tr> ごと削除する
      const tr = wrap.closest('tr.publish-progress-tr');
      (tr || wrap).remove();
    }, 600);
  }, 1000);
}

function ensurePipelineWidget() {}
function _attachPipelineToLastBubble() { return null; }

function showPublishResult(pipelineResult) {
  const resultEl = document.getElementById('publish-result');
  const linesEl = document.getElementById('publish-result-lines');
  if (!resultEl || !linesEl || !pipelineResult) return;
  linesEl.innerHTML = (pipelineResult.summary_lines || [])
    .map(line => `<li>${escapeHtml(line)}</li>`).join('');
  resultEl.style.display = 'block';
  // Workspace詳細パネルが該当WSを表示中なら、Chunks/履歴の両タブを最新化
  if (_currentWorkspaceId === pipelineResult.workspace_id) {
    const panel = document.getElementById('ws-detail-panel');
    if (panel && panel.style.display !== 'none') {
      loadChunks(pipelineResult.workspace_id, _currentChunkFilter || 'all');
      loadPublishHistory(pipelineResult.workspace_id);
    }
  }
}

function _updateSessionStatsFromResult(result) {
  const ti = result?.token_info || {};
  const ad = result?.adaptive_rag || {};
  const pd = result?.pipeline_detail || {};
  const pt = parseInt(ti.prompt_tokens || 0, 10) || 0;
  const ct = parseInt(ti.completion_tokens || 0, 10) || 0;
  const tt = parseInt(ti.total_tokens || (pt + ct), 10) || 0;
  _sessionStats.queries += 1;
  _sessionStats.prompt_total += pt;
  _sessionStats.completion_total += ct;
  _sessionStats.total_total += tt;
  // used_tokens は最後の prompt+completion を「現在の会話コンテキスト概算」とみなす
  if (tt > 0) _sessionStats.used_tokens = tt;
  if (ti.tokens_per_second) _sessionStats.speeds.push(parseFloat(ti.tokens_per_second));
  if (ti.llm_time_ms) _sessionStats.llm_times_ms.push(parseFloat(ti.llm_time_ms));
  const mode = ad.mode || 'basic';
  if (mode === 'agentic') _sessionStats.modes.agentic += 1;
  else _sessionStats.modes.basic += 1;
  if ((pd.chunks_sent_to_llm || 0) === 0) _sessionStats.zero_hits += 1;
  _sessionStats.last = {
    ...ti,
    mode,
    search_ms: Math.round(pd.search_latency_ms || 0),
    llm_ms:    Math.round(pd.llm_latency_ms || 0),
    chunks_used: pd.chunks_sent_to_llm || 0,
    timestamp: new Date(),
  };
}

function _renderTokenBadge(result) {
  const ti = result?.token_info || {};
  const ad = result?.adaptive_rag || {};
  const pd = result?.pipeline_detail || {};
  // #A: pipeline_detail だけでもサマリーは表示する (token_info が空でもタイミングは見せる)
  const hasAny = (ti && (ti.total_tokens || ti.completion_tokens)) ||
                 (pd && (pd.total_latency_ms || pd.llm_latency_ms));
  if (!hasAny) return '';
  const mode = (ad.mode === 'agentic') ? '🟢 Agentic RAG' : '🟢 Basic RAG';
  const searchMs = Math.round(pd.search_latency_ms || 0);
  const llmMs    = Math.round(pd.llm_latency_ms || 0);
  const totalMs  = Math.round(pd.total_latency_ms || 0);
  const hits     = pd.chunks_sent_to_llm || 0;
  const finish = (ti.finish_reason || '').toLowerCase();
  let finishLabel = '完了';
  if (finish === 'length') finishLabel = '<span class="tok-finish-warn">⚠️上限到達</span>';
  else if (finish && finish !== 'stop') finishLabel = escapeHtml(finish);
  const ts = new Date();
  const tsStr = `${String(ts.getMonth()+1).padStart(2,'0')}-${String(ts.getDate()).padStart(2,'0')} ${String(ts.getHours()).padStart(2,'0')}:${String(ts.getMinutes()).padStart(2,'0')}`;
  // #A: サマリー行に検索/LLM/合計/ヒット件数を直接表示
  // E-5: ms 表示を秒表示に統一
  const searchSec = (searchMs/1000).toFixed(2);
  const llmSec    = (llmMs/1000).toFixed(2);
  const totalSec  = (totalMs/1000).toFixed(2);
  // cloud-metrics-fix-20260628: トークン(入力/出力/合計)をサマリー行に1行で出す。
  //   従来は詳細(初期closed)に埋もれて一目で消費が見えなかった。詳細の折りたたみは温存。
  const _pt = ti.prompt_tokens, _ct = ti.completion_tokens, _tt = ti.total_tokens;
  const _hasTok = (_pt != null) || (_ct != null) || (_tt != null);
  const _tokTotal = (_tt != null) ? _tt : (Number(_pt || 0) + Number(_ct || 0));
  const tokLine = _hasTok
    ? `<br>入力: ${Number(_pt||0).toLocaleString()}tok　出力: ${Number(_ct||0).toLocaleString()}tok　合計: ${Number(_tokTotal).toLocaleString()}tok`
    : '';
  const summary = `${mode} ｜ 検索${searchSec}秒 ｜ LLM${llmSec}秒 ｜ 合計${totalSec}秒 ｜ ${hits}件ヒット${tokLine}`;
  const expanded = `
    <div class="tok-grid">
      入力: ${ti.prompt_tokens||0}tok　出力: ${ti.completion_tokens||0}tok　合計: ${ti.total_tokens||0}tok<br>
      速度: ${ti.tokens_per_second||0}tok/s　停止: ${finishLabel}　時刻: ${tsStr}<br>
      検索: ${searchSec}秒　LLM: ${llmSec}秒　チャンク: ${hits}件使用
    </div>`;
  // PHASE UI-2: フィードバックボタンは renderFeedbackButtons() に一本化したため削除済み。
  return `<div class="tok-badge" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
    <span style="flex:1 1 auto;min-width:0;">${summary}</span>
    <details style="flex-basis:100%"><summary>${bi('📊 Details ▼', '📊 詳細 ▼')}</summary>${expanded}</details></div>`;
}

function _renderExplainPanel(d) {
  const aclMsg = (d.acl_filtered_count || 0) > 0
    ? `そのうち${d.acl_filtered_count}件はあなたの閲覧権限の対象外だったため除外されました。`
    : '';
  return `
    <div class="pipeline-explain">
      <div class="pipe-explain-title">💡 回答の根拠</div>
      <div class="pipe-explain-body">
        Cynovelaは${d.total_chunks_searched || 0}個の文書断片の中から、あなたの質問に最も関連する${d.chunks_sent_to_llm || 0}件を選びました。
        ${aclMsg}
        選ばれた文書をAIに渡して回答を生成しています。
      </div>
      <div class="pipe-explain-meta">
        検索 ${((d.search_latency_ms||0)/1000).toFixed(2)}秒
        ${(d.rerank_latency_ms||0) > 0 ? ` / 絞り込み ${((d.rerank_latency_ms||0)/1000).toFixed(2)}秒` : ''}
        / AI生成 ${((d.llm_latency_ms||0)/1000).toFixed(2)}秒
      </div>
    </div>
  `;
}

function _renderDeveloperPanel(d) {
  const fmt = arr => Array.isArray(arr) ? arr.map(s => Number(s).toFixed(3)).join(', ') : 'N/A';
  return `
    <details class="pipeline-developer">
      <summary class="pipe-dev-summary">${bi('🔧 Developer Panel', '🔧 開発者パネル')}</summary>
      <pre class="pipe-dev-body">RAG Strategy:   ${escapeHtml(d.rag_strategy || '')}
Embedding:      ${escapeHtml(d.embedding_model || '')}
Total Chunks:   ${d.total_chunks_searched || 0}
ACL Filtered:   ${d.acl_filtered_count || 0}
Sent to LLM:    ${d.chunks_sent_to_llm || 0}

Latency:
  Search:  ${((d.search_latency_ms||0)/1000).toFixed(2)}秒
  Rerank:  ${((d.rerank_latency_ms||0)/1000).toFixed(2)}秒
  LLM:     ${((d.llm_latency_ms||0)/1000).toFixed(2)}秒
  Total:   ${((d.total_latency_ms||0)/1000).toFixed(2)}秒

Vector Scores: [${fmt(d.vector_scores)}]
BM25 Scores:   [${fmt(d.bm25_scores)}]
Rerank Scores: [${fmt(d.rerank_scores)}]

--- Prompt sent to LLM ---
${escapeHtml(d.prompt_sent_to_llm || '')}
</pre>
    </details>
  `;
}

async function loadChunkingPresetsForSettings() {
  // sweep-fix-gen-settings-chunkpreset-comingsoon-20260711:
  //   本セレクターは保存先(pipeline_presets)への永続化が未実装(onOk が TODO のまま)で、
  //   選択値は取り込み/Re-Publish に一切反映されない死に設定だった。
  //   「プリセット保存済/Re-Publish で反映」と誤認させる確認モーダルを撤去し、
  //   ComingSoon として無効化する（機能の捏造は行わない）。
  await renderChunkingPresetSelector('rag-chunking-presets-host', {});
  const host = document.getElementById('rag-chunking-presets-host');
  if (host) {
    const sel = document.getElementById('rag-chunking-presets-host-sel');
    if (sel) {
      sel.disabled = true;
      sel.style.opacity = '0.6';
      sel.style.cursor = 'not-allowed';
    }
    const badge = document.createElement('div');
    badge.style.cssText = 'font-size:14px;color:#94a3b8;margin-top:4px;';
    badge.textContent = lj('🚧 Coming Soon — this preset is not yet applied to ingestion / Re-Publish', '🚧 Coming Soon — このプリセットは取り込み / Re-Publish にまだ反映されません');
    host.appendChild(badge);
  }
}

function _ensureHealthGrid() {
  const host = document.getElementById('pipeline-health-grid');
  if (!host || _healthGridInited) return;
  host.innerHTML = PIPELINE_COMPONENTS.map(c => `
    <div class="health-cell">
      <span id="health-${c.id}" class="health-indicator health-pending"
            title="checking..."></span>
      <span class="health-name">
        <span class="en">${escapeHtml(c.en)}</span><span class="ja">${escapeHtml(c.ja)}</span>
      </span>
    </div>`).join('');
  _healthGridInited = true;
}

async function updatePipelineHealth() {
  _ensureHealthGrid();
  for (const comp of PIPELINE_COMPONENTS) {
    let status = 'error';
    try { status = await comp.check(); } catch (_) { status = 'error'; }
    const el = document.getElementById(`health-${comp.id}`);
    if (!el) continue;
    el.className = `health-indicator health-${status}`;
    el.title = (status === 'ok') ? 'Operational' :
               (status === 'warn') ? 'Degraded' : 'Unavailable';
  }
}

// ===== Stage 3: DOMContentLoaded blocks moved from FIX app.js =====

// --- Block #8 (FIX app.js L8852-L8852) ---
document.addEventListener('DOMContentLoaded', () => _syncRagDisplayModeUI(ragDisplayMode));
