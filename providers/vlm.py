"""Cynovela — VLM (Vision Language Model) Provider 抽象 (BLOCK F-2)。

fullモード専用。Apple Silicon: mlx-vlm / Windows GPU: transformers+CUDA。
他モードでは create_vlm_provider() が None を返す。
"""

from __future__ import annotations

import platform
import tempfile
import os
from abc import ABC, abstractmethod
from typing import Optional


class RemoteVLMEgressBlocked(RuntimeError):
    """v3.5.0 Stage4: raised when an image would be POSTed to a non-local VLM endpoint
    without explicit opt-in. Callers fall back to local processing (filename_only)."""


def _is_local_vlm_endpoint(endpoint: str) -> bool:
    """True iff the endpoint host stays on this machine / private LAN (no internet egress).
    localhost / loopback / container host-gateway / RFC1918 private ranges = local.
    Public hostnames or public IPs = remote."""
    from urllib.parse import urlparse

    try:
        host = (urlparse(endpoint if "://" in (endpoint or "") else "http://" + (endpoint or "")).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in (
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "host.containers.internal",
        "host.docker.internal",
        "gateway.docker.internal",
    ):
        return True
    import ipaddress

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        # a DNS hostname / public domain — treat as remote (internet egress)
        return False


class VLMProvider(ABC):
    @abstractmethod
    def describe_image(self, image_bytes: bytes, prompt: str = "") -> str: ...

    @abstractmethod
    def health_check(self) -> dict: ...


class MLXVLMProvider(VLMProvider):
    """Apple Silicon 専用 (mlx-vlm)。最初の describe_image でモデルをロードする。"""

    def __init__(self, model_name: str = "mlx-community/llava-1.5-7b-4bit"):
        try:
            from mlx_vlm import load, generate  # noqa: F401
            from mlx_vlm.prompt_utils import apply_chat_template  # noqa: F401
            from mlx_vlm.utils import load_config  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False
        self._model_name = model_name
        self._model = None
        self._processor = None
        self._config = None

    def _ensure_loaded(self):
        if not self._available:
            raise ImportError("mlx-vlm が未インストールです (pip install mlx-vlm)")
        if self._model is None:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            self._model, self._processor = load(self._model_name)
            self._config = load_config(self._model_name)

    def describe_image(self, image_bytes: bytes, prompt: str = "") -> str:
        self._ensure_loaded()
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            formatted = apply_chat_template(
                self._processor,
                self._config,
                prompt or "この画像の内容を詳しく説明してください。",
                num_images=1,
            )
            output = generate(
                self._model,
                self._processor,
                tmp_path,
                formatted,
                max_tokens=500,
            )
            return str(output)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def health_check(self) -> dict:
        return {
            "status": "ok" if self._available else "not_available",
            "type": "mlx_vlm",
            "model": self._model_name,
            "platform": "apple_silicon",
            "loaded": self._model is not None,
        }


class CUDAVLMProvider(VLMProvider):
    """Windows / Linux GPU 専用 (transformers + CUDA)。"""

    def __init__(self, model_name: str = "llava-hf/llava-1.5-7b-hf"):
        try:
            import torch  # noqa: F401
            from transformers import AutoProcessor  # noqa: F401

            self._available = True
            self._cuda_available = bool(torch.cuda.is_available())
        except ImportError:
            self._available = False
            self._cuda_available = False
        self._model_name = model_name
        self._model = None
        self._processor = None

    def _ensure_loaded(self):
        if not (self._available and self._cuda_available):
            raise RuntimeError("transformers / torch (CUDA) が利用できません")
        if self._model is None:
            import torch
            from transformers import LlavaForConditionalGeneration, AutoProcessor

            self._model = LlavaForConditionalGeneration.from_pretrained(
                self._model_name,
                torch_dtype=torch.float16,
                device_map="auto",
            )
            self._processor = AutoProcessor.from_pretrained(self._model_name)

    def describe_image(self, image_bytes: bytes, prompt: str = "") -> str:
        self._ensure_loaded()
        from PIL import Image
        import io, torch

        image = Image.open(io.BytesIO(image_bytes))
        inputs = self._processor(
            text=prompt or "この画像の内容を詳しく説明してください。",
            images=image,
            return_tensors="pt",
        ).to("cuda")
        with torch.no_grad():
            output = self._model.generate(**inputs, max_new_tokens=500)
        return self._processor.decode(output[0], skip_special_tokens=True)

    def health_check(self) -> dict:
        return {
            "status": "ok" if (self._available and self._cuda_available) else "not_available",
            "type": "cuda_vlm",
            "model": self._model_name,
            "cuda_available": self._cuda_available,
        }


def _endpoint_is_local(endpoint: str) -> bool:
    """V3.5.0 Stage B: 画像エグレス封鎖の locality 判定。

    loopback (localhost / 127.0.0.0/8 / ::1 / 0.0.0.0) のみ「ローカル」とみなす。
    コンテナで host gateway 等の非ループバック宛にローカル LLM を置く場合は、
    意図的な opt-in (image.allow_remote_vlm=true) を必須とする(無警告 egress を防ぐ)。
    注: テキストLLM送出の局所性 (_is_local_vlm_endpoint: RFC1918=local) とは意図的に別基準。
    画像 Base64 は情報量が大きく、私設 IP 宛でも既定では出さない。
    """
    from urllib.parse import urlparse

    try:
        _u = endpoint if "://" in (endpoint or "") else "http://" + (endpoint or "")
        host = (urlparse(_u).hostname or "").lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.startswith("127.")


class LMStudioVisionProvider(VLMProvider):
    """LM Studio (OpenAI 互換) Vision API プロバイダー。

    Gemma 3/4 Vision など vision 対応モデルを LM Studio 経由で利用する。
    既存 LM Studio 接続を再利用するため Apple Silicon / Windows / Linux いずれでも動作する。
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:1234/v1",
        model_name: str = "",
        timeout: int = 180,
        allow_remote: bool = False,
    ):
        try:
            import requests  # noqa: F401

            self._available = True
        except ImportError:
            self._available = False
        ep = endpoint.rstrip("/")
        if not ep.endswith("/v1"):
            ep = ep + "/v1"
        self._endpoint = ep
        self._model_name = (model_name or "").strip()
        self._timeout = timeout
        # V3.5.0 Stage B (Berth 適応): 画像エグレスは loopback 限定を既定とする。
        # テキストLLMの局所性 (_is_local_vlm_endpoint: RFC1918=local) より厳しく、
        # 非ループバック宛 (host gateway / 私設 IP 含む) は opt-in 必須 (無警告 egress を防ぐ)。
        self._allow_remote = bool(allow_remote)
        self._endpoint_local = _endpoint_is_local(ep)

    def describe_image(self, image_bytes: bytes, prompt: str = "", allow_remote: bool = False) -> str:
        if not self._available:
            raise ImportError("requests が未インストールです")
        # V3.5.0 Stage B: 画像エグレス封鎖。非ループバック宛への Base64 画像 POST は
        # 既定で封鎖し、image.allow_remote_vlm=true の明示 opt-in 時のみ許可する。
        # ここで raise すると呼出側(rag.py _extract_image_text)の try/except が
        # filename_only へ fallback するため、封鎖時も無回帰(挙動は filename_only)。
        # (RemoteVLMEgressBlocked は RuntimeError 派生・呼出側 fallback 互換)
        if not (allow_remote or self._allow_remote or self._endpoint_local):
            raise RemoteVLMEgressBlocked(
                "画像のリモート送信は既定で封鎖されています(V3.5.0 Stage B): "
                f"endpoint={self._endpoint} は loopback ではありません。"
                "リモート VLM へ画像を送るには image.allow_remote_vlm=true を明示設定してください(opt-in)。"
            )
        import base64
        import requests

        mime = "image/png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
            mime = "image/gif"
        elif len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            mime = "image/webp"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        body: dict = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or "この画像の内容を詳しく説明してください。"},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "temperature": 0.2,
        }
        if self._model_name:
            body["model"] = self._model_name
        resp = requests.post(f"{self._endpoint}/chat/completions", json=body, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return ((choices[0].get("message") or {}).get("content") or "").strip()

    def health_check(self) -> dict:
        return {
            "status": "ok" if self._available else "not_available",
            "type": "lm_studio_vision",
            "endpoint": self._endpoint,
            "model": self._model_name or "(auto)",
        }


def create_vlm_provider(app_cfg) -> Optional[VLMProvider]:
    """fullモード以外では None を返す。
    Apple Silicon は MLXVLMProvider、それ以外は CUDAVLMProvider を試す。
    どちらも利用不可なら None。"""
    if not getattr(app_cfg, "multimodal_enabled", False):
        return None
    if platform.system() == "Darwin":
        try:
            p = MLXVLMProvider()
            return p if p.health_check().get("status") == "ok" else None
        except Exception:
            return None
    try:
        p = CUDAVLMProvider()
        return p if p.health_check().get("status") == "ok" else None
    except Exception:
        return None
