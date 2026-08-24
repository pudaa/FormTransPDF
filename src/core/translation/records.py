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
import time
from dataclasses import asdict, dataclass
from pathlib import Path

SIDECAR_SUFFIX = ".zh.srcinfo.json"

# ── 粗糙翻译 sidecar ──────────────────────────────────────
ROUGH_SIDECAR_SUFFIX = ".rough.json"
_ROUGH_SIDECAR_VERSION = 1


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


# ═══════════════════════════════════════════════════════════
# 粗糙翻译缓存（.rough.json）
#
# 与 BabelDOC 结果不同，粗译此前只存内存 —— 关闭应用即丢，重开同一 PDF
# 要重新消耗 API。这里把译文按「源文件指纹」落盘到 output/rough_cache/：
#   - 内容指纹优先（重命名/重保存稳定），字节指纹兜底；
#   - 同时保存每段原文：加载时与当前分段逐段比对，分段算法升级导致
#     失配的段自动跳过（不会错位注入）。
# ═══════════════════════════════════════════════════════════


def rough_cache_path(
    output_dir: str | Path, content_hash: str, bytes_hash: str
) -> Path:
    """粗译缓存路径：output/rough_cache/<指纹前32位>.rough.json。"""
    key = (content_hash or bytes_hash or "").strip()
    if not key:
        raise ValueError("指纹为空，无法定位粗译缓存")
    return Path(output_dir) / "rough_cache" / f"{key[:32]}{ROUGH_SIDECAR_SUFFIX}"


def save_rough_sidecar(
    path: Path,
    *,
    source_hash: str,
    bytes_hash: str,
    lang_in: str,
    lang_out: str,
    translator: str,
    model: str,
    pages: dict,
    source_name: str = "",
    source_path: str = "",
) -> None:
    """原子写入粗糙翻译结果。

    :param pages: {page: [(idx, 原文, 译文|None), ...]}（viewer.collect_rough_result）
    :param source_name / source_path: 源文件名与绝对路径 —— 历史面板据此展示
        「粗译」记录并支持点击重新打开源文件（按指纹自动命中缓存）。
    """
    payload = {
        "version": _ROUGH_SIDECAR_VERSION,
        "source_hash": source_hash,
        "source_bytes_hash": bytes_hash,
        "source_name": source_name,
        "source_path": source_path,
        "lang_in": lang_in,
        "lang_out": lang_out,
        "translator": translator,
        "model": model,
        "timestamp": time.time(),
        "pages": {
            str(pg): [{"i": idx, "src": src, "dst": dst} for (idx, src, dst) in rows]
            for pg, rows in pages.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_rough_sidecar(path: str | Path) -> dict | None:
    """读取粗译缓存；缺失/损坏/版本不符返回 None（不抛异常）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _ROUGH_SIDECAR_VERSION:
        return None
    return data


def rough_translations_from_sidecar(data: dict) -> dict[tuple[int, int], str]:
    """从 sidecar 提取 {(page, idx): 译文}（丢弃空译文）。"""
    out: dict[tuple[int, int], str] = {}
    for pg_str, rows in (data.get("pages") or {}).items():
        try:
            pg = int(pg_str)
        except (TypeError, ValueError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            dst = row.get("dst")
            if isinstance(dst, str) and dst.strip():
                try:
                    idx = int(row.get("i", -1))
                except (TypeError, ValueError):
                    continue
                out[(pg, idx)] = dst
    return out
