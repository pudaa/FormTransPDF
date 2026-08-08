"""
翻译结果记录 — 将「源文件指纹 → 翻译结果」的映射持久化到 output 目录。

每个翻译结果组（{base}.zh.dual.pdf / {base}.zh.mono.pdf / {base}.zh.glossary.csv）
旁边写入一个 {base}.zh.srcinfo.json sidecar：

    {
        "source_hash":       "<内容指纹 sha256>",
        "source_bytes_hash": "<字节指纹 sha256>",
        "source_name":       "原始文件名.pdf",
        "lang_in":           "en",
        "lang_out":          "zh",
        "translator":        "openai",
        "output_mode":       "dual",
        "timestamp":         1754...
    }

指纹设计（解决「同一文件重复提交」的算力浪费）：
- 内容指纹基于 PDF 每页文本 + 页面尺寸（非文件名）：
  * 用户重命名文件 → 指纹不变 → 可复用已有结果
  * 用户重新保存（仅元数据/压缩变化）→ 指纹不变 → 可复用
  * 内容/分页变化 → 指纹不同 → 正常重新翻译
- 字节指纹为文件字节 SHA-256（精确）：扫描件没有文本层时内容指纹失真，
  字节指纹兜底保证「重命名同一文件」仍能命中。
- 查找时内容指纹或字节指纹任一命中即可复用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SIDECAR_SUFFIX = ".zh.srcinfo.json"


def compute_source_fingerprint(path: str | Path) -> tuple[str, str]:
    """计算源 PDF 的 (内容指纹, 字节指纹)，均为 SHA-256 hex。

    内容指纹：拼接页面数、每页尺寸与文本后哈希 —— 对重命名/重保存鲁棒；
    字节指纹：对文件原始字节哈希 —— 精确匹配（扫描件兜底）。
    """
    path = Path(path)

    # ── 字节指纹（先算，读取成本低）──
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    bytes_hash = h.hexdigest()

    # ── 内容指纹（PyMuPDF 已在依赖中，pdf_text_extractor 亦使用）──
    import fitz

    doc = fitz.open(str(path))
    try:
        parts = [f"pages:{doc.page_count}"]
        for i in range(doc.page_count):
            page = doc[i]
            rect = page.rect
            parts.append(f"{i}:{rect.width:.2f}x{rect.height:.2f}")
            parts.append(page.get_text("text"))
        content_hash = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    finally:
        doc.close()

    return content_hash, bytes_hash


@dataclass
class SourceInfo:
    """源文件信息（与一次翻译结果绑定）。"""

    source_hash: str            # 内容指纹（主键）
    source_bytes_hash: str = ""  # 字节指纹（副键，扫描件兜底）
    source_name: str = ""
    source_path: str = ""       # 原文件绝对路径（历史记录打开原文用；文件可能已移动）
    lang_in: str = "en"
    lang_out: str = "zh"
    translator: str = ""
    output_mode: str = "dual"
    timestamp: float = 0.0


def sidecar_path(output_dir: Path, base: str) -> Path:
    """返回某结果组的 sidecar 路径（output_dir/{base}.zh.srcinfo.json）。"""
    return output_dir / f"{base}{SIDECAR_SUFFIX}"


def read_source_info(sidecar: Path) -> SourceInfo | None:
    """读取 sidecar；文件缺失/损坏时返回 None（不抛异常）。"""
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return SourceInfo(
            source_hash=str(data.get("source_hash", "")),
            source_bytes_hash=str(data.get("source_bytes_hash", "")),
            source_name=str(data.get("source_name", "")),
            source_path=str(data.get("source_path", "")),
            lang_in=str(data.get("lang_in", "en")),
            lang_out=str(data.get("lang_out", "zh")),
            translator=str(data.get("translator", "")),
            output_mode=str(data.get("output_mode", "dual")),
            timestamp=float(data.get("timestamp", 0.0)),
        )
    except Exception:
        return None


def write_source_info(sidecar: Path, info: SourceInfo) -> None:
    """写入 sidecar（原子写入：先写临时文件再替换，避免半写损坏）。"""
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(
        json.dumps(asdict(info), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(sidecar)
