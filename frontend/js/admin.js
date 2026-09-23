// admin.js - Cynovela v13

function roleLevel(role) { return { admin:2, viewer:1 }[role] || 0; }


async function renderAdminUsers() {
  const container = $('admin-users-list');
  if (!container) return;
  try {
    const users = await API.get('/api/admin/users');
    // UX-2: 役割をバッジ表示 + display_name のインライン編集
    // 役割はサーバの正 (core/constants.py VALID_ROLES) と同じ admin / viewer の 2 つだけ。
    const _ROLE_BADGE = {
      'admin':  { en: 'Administrator', ja: '管理者', cls: 'role-admin' },
      'viewer': { en: 'Viewer',        ja: '閲覧者', cls: 'role-viewer' },
    };
    const _hdrUser = (CYNOVELA_LANG === 'ja') ? 'ユーザー名'  : 'Username';
    const _hdrDisp = (CYNOVELA_LANG === 'ja') ? '表示名'      : 'Display name';
    const _hdrRole = (CYNOVELA_LANG === 'ja') ? 'ロール'      : 'Role';
    const _hdrAct  = (CYNOVELA_LANG === 'ja') ? '有効'        : 'Active';
    const _hdrCre  = (CYNOVELA_LANG === 'ja') ? '作成日'      : 'Created';
    const _hdrOps  = (CYNOVELA_LANG === 'ja') ? '操作'        : 'Actions';
    container.innerHTML = `<table><thead><tr>
        <th>${_hdrUser}</th><th>${_hdrDisp}</th><th>${_hdrRole}</th>
        <th>${_hdrAct}</th><th>${_hdrCre}</th><th>${_hdrOps}</th>
      </tr></thead><tbody>` +
      users.map(u => {
        const isSelf = State.user && State.user.id === u.id;
        const dn = u.display_name || u.name || '';
        const rb = _ROLE_BADGE[u.role] || { en: u.role, ja: u.role, cls: '' };
        return `<tr>
          <td class="primary">${escapeHtml(u.username || u.id)}</td>
          <td>
            <input type="text" class="form-input" style="font-size:17px;padding:3px 8px;width:160px;"
                   placeholder="Display name (optional)"
                   value="${escapeHtml(dn).replace(/"/g, '&quot;')}"
                   onblur="updateDisplayName('${u.id}', this.value)">
          </td>
          <td>
            <span class="user-role ${rb.cls}">
              <span class="en">${escapeHtml(rb.en)}</span><span class="ja">${escapeHtml(rb.ja)}</span>
            </span>
          </td>
          <td>${u.is_active ? '✅' : '❌'}</td>
          <td>${(u.created_at || '').slice(0, 10)}</td>
          <td class="btn-row">
            <button class="btn btn-sm btn-ghost" onclick="showEditUserModal('${u.id}','${(dn).replace(/'/g, "\\'")}','${u.role}',${u.is_active ? 1 : 0})">✏️ <span class="en">Edit</span><span class="ja">${bi('Edit', '編集')}</span></button>
            <button class="btn btn-sm btn-ghost" onclick="showResetPasswordModal('${u.id}','${(u.username || u.id).replace(/'/g, "\\'")}')">🔑 <span class="en">Password</span><span class="ja">パス変更</span></button>
            ${isSelf ? '' : `<button class="btn btn-sm btn-danger" onclick="deactivateUser('${u.id}','${(u.username || u.id).replace(/'/g, "\\'")}')">🚫 <span class="en">Disable</span><span class="ja">無効化</span></button>`}
          </td>
        </tr>`;
      }).join('') + '</tbody></table>';
  } catch (e) {
    const msg = lj('Failed to load users', 'ユーザー一覧取得失敗');
    container.innerHTML = `<div style="color:var(--error)">${msg}: ${escapeHtml(e.message || '')}</div>`;
  }
}

function showEditUserModal(uid, displayName, role, isActive) {
  const roleAlias = (role === 'admin') ? 'admin' : 'viewer';
  openFormModal(lj('Edit user', 'ユーザー編集'), `
    <div class="form-group"><label class="form-label">${bi('Display name', '表示名')}</label>
      <input id="edit-user-display" class="form-input" value="${displayName}"></div>
    <div class="form-group"><label class="form-label">${bi('Role', 'ロール')}</label>
      <select id="edit-user-role" class="form-select">
        <option value="viewer" ${roleAlias==='viewer'?'selected':''}>viewer</option>
        <option value="admin" ${roleAlias==='admin'?'selected':''}>admin</option>
      </select></div>
    <div class="form-group"><label class="check-item">
      <input id="edit-user-active" type="checkbox" ${isActive?'checked':''}> ${bi('Active', '有効')}
    </label></div>
  `, lj('Save', '保存'), () => updateUser(uid));
}

async function updateUser(uid) {
  const display_name = $('edit-user-display').value.trim();
  const role = $('edit-user-role').value;
  const is_active = $('edit-user-active').checked;
  try {
    await API.put ? null : null;
    // PATCHメソッドはAPI helperにないので直接fetch
    const res = await fetch(`${API.base}/api/admin/users/${uid}`, {
      method: 'PATCH', headers: API.headers(),
      body: JSON.stringify({ display_name, role, is_active }),
    });
    if (!res.ok) { const e = await res.json().catch(()=>({})); throw new Error(e.detail||`HTTP ${res.status}`); }
    closeFormModal();
    showToast(lj('User info updated','ユーザー情報を更新しました'), 'success');
    renderAdminUsers();
  } catch (e) { showToast(lj(`Update failed: ${e.message}`,`更新失敗: ${e.message}`), 'error'); }
}

async function createBackupNow() {
  const label = $('backup-label').value.trim();
  try {
    const meta = await API.post('/api/admin/backup', { label });
    $('backup-label').value = '';
    showToast(lj(`Backup created: ${meta.name}`,`バックアップ作成: ${meta.name}`), 'success');
    renderBackupList();
  } catch (e) { showToast(lj(`Backup failed: ${e.message}`,`バックアップ失敗: ${e.message}`), 'error'); }
}

function restoreBackup(name) {
  confirmAction(lj('Confirm restore', 'リストア確認'),
    lj(`Restore from “${name}”.\nThe current state will be automatically backed up before being overwritten. Continue?`, `「${name}」から復元します。\n現在の状態は自動でバックアップしてから上書きします。続行しますか？`),
    '↩', async () => {
      try {
        const r = await API.post(`/api/admin/backups/${encodeURIComponent(name)}/restore`, {});
        showToast(lj(`Restore complete (current state saved to ${r.auto_backup_before_restore})`,`復元完了 (現状を ${r.auto_backup_before_restore} に保存)`), 'success');
        await refreshAllData();
        renderBackupList();
      } catch (e) { showToast(lj(`Restore failed: ${e.message}`,`リストア失敗: ${e.message}`), 'error'); }
    });
}

async function loadProcessingLogsSection() {
  const host = document.getElementById('processing-logs-host');
  if (!host) return;
  try {
    const data = await API.get('/api/admin/processing-logs?limit=50');
    if (!data || !data.length) {
      host.innerHTML = `<div style="font-size:16px;color:#94a3b8;">${bi('No processing logs yet.', '処理ログはまだありません。')}</div>`;
      return;
    }
    const rows = data.map(r => `
      <tr>
        <td style="font-size:16px;color:#94a3b8;white-space:nowrap;">${escapeHtml(r.timestamp || '')}</td>
        <td><span class="badge badge-${r.level === 'error' ? 'danger' : (r.level === 'warning' ? 'warn' : 'info')}">${escapeHtml(r.log_type || '')}</span></td>
        <td style="font-size:16px;">${escapeHtml((r.message || '').slice(0, 200))}</td>
      </tr>`).join('');
    host.innerHTML = `
      <h4 style="margin:0 0 6px 0;font-size:17px;color:#475569;">📋 ${lj(`Processing logs (latest ${data.length})`, `処理ログ (直近 ${data.length} 件)`)}</h4>
      <div style="max-height:280px;overflow:auto;border:1px solid #e2e8f0;border-radius:6px;">
        <table style="width:100%;border-collapse:collapse;font-size:16px;">
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } catch (e) {
    host.innerHTML = `<div style="color:#ef4444">${lj('Failed to load processing-logs', 'processing-logs 取得失敗')}: ${escapeHtml(e.message)}</div>`;
  }
}
