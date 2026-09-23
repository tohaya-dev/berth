// api.js - Cynovela v13
// fix065-066 段 B 方針: API ラッパーは state.js:349 の const API オブジェクトに集約。
// 401 セッション切れ時のログイン画面遷移は state.js:API._handleSessionExpired() が担当。
// 個別画面コードからの裸 fetch は新規追加禁止 (API.get/post/put/patch/del を使用)。
