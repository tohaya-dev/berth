// guardrails.js - Cynovela v13

function renderAiReadinessScore(summary, totalFiles, readyCol, piiCount) {
  const host = document.getElementById('ai-readiness-host');
  if (!host) return;
  const totalChunks = summary.total_chunks || 0;
  const vecChunks   = summary.vectorized_chunks || 0;
  // ga-close-v3 PartX: pii_unreviewed_count は DEPRECATED (サーバ側で
  //   pii_detections_total の写し = 「未処理」という実体が無い)。読むのをやめる。
  const piiTotal    = summary.pii_detections_total || 0;
  const maskedSpans = summary.masked_spans_total || 0;
  const wsTotal     = summary.total_workspaces || 0;
  const wsNoPolicy  = summary.ws_without_policy_count || 0;
  // スコア計算
  const vecScore  = totalChunks > 0 ? (vecChunks / totalChunks) * 50 : 0;
  // ga-close-v3 PartX: 伏字が効いているか (検出ゼロなら満点)。
  //   「対処率」は測っていないので出さない。測れているのは「個人情報を含む塊が
  //   あるのに伏字が1件も記録されていない」という失敗形の有無だけ。
  const piiScore = piiTotal > 0 ? (maskedSpans > 0 ? 30 : 0) : 30;
  // Guardrail適用率
  const policyScore = wsTotal > 0 ? ((wsTotal - wsNoPolicy) / wsTotal) * 20 : 20;
  const total = Math.round(vecScore + piiScore + policyScore);
  const color = total >= 80 ? '#16a34a' : (total >= 50 ? '#d97706' : '#dc2626');
  const colorBg = total >= 80 ? '#f0fdf4' : (total >= 50 ? '#fffbeb' : '#fef2f2');
  host.innerHTML = `
    <div class="card" style="background:${colorBg};border:1px solid ${color}33;padding:16px 20px;margin-bottom:16px;position:relative;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <span style="font-size:18px;">🎯</span>
        <span class="section-label" style="margin:0;">${bi('AI Readiness Score', 'AI準備スコア')}</span>
        <span class="help-icon" id="readiness-help-btn" title="${lj('How the score is calculated','スコアの計算方法')}">?</span>
        <span style="margin-left:auto;font-size:20px;font-weight:800;color:${color};">${total}%</span>
      </div>
      <div class="readiness-help-popover" id="readiness-help-popover" style="display:none;">
        <div class="help-popover-title">${bi('What is AI Readiness Score', 'AI準備スコアとは')}</div>
        <div class="help-popover-body">
          <p style="margin:0 0 8px 0;">${lj('A 0–100% score showing how ready your data is for AI use.','データがAIで使える状態かを0〜100%で示すスコアです。')}</p>
          <div class="help-score-row">
            <span class="help-score-weight">50%</span>
            <span>${lj('Vectorized chunks ÷ total chunks','ベクター化済みチャンク数 ÷ 総チャンク数')}</span>
          </div>
          <div class="help-score-row">
            <span class="help-score-weight">30%</span>
            <span>${lj('Masking is in effect (masked items > 0 where personal information was found)','伏字が効いていること（個人情報を含む塊がある場合、伏字が1件以上記録されている）')}<br>
                  <small>${lj('(Full score if no personal information was found)','（個人情報が検出されなかった場合は満点）')}</small></span>
          </div>
          <div class="help-score-row">
            <span class="help-score-weight">20%</span>
            <span>${lj('Guardrail-applied WS ÷ total WS','Guardrail適用済みWS数 ÷ 総WS数')}</span>
          </div>
          <hr style="margin:8px 0;border:none;border-top:1px solid #e2e8f0;">
          <div class="help-score-legend">
            <span style="color:#16a34a">■ ${lj('80%+', '80%以上')}</span> ${lj('Demo ready','デモ準備完了')}<br>
            <span style="color:#d97706">■ ${lj('50–79%', '50〜79%')}</span> ${lj('Needs improvement','要改善あり')}<br>
            <span style="color:#dc2626">■ ${lj('Below 50%', '50%未満')}</span> ${lj('Action required','要対処')}
          </div>
        </div>
      </div>
      <div class="ai-readiness-bar-track">
        <div class="ai-readiness-bar-fill" style="width:${total}%;background:${color};"></div>
      </div>
      <div style="display:flex;gap:18px;flex-wrap:wrap;font-size:17px;color:#475569;margin-top:10px;">
        <span>${lj('Vectorized','ベクター化済み')}: <strong>${vecChunks}${lj('','件')}</strong></span>
        <span>${lj('Chunks with personal information','個人情報を含む塊')}: <strong>${piiTotal}${lj('','件')}</strong></span>
        <span>${lj('Masked items (total)','伏字の総件数')}: <strong style="color:${(piiTotal>0&&maskedSpans===0)?'#d97706':'#16a34a'};">${maskedSpans}${lj('','件')}</strong>${(piiTotal>0&&maskedSpans===0)?' ⚠️':''}</span>
        <span>${lj('WS without policy','ポリシー未適用WS')}: <strong style="color:${wsNoPolicy>0?'#d97706':'#16a34a'};">${wsNoPolicy}${lj('','件')}</strong>${wsNoPolicy>0?' ⚠️':''}</span>
      </div>
    </div>`;
  // ヘルプポップオーバーのトグル
  const btn = document.getElementById('readiness-help-btn');
  const pop = document.getElementById('readiness-help-popover');
  if (btn && pop) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      pop.style.display = pop.style.display === 'none' ? 'block' : 'none';
    });
    pop.addEventListener('click', (e) => e.stopPropagation());
    if (!window._readinessHelpDocClickBound) {
      document.addEventListener('click', () => {
        const p = document.getElementById('readiness-help-popover');
        if (p) p.style.display = 'none';
      });
      window._readinessHelpDocClickBound = true;
    }
  }
}

function renderStatsGridV2(totalFiles, piiCount, readyCol, totalCol, vecRate) {
  const grid = $('stats-grid');
  if (!grid) return;
  const piiBg = piiCount > 0 ? '#fffbeb' : '#fff';
  const piiCursor = piiCount > 0 ? 'cursor:pointer;' : '';
  const piiClick = piiCount > 0 ? `onclick="navigate('catalog')"` : '';
  grid.innerHTML = `
    <div class="stat-card">
      <div class="stat-icon">📄</div>
      <div class="stat-label">${t('total_files')}</div>
      <div class="card-value-lg">${totalFiles}</div>
      <div class="card-sub">${State.sources.length} ${t('sources_unit')}</div>
    </div>
    <div class="stat-card" style="background:${piiBg};${piiCursor}" ${piiClick}>
      <div class="stat-icon">🔒</div>
      <div class="stat-label">${t('pii_detected_card')}</div>
      <div class="card-value-lg" style="color:${piiCount>0?'#d97706':'#1e293b'};">${piiCount}</div>
      <div class="card-sub">${piiCount > 0 ? t('review_required') : t('not_detected')}</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon">📦</div>
      <div class="stat-label">Collections</div>
      <div class="card-value-lg">${readyCol}<span style="font-size:18px;color:#94a3b8;">/${totalCol}</span></div>
      <div class="card-sub">${readyCol} Ready</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon">📊</div>
      <div class="stat-label">${t('vectorization_rate')}</div>
      <div class="card-value-lg">${vecRate}<span style="font-size:18px;color:#94a3b8;">%</span></div>
      <div style="width:100%;height:4px;background:#e2e8f0;border-radius:2px;margin-top:6px;overflow:hidden;">
        <div style="width:${vecRate}%;height:100%;background:#0284c7;border-radius:2px;"></div>
      </div>
    </div>`;
}

async function renderGuardrails() {
  // GUI修正 #16: 解説文は ❓ヘルプボタンに集約。常時表示を取りやめる
  const explainer = document.getElementById('guardrail-explainer');
  if (explainer) explainer.innerHTML = '';

  // F2-3: 4セクション構成
  await renderGuardrailsSummary().catch(()=>{});
  // セクション2 (ポリシー管理) は本関数の後段で描画する


  // BETA-pagination
  const pp = State.policyPager;
  const pqs = new URLSearchParams({ limit: pp.limit, offset: (pp.page - 1) * pp.limit });
  if (pp.q) pqs.set('q', pp.q);
  try {
    const pres = await API.get(`/api/policies?${pqs}`);
    if (pres && pres.items !== undefined) {
      State.policies = pres.items;
      State.policyPager.total = pres.total;
    } else {
      State.policies = pres;
    }
  } catch (e) {
    // DD-CYN-0089 §6-B: 同上。
    State.policies = [];
    if (typeof showToast === 'function') {
      showToast(lj(`Could not read the policies: ${(e && e.message) || ''}`,
        `ポリシーの一覧を読めませんでした: ${(e && e.message) || ''}`), 'error');
    }
  }
  _pagerCallbacks['policy'] = {
    page: (n) => { State.policyPager.page = Math.max(1, n); renderGuardrails(); },
    limit: (n) => { State.policyPager.limit = n; State.policyPager.page = 1; renderGuardrails(); },
  };
  // F2-3 セクション2: ポリシー管理テーブル
  const policyRows = State.policies.map(p => {
    const rules = Array.isArray(p.rules) ? p.rules : [];
    const wsCount = p.workspace_count != null ? p.workspace_count : '—';
    const trigCount = p.trigger_count_7d != null ? p.trigger_count_7d : 0;
    const lastTrig = p.last_triggered ? String(p.last_triggered).slice(0, 16).replace('T', ' ') : '—';
    const active = p.state === 'active';
    return `<tr style="border-bottom:1px solid #f0f0f0;">
      <td style="padding:10px 12px;font-weight:600;">🛡️ ${escapeHtml(p.name)}</td>
      <td style="padding:10px 12px;text-align:right;">${rules.length}</td>
      <td style="padding:10px 12px;text-align:right;">${wsCount}</td>
      <td style="padding:10px 12px;text-align:right;">${trigCount}</td>
      <td style="padding:10px 12px;font-size:13px;color:#64748b;">${escapeHtml(lastTrig)}</td>
      <td style="padding:10px 12px;">
        <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
          <input type="checkbox" ${active?'checked':''} onchange="togglePolicyActive('${p.id}', this.checked)">
          <span class="tag ${active?'tag-green':'tag-grey'}">${active?'active':'inactive'}</span>
        </label>
      </td>
      <td style="padding:10px 12px;text-align:right;white-space:nowrap;">
        <button class="btn btn-sm btn-ghost" data-role-min="admin" onclick="openEditPolicyModal('${p.id}')">${lj('Edit','編集')}</button>
        <button class="btn btn-sm" data-role-min="admin" onclick="deletePolicyConfirm('${p.id}','${escapeHtml(p.name)}')"
                style="background:#fff;border:1px solid #fecaca;color:#991b1b;">🗑</button>
      </td>
    </tr>`;
  }).join('');
  // uifix v1 K (2026-05-24): ポリシー管理表見出し + 空表メッセージを lj() で二言語化
  $('policies-list').innerHTML = `
    <table style="width:100%;border-collapse:collapse;font-size:14px;min-width:760px;">
      <thead><tr style="background:#f8fafc;">
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${lj('Policy name','ポリシー名')}</th>
        <th style="text-align:right;padding:10px 12px;font-size:13px;color:#475569;">${lj('Rules','ルール数')}</th>
        <th style="text-align:right;padding:10px 12px;font-size:13px;color:#475569;">${lj('Applied WS','適用WS')}</th>
        <th style="text-align:right;padding:10px 12px;font-size:13px;color:#475569;">${lj('7-day triggers','7日トリガー')}</th>
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${lj('Last fired','最終発動')}</th>
        <th style="text-align:left;padding:10px 12px;font-size:13px;color:#475569;">${lj('State','状態')}</th>
        <th style="text-align:right;padding:10px 12px;font-size:13px;color:#475569;">${lj('Actions','操作')}</th>
      </tr></thead>
      <tbody>${policyRows || `<tr><td colspan="7" style="text-align:center;padding:20px;color:#94a3b8;">${lj('No policies','ポリシーがありません')}</td></tr>`}</tbody>
    </table>`;
  // BETA-pagination: ページャ描画
  const policyPagerEl = document.getElementById('policy-pager');
  if (policyPagerEl) {
    policyPagerEl.innerHTML = _renderPager({
      key: 'policy',
      page: State.policyPager.page,
      limit: State.policyPager.limit,
      total: State.policyPager.total,
    });
  }

  // F2-3 セクション3: WS保護カバレッジ
  await renderGuardrailsCoverage().catch(()=>{});
  // F2-3 セクション4: 直近の検知イベント
  await renderRecentGuardrailEvents().catch(()=>{});
  // コンプライアンスチェックリスト（4セクションの下に表示）
  renderComplianceChecklist().catch(()=>{});

  // F2-2: ポリシーマトリクス・PII検出パネル・監査ログ は Guardrails から撤去
  // - 監査ログは「管理 > 監査ログ」ページに移動
  // - PII検出は監査ログ(category=security)で代替
  // - マトリクスは個別ポリシーで代替
  // 関数本体（loadPolicyMatrix / loadPiiDetectionList / loadAuditLogsEnhanced）は残置
  applyRoleRestrictions();
}

async function renderGuardrailsSummary() {
  const host = document.getElementById('guardrails-summary');
  if (!host) return;
  let summary = {};
  try { summary = await API.get('/api/dashboard/summary') || {}; } catch (e) { summary = {}; }
  const totalWs = summary.total_workspaces || 0;
  const wsWithoutPolicy = summary.ws_without_policy_count || 0;
  const protectedWs = Math.max(0, totalWs - wsWithoutPolicy);
  // honest fix (pii-fiction): 「PII未対処」(実体なし=pii_unreviewed_count)を実伏字スパン総数へ置換。
  const maskedSpans = summary.masked_spans_total || 0;
  // 直近24h security 検知（audit-logs から）
  let recent24h = 0;
  try {
    const al = await API.get('/api/audit-logs?category=security&limit=200');
    const items = (al && al.items) || [];
    const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
    recent24h = items.filter(r => {
      const ts = r.timestamp ? Date.parse(r.timestamp) : 0;
      return ts >= dayAgo;
    }).length;
  } catch (e) { /* ignore */ }
  const card = (icon, label, value, accent) => `
    <div class="stat-card" style="background:#fff;border:1px solid ${accent.border};
         border-left:4px solid ${accent.bar};border-radius:10px;padding:14px;">
      <div style="font-size:13px;color:#64748b;">${icon} ${label}</div>
      <div style="font-size:32px;font-weight:800;color:${accent.fg};line-height:1.1;margin-top:4px;">${value}</div>
    </div>`;
  const greenAcc  = { bar:'#10b981', border:'#bbf7d0', fg:'#15803d' };
  const redAcc    = { bar:'#ef4444', border:'#fecaca', fg:'#991b1b' };
  const orangeAcc = { bar:'#f59e0b', border:'#fde68a', fg:'#92400e' };
  const blueAcc   = { bar:'#3b82f6', border:'#bfdbfe', fg:'#1e40af' };
  // uifix v1 K (2026-05-24): 4 カードを lj() で二言語化
  host.innerHTML =
      card('🛡️', lj('Protected WS', '保護済みWS'),     `${protectedWs} / ${totalWs}`, wsWithoutPolicy>0 ? orangeAcc : greenAcc)
    + card('⚠️', lj('Unprotected WS', '未保護WS'),     `${wsWithoutPolicy}`,          wsWithoutPolicy>0 ? redAcc   : greenAcc)
    + card('⚡', lj('Detected (24h)', '直近24h検知'),  `${recent24h}`,                blueAcc)
    + card('🔒', lj('Masked PII', '伏字済みPII'),     `${maskedSpans}`,            greenAcc);
}

async function renderRecentGuardrailEvents() {
  const host = document.getElementById('guardrails-recent-events');
  if (!host) return;
  let items = [];
  try {
    const res = await API.get('/api/audit-logs?category=security&limit=5');
    items = (res && res.items) || [];
  } catch (e) { items = []; }
  if (!items.length) {
    host.innerHTML = `
      <div style="padding:14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;color:#15803d;">
        ${lj('✅ No recent security detections','✅ 直近のセキュリティ検知はありません')}
      </div>
      <div style="margin-top:8px;text-align:right;">
        <a href="javascript:navigate('audit')" style="color:#1e40af;font-size:13px;">${lj('View full audit log →','監査ログ全件を見る →')}</a>
      </div>`;
    return;
  }
  const cards = items.map(e => {
    const ts = e.timestamp ? String(e.timestamp).slice(0, 19).replace('T', ' ') : '—';
    return `<div style="background:#fff;border:1px solid #fde68a;border-left:4px solid #f59e0b;
         border-radius:8px;padding:10px 14px;margin-bottom:8px;">
      <div style="display:flex;justify-content:space-between;font-size:13px;color:#92400e;">
        <strong>${escapeHtml(e.action || '')}</strong>
        <span>${escapeHtml(ts)}</span>
      </div>
      <div style="font-size:13px;color:#475569;margin-top:4px;">
        ${lj('Target','対象')}: ${escapeHtml(e.target || '—')}
      </div>
    </div>`;
  }).join('');
  host.innerHTML = cards + `
    <div style="margin-top:8px;text-align:right;">
      <a href="javascript:navigate('audit')" style="color:#1e40af;font-size:13px;">${lj('View full audit log →','監査ログ全件を見る →')}</a>
    </div>`;
}

async function loadPiiDetectionList() {
  const host = document.getElementById('pii-detection-list');
  if (!host) return;
  host.innerHTML = '<div style="padding:12px;color:#94a3b8;">' + bi('Loading...', '読み込み中...') + '</div>';
  try {
    // BETA-pagination
    const p = State.piiPager;
    const qs = new URLSearchParams({ limit: p.limit, offset: (p.page - 1) * p.limit });
    const data = await API.get(`/api/pii-detections?${qs}`);
    const items = data.items || [];
    State.piiPager.total = data.total != null ? data.total : items.length;
    _pagerCallbacks['pii'] = {
      page: (n) => { State.piiPager.page = Math.max(1, n); loadPiiDetectionList(); },
      limit: (n) => { State.piiPager.limit = n; State.piiPager.page = 1; loadPiiDetectionList(); },
    };
    if (!items.length) {
      host.innerHTML = `
        <div style="padding:14px;background:#f0fdf4;border:1px solid #bbf7d0;
                    border-radius:8px;color:#15803d;font-size:18px;">
          ${lj('✅ No PII detected — all chunks are searchable by RAG','✅ PII 検出なし — 全チャンクが RAG 検索対象です')}
        </div>`;
      return;
    }
    const total = items.reduce((s, i) => s + (i.pii_chunks || 0), 0);
    const rows = items.map(i => `
      <tr style="border-bottom:1px solid #fde68a;">
        <td style="padding:8px 12px;font-size:17px;font-weight:600;color:#92400e;">
          ${escapeHtml(i.source_doc || '(unnamed)')}
        </td>
        <td style="padding:8px 12px;font-size:17px;color:#b45309;">
          ${escapeHtml(i.collection_name || '—')}
        </td>
        <td style="padding:8px 12px;font-size:17px;color:#92400e;text-align:right;">
          <strong>${i.pii_chunks}</strong> ${lj('chunks','チャンク')}
        </td>
        <td style="padding:8px 12px;font-size:16px;color:#78350f;">
          ${i.excluded > 0 ? `🚫 ${lj(`${i.excluded} excluded`,`${i.excluded}件除外`)}` : lj('🔒 Masked','🔒 マスク')}
        </td>
      </tr>`).join('');
    host.innerHTML = `
      <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;overflow-x:auto;">
        <div style="padding:10px 14px;background:rgba(251,191,36,0.15);
                    font-size:17px;color:#92400e;font-weight:700;">
          ${lj('Detected PII','検出済み PII')}: ${items.length}${lj(' docs','ドキュメント')} / ${lj('total','累計')} ${total} ${lj('chunks','チャンク')}
        </div>
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr style="background:rgba(251,191,36,0.08);">
              <th style="text-align:left;padding:8px 12px;font-size:16px;color:#92400e;">${lj('Document','ドキュメント')}</th>
              <th style="text-align:left;padding:8px 12px;font-size:16px;color:#92400e;">Collection</th>
              <th style="text-align:right;padding:8px 12px;font-size:16px;color:#92400e;">${lj('Detections','検出件数')}</th>
              <th style="text-align:left;padding:8px 12px;font-size:16px;color:#92400e;">${lj('Action','処理')}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div id="pii-pager"></div>`;
    const piiPagerEl = document.getElementById('pii-pager');
    if (piiPagerEl) {
      piiPagerEl.innerHTML = _renderPager({
        key: 'pii',
        page: State.piiPager.page,
        limit: State.piiPager.limit,
        total: State.piiPager.total,
      });
    }
  } catch (e) {
    host.innerHTML = `<div style="padding:12px;color:#ef4444;">${lj('Fetch failed','取得失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadPiiMode() {
  try {
    const r = await API.get('/api/settings/pii-mode');
    // ga-close-v3 PartX: 画面から選択肢を外したため、実際に効いている値を読み取り専用で表示する。
    const view = document.getElementById('pii-detection-mode-view');
    if (view && r.mode) {
      const label = {
        lite:     lj('Lite (regex only, minimal CPU)', '軽量（regex のみ・CPU最小）'),
        standard: lj('Standard (regex + GiNZA NER + Japanese address)', '標準（regex + GiNZA NER + 日本語住所）'),
      }[r.mode] || String(r.mode);
      view.textContent = label;
    }
  } catch (e) { /* ignore */ }
}

function _showPiiBanner(spans) {
  // #C: モデル行 inline 表示 (display:inline-flex でモデル行に同居)
  const banner = document.getElementById('chat-guardrail-banner');
  if (!banner) return;
  const counts = {};
  spans.forEach(p => { counts[p.type] = (counts[p.type] || 0) + 1; });
  const detail = Object.entries(counts).map(([t, c]) => `${escapeHtml(t)}:${c}`).join(' / ');
  banner.innerHTML = lj(`⚠️ PII was masked before sending to the LLM (${detail})`, `⚠️ LLMへの送信時にPIIをマスクしました（${detail}）`);
  banner.style.display = 'inline-flex';
  setTimeout(() => { banner.style.display = 'none'; }, 8000);
}
