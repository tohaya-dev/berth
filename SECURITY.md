## Current PostgreSQL/Kubernetes deployment

The Linux adapter uses PostgreSQL with pgvector, Redis and Kubernetes Secrets. The SQLite descriptions below apply to historical sandbox/demo paths, not this deployment. Keep PostgreSQL PVCs, source files, Secret manifests and backups private; use encrypted storage and a secret manager for production. The SQL dump is not encrypted by pg_dump. Its paired application encryption key must be retained and verified on restore. API and worker pods do not mount a Kubernetes service-account token. Authentication, workspace/collection RBAC, masking and MCP write gates remain active.

See [RC dependency audit](THIRD_PARTY_NOTICES.md) for unresolved image findings. The local RC endpoint is loopback only. Do not publish a vulnerable binary image as cleared merely because functional tests pass. Never include credentials or private documents in a public issue.

# Security Policy

## Known Limitations (Beta)

The following limitations are known and planned for future releases:

- **API Key Storage**: LLM API keys are stored in plain text in the configuration
  file (`cynovela.yaml`). Do not expose this file to untrusted parties.
  Fernet encryption is planned for a future release.

- **Session Tokens**: Session tokens are stored in server memory only.
  All active sessions are invalidated when the server restarts.
  Database-backed session persistence is planned for a future release.

- **PII in Audit Logs**: Prior to v11-beta, chat query text was stored verbatim
  in audit log detail fields. This has been addressed in v11-beta with
  PII masking applied before storage.

## Threat Model and Defenses (sec4 v4.1 / 2026-05-24)

### Threat Model

Cynovela is positioned as a local-first governance concept tool. The threat model assumes:

- **Local-first deployment**: The application runs on the operator's machine. OS-level disk encryption (e.g. FileVault on macOS) is assumed to be in place.
- **Trust boundary**: Other local users on the same machine are inside the trust boundary. Cynovela does not defend against local privilege escalation.
- **Out of scope**: Defense against a skilled attacker with shell access on the same host. Defense against malicious browser extensions.

### What we defend by default

1. **Password hashing**: User passwords are hashed with PBKDF2-HMAC-SHA256 (100,000 iterations, per-user salt). Plaintext passwords are never stored. Implementation: `db.py:689-692 hash_password()`.
2. **No password-less entry**: As of sec4 v4.1, there is no one-click / user-list quick-login. All logins require username + password. The legacy `user_id`-only login path on `/api/auth/login` has been removed and now returns 401. The `/api/auth/users` endpoint always requires admin authentication.
3. **Encryption-at-rest for secrets**: Long-lived secrets stored in the `settings` table (e.g. external service adapter credentials) are encrypted with Fernet on write and decrypted on read. Plaintext fallback is preserved for backward compatibility (`enc:` prefix marker).
4. **Encryption key**: `CYNOVELA_SECRET_KEY` is read from the env first, then from a persisted file `~/.cynovela-alphaga/secret.key` (chmod 600). New keys are generated and persisted on first start.
5. **File permissions**: Sandbox SQLite databases and the encryption key file are chmod 600 (owner-only read/write).
6. **Audit logging**: Authentication failures, admin changes, and chat queries are recorded in `audit_logs` with PII masking applied to payload fields.

### Known weaknesses and operational guidance

| Area | Weakness | Operational guidance |
|---|---|---|
| Default admin password | `cynovela/cynovela` is a weak default initial credential. Acceptable for a local demo, dangerous on a network. | **Change immediately for non-demo use**. Set `CYNOVELA_ADMIN_USERNAME` and `CYNOVELA_ADMIN_INITIAL_PASSWORD` environment variables before the first start, or rotate the admin password via the UI after first login. |
| Encryption key colocation | The encryption key `~/.cynovela-alphaga/secret.key` sits next to the SQLite database. An attacker who can read the DB can read the key. This is a deliberate trade-off for a local demo. | **For production**: set `CYNOVELA_SECRET_KEY` in the OS keychain or a secrets manager and never write it to disk. |
| Full-DB encryption | The SQLite file itself is unencrypted on disk. OS-level disk encryption is assumed. | **For production / regulated workloads**: enable full-DB encryption (e.g. SQLCipher migration) gated by a future toggle (`CYNOVELA_DB_ENCRYPTION=on`, see "Future toggles" below). |
| Login rate limit | No login-specific rate limit. SlowAPI applies a global 200 req/min/IP cap, which slows but does not prevent brute force. | **For internet-facing deployments**: front the service with a reverse proxy that enforces a tighter rate per IP, fail2ban, or CAPTCHA. |
| Sensitive content (chunks/audit detail) at rest | Document body and `audit_logs.detail` are not encrypted at rest. Encryption is deferred because the read paths span many call sites. | **Rely on OS disk encryption** until full-DB encryption is enabled. Avoid storing highly sensitive corpora without disk encryption. |
| Backups | When you copy the sandbox DB to backups (e.g. `~/.cynovela-alphaga/demo/v13-demo.db.bak-*`), plaintext rows go with it. | **Do not copy backups to untrusted media**. If you adopt full-DB encryption, copy the encrypted file instead of dumping. Old plaintext backups should be cleaned up after migrating. |

### Future toggles (design memo, not implemented)

- `CYNOVELA_DB_ENCRYPTION=on`: enable full-database encryption via SQLCipher (or equivalent). Requires offline migration of the existing plaintext DB.
- OS keychain integration for `CYNOVELA_SECRET_KEY`: replace the file-based fallback when `CYNOVELA_USE_KEYCHAIN=on` is set.
- Login rate limiting via SlowAPI: add a `@limiter.limit("10/minute")` decorator to `POST /api/auth/login` with a short auto-unlock window. Not enabled by default because we do not want lockouts on a local demo.

### Future hardening of password hashing (record-only)

PBKDF2-SHA256 at 100,000 iterations is acceptable today but below the current OWASP recommendation (≥ 600,000 for PBKDF2-SHA256, or migration to Argon2id). Migration is deferred to avoid invalidating existing password hashes. The intended approach is to upgrade on the next successful login per user (silent upgrade), not to force a global re-hash.

### Permissions reference

After installation / first start, the sandbox layout should look like:

```
~/.cynovela-alphaga/
├── secret.key                 -rw-------  (chmod 600)
└── demo/
    ├── v13-demo.db            -rw-------  (chmod 600)
    ├── v13-demo.db.bak-*      -rw-------  (chmod 600 — keep with the DB)
    └── chroma/                drwxr-xr-x  (Chroma vector store)
```

If your DB file shows `-rw-r--r--`, run `chmod 600 ~/.cynovela-alphaga/demo/v13-demo.db` to restrict access.

## Reporting a Vulnerability

Please open a GitHub Issue with the label `security`.
