"""Cynovela — DataSource 抽象層 (BLOCK D)。

既存の server.py / _do_scan のファイルシステムスキャンはそのまま維持し、
本Providerはデータソース抽象化の足場として並行する形で導入する。

- DataSource: discover / read / health_check の3メソッドを持つ async 抽象
- DiscoveredFile: 発見ファイルのメタデータ + ACL情報のコンテナ
- FileSystemDataSource: 既存FSスキャンと同等の挙動を提供
"""

from __future__ import annotations

import os
import hashlib
import platform
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class DiscoveredFile:
    file_id: str
    source_path: str
    file_name: str
    file_size: int
    modified_at: str  # ISO8601
    content_hash: str = ""  # read() 後に SHA256 を入れる
    acl_info: dict = field(default_factory=dict)
    acl_source: str = "filesystem"


class DataSource(ABC):
    @abstractmethod
    async def discover(self, path: str) -> list[DiscoveredFile]: ...

    @abstractmethod
    async def read(self, file: DiscoveredFile) -> bytes: ...

    @abstractmethod
    async def health_check(self) -> dict: ...


# ────────────────────────────────────────────
# FileSystem DataSource
# ────────────────────────────────────────────


class FileSystemDataSource(DataSource):
    """ローカルFSラッパ。既存 _do_scan は触らず、新規コードがこちらを使う。"""

    async def discover(self, path: str) -> list[DiscoveredFile]:
        out: list[DiscoveredFile] = []
        if not os.path.exists(path):
            return out
        if os.path.isfile(path):
            out.append(self._from_file(path))
            return out
        for root, _dirs, filenames in os.walk(path):
            for fname in filenames:
                fpath = os.path.join(root, fname)
                try:
                    out.append(self._from_file(fpath))
                except FileNotFoundError:
                    continue
        return out

    def _from_file(self, fpath: str) -> DiscoveredFile:
        stat = os.stat(fpath)
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        fid = hashlib.sha256(fpath.encode("utf-8")).hexdigest()[:16]
        return DiscoveredFile(
            file_id=fid,
            source_path=fpath,
            file_name=os.path.basename(fpath),
            file_size=stat.st_size,
            modified_at=mtime,
            acl_source="filesystem",
        )

    async def read(self, file: DiscoveredFile) -> bytes:
        with open(file.source_path, "rb") as f:
            data = f.read()
        file.content_hash = hashlib.sha256(data).hexdigest()
        return data

    async def health_check(self) -> dict:
        return {"status": "ok", "type": "filesystem"}

    def open_in_finder(self, path: str) -> bool:
        """OSに応じてファイルマネージャーを起動する。
        macOS: Finder (open -R) / Windows: Explorer (/select,) / Linux: xdg-open
        （Linuxは Nautilus/Dolphin/Thunar 等、xdg-open が解決する任意のFM）。
        対応外OSやエラー時は False を返す。
        """
        try:
            import subprocess

            system = platform.system()
            if system == "Darwin":
                subprocess.run(["open", "-R", path], check=False)
            elif system == "Windows":
                subprocess.run(["explorer", "/select,", path], check=False)
            elif system == "Linux":
                # Linux の xdg-open はファイル選択をサポートしないため親ディレクトリを開く
                target = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.run(["xdg-open", target], check=False)
            else:
                return False
            return True
        except Exception:
            return False


