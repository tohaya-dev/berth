// auth.js - Cynovela v13

async function doLoginByPassword() {
  const username = $('login-username').value.trim();
  const password = $('login-password').value;
  const errEl = $('login-error');
  errEl.style.display = 'none';
  if (!username || !password) {
    errEl.textContent = (CYNOVELA_LANG === 'ja')
      ? 'ユーザー名とパスワードを入力してください'
      : 'Please enter username and password';
    errEl.style.display = '';
    return;
  }
  try {
    const result = await API.post('/api/auth/login', { username, password });
    await _enterApp(result);
  } catch (e) {
    errEl.textContent = e.message || ((CYNOVELA_LANG === 'ja') ? 'ログイン失敗' : 'Login failed');
    errEl.style.display = '';
  }
}
