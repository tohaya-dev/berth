"""Cynovela — DocumentParser 抽象層 (BLOCK F-1)。

ファイル形式に応じてテキスト + 画像を抽出する。
fullモードでは MultimodalPDFParser / ImageParser を有効化し、
text/lite/minimal モードではテキストのみのパーサに切り替える。

依存:
  - pdfminer.six: PDFテキスト抽出 (text/lite/minimal/full 全モードで使用)
  - PyMuPDF (fitz): PDFラスタライズ → 画像 (fullモード専用)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedDocument:
    text: str
    images: list[bytes] = field(default_factory=list)  # 画像バイト列のリスト
    page_count: int = 1
    language: str = "unknown"
    extractor_version: str = "unknown"


class DocumentParser(ABC):
    @abstractmethod
    def can_parse(self, file_name: str, mime_type: str) -> bool: ...

    @abstractmethod
    def parse(self, data: bytes, file_name: str) -> ParsedDocument: ...


class PlainTextParser(DocumentParser):
    """テキスト系 (UTF-8/SJIS/EUC-JP/Latin-1) を試行する。全モード対応。"""

    def can_parse(self, file_name: str, mime_type: str) -> bool:
        if mime_type and mime_type.startswith("text/"):
            return True
        return file_name.lower().endswith((".txt", ".md", ".csv", ".log"))

    def parse(self, data: bytes, file_name: str) -> ParsedDocument:
        for enc in ("utf-8", "shift_jis", "euc-jp", "latin-1"):
            try:
                text = data.decode(enc)
                return ParsedDocument(text=text, extractor_version="plaintext_v1")
            except UnicodeDecodeError:
                continue
        return ParsedDocument(
            text=data.decode("utf-8", errors="replace"),
            extractor_version="plaintext_v1",
        )


class PDFParser(DocumentParser):
    """text/lite/minimal モード用: pdfminer によるテキスト抽出のみ。"""

    def can_parse(self, file_name: str, mime_type: str) -> bool:
        return mime_type == "application/pdf" or file_name.lower().endswith(".pdf")

    def parse(self, data: bytes, file_name: str) -> ParsedDocument:
        try:
            import io
            from pdfminer.high_level import extract_text as _extract_text

            text = _extract_text(io.BytesIO(data)) or ""
            return ParsedDocument(text=text, extractor_version="pdfminer_v1")
        except ImportError:
            return ParsedDocument(
                text="[PDF: pdfminer.six 未インストール]",
                extractor_version="none",
            )
        except Exception as e:
            return ParsedDocument(
                text=f"[PDF parse error: {e}]",
                extractor_version="pdfminer_v1_err",
            )


class MultimodalPDFParser(DocumentParser):
    """fullモード用: テキスト + 各ページの画像 (PNG) を抽出する。"""

    def can_parse(self, file_name: str, mime_type: str) -> bool:
        return mime_type == "application/pdf" or file_name.lower().endswith(".pdf")

    def parse(self, data: bytes, file_name: str) -> ParsedDocument:
        try:
            import io
            from pdfminer.high_level import extract_text as _extract_text

            text = _extract_text(io.BytesIO(data)) or ""
        except Exception as e:
            text = f"[PDF text parse error: {e}]"
        images: list[bytes] = []
        page_count = 0
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=data, filetype="pdf")
            try:
                for page in doc:
                    pix = page.get_pixmap(dpi=120)
                    images.append(pix.tobytes("png"))
                page_count = len(doc)
            finally:
                doc.close()
        except ImportError:
            # PyMuPDF 無し → テキストのみ
            return ParsedDocument(
                text=text,
                images=[],
                page_count=1,
                extractor_version="pdfminer_v1_no_pymupdf",
            )
        except Exception as e:
            return ParsedDocument(
                text=text + f"\n[image extract error: {e}]",
                images=[],
                page_count=1,
                extractor_version="multimodal_pdf_v1_err",
            )
        return ParsedDocument(
            text=text,
            images=images,
            page_count=page_count or 1,
            extractor_version="multimodal_pdf_v1",
        )


class ImageParser(DocumentParser):
    """fullモード専用: 画像ファイルを bytes として image に格納する (テキストは空)。"""

    SUPPORTED = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    def can_parse(self, file_name: str, mime_type: str) -> bool:
        if mime_type and mime_type.startswith("image/"):
            return True
        return any(file_name.lower().endswith(ext) for ext in self.SUPPORTED)

    def parse(self, data: bytes, file_name: str) -> ParsedDocument:
        return ParsedDocument(
            text="",
            images=[data],
            page_count=1,
            extractor_version="image_v1",
        )


class DocumentParserRegistry:
    """AppConfig (mode) に応じてパーサを切り替える。"""

    def __init__(self, app_cfg=None):
        self._parsers: list[DocumentParser] = [PlainTextParser()]
        mode = getattr(app_cfg, "mode", "text") if app_cfg else "text"
        if mode == "full":
            self._parsers.extend([MultimodalPDFParser(), ImageParser()])
        else:
            self._parsers.append(PDFParser())

    def get_parser(self, file_name: str, mime_type: str = "") -> DocumentParser:
        for p in self._parsers:
            if p.can_parse(file_name, mime_type):
                return p
        return self._parsers[0]  # PlainTextParser フォールバック
