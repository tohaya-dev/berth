"""
Cynovela 差分同期エージェント (PHASE 12)

- 指定フォルダを監視してファイル変更を検知 (watchdog)
- SHA256 で変更を確認 → Cynovela サーバーに差分のみ通知
- Windows 8GB RAM 対応 (軽量設計・mlx/torch 不要)
- Tailscale 経由でリモートの Cynovela サーバーに接続可能

使い方:
    python cynovela_agent.py --watch /path/to/docs \
        --server http://100.x.x.x:8765 \
        --workspace-id YOUR_WS_ID
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

AGENT_DB = os.path.expanduser("~/.cynovela-agent.db")
SUPPORTED = {
    ".pdf",
    ".txt",
    ".md",
    ".docx",
    ".html",
    ".htm",
    ".pptx",
    ".xlsx",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
}


def compute_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(AGENT_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_hashes (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            synced_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def notify_server(url: str, ws_id: str, path: str) -> bool:
    """新規/変更ファイルをサーバーに登録 + scan を起動。"""
    try:
        r = requests.post(
            f"{url}/api/sources",
            json={"name": Path(path).name, "path": path},
            timeout=30,
        )
        if r.status_code in (200, 201):
            src_id = r.json().get("id", "")
            if src_id:
                requests.post(f"{url}/api/sources/{src_id}/scan", timeout=10)
            print(f"✅ Synced: {path}")
            return True
        print(f"⚠️ {r.status_code}: {path}")
        return False
    except Exception as e:
        print(f"❌ {e}")
        return False


class Handler(FileSystemEventHandler):
    def __init__(self, db, server_url, ws_id):
        self.db = db
        self.url = server_url
        self.ws_id = ws_id

    def on_modified(self, event):
        if not event.is_directory:
            self._process(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._process(event.src_path)

    def _process(self, path):
        if Path(path).suffix.lower() not in SUPPORTED:
            return
        try:
            new_hash = compute_sha256(path)
            row = self.db.execute("SELECT sha256 FROM file_hashes WHERE path = ?", (path,)).fetchone()
            if row and row[0] == new_hash:
                return  # 変更なし
            if notify_server(self.url, self.ws_id, path):
                self.db.execute(
                    "INSERT OR REPLACE INTO file_hashes " "VALUES(?, ?, datetime('now'))",
                    (path, new_hash),
                )
                self.db.commit()
        except Exception as e:
            print(f"⚠️ {e}")


def main():
    parser = argparse.ArgumentParser(description="Cynovela Sync Agent")
    parser.add_argument("--watch", required=True, help="Directory to watch")
    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8765",
        help="Cynovela server URL (Tailscale OK)",
    )
    parser.add_argument("--workspace-id", default="", help="Target workspace ID")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()

    if not Path(args.watch).exists():
        print(f"❌ {args.watch} not found")
        sys.exit(1)

    print(f"🔍 Watching: {args.watch}")
    print(f"🌐 Server:   {args.server}")
    if args.workspace_id:
        print(f"📁 Workspace: {args.workspace_id}")

    db = init_db()
    obs = Observer()
    obs.schedule(
        Handler(db, args.server, args.workspace_id),
        args.watch,
        recursive=True,
    )
    obs.start()
    try:
        while True:
            time.sleep(args.interval)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
    db.close()
    print("👋 Stopped")


if __name__ == "__main__":
    main()
