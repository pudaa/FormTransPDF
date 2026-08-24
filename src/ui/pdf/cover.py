"""
粗糙翻译的覆盖层数据模型 — 段落级 segment 构建。

翻译单位设计（避免逐字块翻译、保留上下文）：
1. span → 行：同 block 内按 y 中心距聚类（同一行内按 x 排序，间隙补空格）
2. 行 → 段：直接使用 PyMuPDF 的 block 结构（即 PDF 的视觉段落）
3. 段内文本拼接时处理「行末连字符断词」：
       ex-  +  amples of ...  →  examples of ...      （去掉连字符）
       state-of-  +  the-art  →  state-of-the-art     （复合词延续，保留连字符）
4. 过长的段按行打包切成 ≤ MAX_SEG_CHARS 的块（断词对保证不跨块）

渲染模式常量：
    COVER_TRANSPARENT   透明（现状：只画高亮/工具栏，原 PDF 完全透出）
    COVER_ORIGINAL      白底 + 原文黑字（盖住原 PDF 文字；保留备选）
    COVER_TRANSLATED    白底 + 译文黑字（译文缺失的 segment 回退原文）
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF

# 注意：build_segments 的 TextSpan 注解为字符串（__future__ 注解惰性求值），
# 运行时并不真正导入 text_extractor，避免本模块连带拉起 fitz 依赖。

# ── 渲染模式 ──────────────────────────────────────────────
COVER_TRANSPARENT = "transparent"
COVER_ORIGINAL = "original"
COVER_TRANSLATED = "translated"

COVER_MODES = (COVER_TRANSPARENT, COVER_ORIGINAL, COVER_TRANSLATED)

# 同一条行的 y 中心距容差（相对行内最大字号）
_LINE_Y_TOLERANCE = 0.6
# 同一视觉行内 span 的 x 间隙上限（相对字号）：
# PyMuPDF 有时把「跨栏文字」合并进同一 block，其 span 横跨两个栏（x 间隙 ≈ 栏间隙，
# 通常 > 1×字号）；正文内 span 间隙（字间距/空格）一般 < 0.5×字号。
# 间隙超过此阈值 → 断成新行 → 行矩形最小化，白底不再跨栏遮盖邻栏。
_COLUMN_GAP_TOLERANCE = 0.75
# 判定 span 之间存在可见间隙（需插入空格）的阈值（相对 span 字号）
_GAP_TOLERANCE = 0.15
# 单个翻译块最大字符数（超过则按行拆块，兼顾 API 长度限制与渲染盒）
MAX_SEG_CHARS = 500
# 行末连字符（断词或复合词延续均以此结尾）
_HYPHENS = ("-", "\u2010", "\u2011")
# 同 block 内首行字号若大于其余行（中位×该系数 且 绝对差 ≥ 此值），视为标题，单独成段
_HEADING_SIZE_RATIO = 1.08
_HEADING_ABS_PT_DIFF = 0.6
# 同 block 内两行间垂直间隙若大于行高×该系数，视为新的逻辑段（视觉空行）
_PARAGRAPH_GAP_RATIO = 1.4

# ── 垃圾段过滤 ────────────────────────────────────────────
# 目的：不把「不该翻译的东西」送进 LLM —— 纯数字/标点、公式碎片、参考文献
# 条目、页眉页脚、arXiv 竖排水印。命中即整段丢弃：不送翻、不画白底，
# 原文原样透出（公式/引用标记保持可读，这正是期望行为）。
# 附带收益：这些垃圾段往往是"全宽/擦边假邻居"，过滤后碰撞计算明显变干净。
_JUNK_TEXT_RE = re.compile(
    r"^[\s\d\.\,\;\:\-\u2010-\u2015\(\)\[\]\{\}/\\|'\"`~!@#\$%\^&\*\+=<>\?"
    r"°±×÷≈≠≤≥·•‹›«»§¶†‡‰µ∞∫∑∏√∂∇∈∉⊂⊃∪∩∀∃Å"
    r"Ωαβγδεζηθικλμνξοπρστυφχψω"
    r"ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ]*$"
)
# 参考文献条目："[1] Smith, J. ..." / "[12] ..."
_REF_ENTRY_RE = re.compile(r"^\[\d{1,3}\]\s*\S")
# 参考文献区标题（命中后本页其后所有段跳过）
_REF_HEADING_SET = {"references", "bibliography", "works cited", "reference"}
# 页眉/页脚带：距页顶/底该比例以内且足够矮的短段视为页眉页脚
_MARGIN_BAND_RATIO = 0.055
_MAX_BAND_SEG_H = 0.09      # 段高不超过页高的该比例（排除正文首行误伤）
_MAX_BAND_TEXT_LEN = 130


def is_junk_text(text: str) -> bool:
    """判定是否为不值得翻译的垃圾文本（纯数字/符号/公式碎片/超短标记）。"""
    t = (text or "").strip()
    if not t or len(t) <= 2:
        return True
    if _JUNK_TEXT_RE.fullmatch(t):
        return True
    # 字母占比过低 → 公式/符号主导（如 "x2 +y2 = r2"、"Fig. 3(a)" 变体）
    letters = sum(1 for c in t if c.isalpha())
    if letters / len(t) < 0.35:
        return True
    return False


def is_reference_entry(text: str) -> bool:
    """判定是否为编号式参考文献条目（[n] Author ...）。"""
    return bool(_REF_ENTRY_RE.match((text or "").strip()))


def is_references_heading(text: str) -> bool:
    """判定是否为 References/Bibliography 区标题。"""
    t = (text or "").strip().lower().rstrip(":.").strip()
    return t in _REF_HEADING_SET


def _in_margin_band(seg: "CoverSegment", pw: float, ph: float) -> bool:
    """判定段是否位于页眉/页脚带或竖排水印位置。

    - 顶部/底部 _MARGIN_BAND_RATIO 带内、足够矮的短段 → 页眉/页脚；
      （论文标题通常在 7% 以下才开始，不会被误伤）
    - 极窄但极高的段（宽 < 5% 页宽 且 高 > 50% 页高）→ arXiv 类竖排水印。
    """
    y0, y1 = seg.pdf_y, seg.pdf_y + seg.pdf_height
    short_enough = (
        seg.pdf_height < ph * _MAX_BAND_SEG_H and len(seg.text) < _MAX_BAND_TEXT_LEN
    )
    if short_enough and (y1 < ph * _MARGIN_BAND_RATIO or y0 > ph * (1 - _MARGIN_BAND_RATIO)):
        return True
    x0, x1 = seg.pdf_x, seg.pdf_x + seg.pdf_width
    if (x1 - x0) < pw * 0.05 and (y1 - y0) > ph * 0.5:
        return True
    return False


@dataclass
class CoverSegment:
    """覆盖层绘制/翻译单位 — 一个段落（或长段落的一部分）。"""

    page: int
    text: str                      # 原文（已做行末连字符拼接）
    pdf_x: float
    pdf_y: float
    pdf_width: float
    pdf_height: float
    font_size: float               # 块内最大字号（pt）
    # 行级包围盒（PDF 坐标 (x0,y0,x1,y1)）：每行精确贴合原文文字范围，
    # 渲染时逐行白底 + 逐行流动换行 —— 解决"整段矩形白底不贴合原文"的瑕疵
    line_rects: list = field(default_factory=list)
    # 每行原文文本（与 line_rects 同序）：译文比原文短时，未译行用原文回退显示
    line_texts: list = field(default_factory=list)
    # 标题段标记（heading-split 切出的独立段）。渲染时用更高的底色 alpha，
    # 让标题视觉突出；同时参考沉浸式翻译给整段一个半透明"段框"底色。
    is_heading: bool = False
    # 运行期由主线程填充
    content_rect: QRectF | None = field(default=None)
    # 显示文本：译文到位时由 viewer 填充，覆盖层优先用它；None 则画原文
    display_text: str | None = field(default=None)
    # 用户拖拽偏移（页面局部像素，默认 (0,0) = 未拖动）。仅影响本段绘制位置，
    # 用于把被其他字块遮盖的段拖出来查看；不参与 next_y 碰撞计算。
    offset_x: float = field(default=0.0)
    offset_y: float = field(default=0.0)


@dataclass
class _LineInfo:
    """一行文本及其包围盒。"""
    page: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float


@dataclass
class _LineAcc:
    """行构建中的累加器。"""
    page: int
    spans: list
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float


def _ends_with_hyphen(text: str) -> bool:
    return text.rstrip().endswith(_HYPHENS)


def _first_word_has_hyphen(text: str) -> bool:
    first = text.split(" ", 1)[0]
    return first.endswith(_HYPHENS) or any(h in first for h in _HYPHENS)


def build_segments(spans: list, page_size: tuple | None = None) -> list[CoverSegment]:
    """把一页的 span 聚合成段落级 segment（block → 行 → 连字符拼接 → 分块）。

    同 block 内进一步切分：
    - 首行字号明显大于其余行（中位数×1.15）→ 拆出标题段
    - 行间隙 > 1.4×行高 → 视为新的逻辑段
    - 相邻行 x 范围基本错开 → 列流不连续处切分（跨栏合并 block 兜底）

    :param page_size: (页宽, 页高) PDF 点单位；提供时启用页眉/页脚/竖排水印过滤
    """
    spans = [s for s in spans if s.text and s.text.strip()]
    if not spans:
        return []

    spans.sort(key=lambda s: (getattr(s, "block_id", 0), s.pdf_y, s.pdf_x))

    pw: float | None = None
    ph: float | None = None
    if page_size and len(page_size) == 2:
        pw, ph = float(page_size[0]), float(page_size[1])

    in_refs = False  # 命中 References 标题后，本页其后所有段跳过
    segments: list[CoverSegment] = []
    for _block_id, block_spans in itertools.groupby(
        spans, key=lambda s: getattr(s, "block_id", 0)
    ):
        block_lines = _build_lines(list(block_spans))
        if not block_lines:
            continue
        # 同 block 内按"标题/正文" + "行间隙" + "列流不连续" 切子段
        sub_ranges = _split_block_lines(block_lines)
        for sr_idx, (s_idx, e_idx) in enumerate(sub_ranges):
            sub_lines = block_lines[s_idx:e_idx]
            pieces = _join_lines(sub_lines)
            if not pieces:
                continue
            # heading-split 仅当 sub_ranges > 1 时切出标题；第一个区间是标题段
            is_heading = sr_idx == 0 and len(sub_ranges) > 1
            for s, e in _pack_pieces(pieces):
                chunk_lines = sub_lines[pieces[s][1]:pieces[e - 1][2]]
                text = " ".join(p[0] for p in pieces[s:e])
                seg = _segment_from_lines(chunk_lines, text)
                seg.is_heading = is_heading
                # ── 垃圾段过滤：命中即丢弃（原文透出，不送翻不覆盖）──
                if is_junk_text(seg.text):
                    continue
                if is_references_heading(seg.text):
                    in_refs = True
                    continue
                if in_refs or is_reference_entry(seg.text):
                    continue
                if pw is not None and ph is not None and _in_margin_band(seg, pw, ph):
                    continue
                segments.append(seg)
    return segments


def _split_block_lines(lines: list[_LineInfo]) -> list[tuple[int, int]]:
    """同 block 内切分为多个子段，返回 [(start, end), ...]（半开区间）。

    规则：
    1. 标题检测：若首行字号 > 中位字号 × _HEADING_SIZE_RATIO，前 1（或首两行连续）
       作为标题段，其余作为正文段。
    2. 段落间隙：两行顶部间距 > max(两行高) × _PARAGRAPH_GAP_RATIO → 在该处切分。
    3. 列流不连续：相邻两行 x 范围基本错开（重叠 < 30% 且水平间隙明显）→ 切分。
       PyMuPDF 偶尔把左右栏文字合进同一 block —— 不切断则段 bbox 横跨两栏，
       译文会横穿栏沟、白底盖住邻栏文字，且该"全宽假段"会成为上方所有段的
       下方碰撞边界，把邻段可用高度压碎 → 过度缩字。此规则为根因兜底。
    """
    n = len(lines)
    if n == 0:
        return []

    sizes = [ln.font_size for ln in lines]
    cut_after: set[int] = set()  # 在 cut_after 中的行号 i 之后切分

    # 规则 1：首行是标题 → 在首行后切（中位数×系数 + 绝对差双重判定）
    body_sizes = sizes[1:]
    if body_sizes:
        sorted_body = sorted(body_sizes)
        median_body = sorted_body[len(sorted_body) // 2]
        if (sizes[0] > median_body * _HEADING_SIZE_RATIO
                and sizes[0] - median_body >= _HEADING_ABS_PT_DIFF):
            cut_after.add(0)

    # 规则 2：行间大间隙处切分
    for i in range(1, n):
        prev = lines[i - 1]
        cur = lines[i]
        gap = cur.y0 - prev.y1
        max_h = max(prev.y1 - prev.y0, cur.y1 - cur.y0)
        if gap > max_h * _PARAGRAPH_GAP_RATIO:
            cut_after.add(i - 1)

    # 规则 3：列流不连续处切分（跨栏合并 block 兜底，见 docstring）
    for i in range(1, n):
        prev, cur = lines[i - 1], lines[i]
        ovl = min(prev.x1, cur.x1) - max(prev.x0, cur.x0)
        min_w = min(prev.x1 - prev.x0, cur.x1 - cur.x0)
        gap_x = max(prev.x0, cur.x0) - min(prev.x1, cur.x1)
        fs = max(1.0, min(prev.font_size, cur.font_size))
        if min_w > 0 and ovl <= 0.3 * min_w and gap_x > _COLUMN_GAP_TOLERANCE * fs:
            cut_after.add(i - 1)

    # 收集区间
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in sorted(cut_after):
        if i < start:
            continue
        end = i + 1
        if end > start:
            ranges.append((start, end))
        start = end
    if start < n:
        ranges.append((start, n))
    return ranges


def _build_lines(spans) -> list[_LineInfo]:
    """把同一 block 的 span 聚类成视觉行。

    聚类键 = y 中心（同行），并做「跨栏合并坏段」处理：同一视觉行内
    span 的 x 间隙若超过 _COLUMN_GAP_TOLERANCE × 字号（≈ 栏间隙），
    说明 PyMuPDF 把多栏文字合并在同一 block —— 断成新行，使每个行矩形
    最小化（白底精确贴合单栏内文字，不跨栏遮盖邻栏）。
    """
    accs: list[_LineAcc] = []
    for sp in spans:
        yc = sp.pdf_y + sp.pdf_height / 2
        if accs:
            last = accs[-1]
            last_yc = (last.y0 + last.y1) / 2
            same_y = abs(yc - last_yc) <= _LINE_Y_TOLERANCE * max(last.font_size, sp.font_size)
            x_gap = sp.pdf_x - last.x1
            if same_y and x_gap <= _COLUMN_GAP_TOLERANCE * max(last.font_size, sp.font_size):
                last.spans.append(sp)
                last.x0 = min(last.x0, sp.pdf_x)
                last.y0 = min(last.y0, sp.pdf_y)
                last.x1 = max(last.x1, sp.pdf_x + sp.pdf_width)
                last.y1 = max(last.y1, sp.pdf_y + sp.pdf_height)
                last.font_size = max(last.font_size, sp.font_size)
                continue
        accs.append(_LineAcc(
            page=sp.page, spans=[sp],
            x0=sp.pdf_x, y0=sp.pdf_y,
            x1=sp.pdf_x + sp.pdf_width, y1=sp.pdf_y + sp.pdf_height,
            font_size=sp.font_size,
        ))

    lines: list[_LineInfo] = []
    for acc in accs:
        parts: list[str] = []
        prev_end: float | None = None
        for sp in sorted(acc.spans, key=lambda s: s.pdf_x):
            if prev_end is not None and sp.pdf_x - prev_end > _GAP_TOLERANCE * sp.font_size:
                parts.append(" ")
            parts.append(sp.text)
            prev_end = sp.pdf_x + sp.pdf_width
        text = "".join(parts).strip()
        if not text:
            continue
        lines.append(_LineInfo(
            page=acc.page, text=text,
            x0=acc.x0, y0=acc.y0, x1=acc.x1, y1=acc.y1,
            font_size=acc.font_size,
        ))
    return lines


def _join_lines(lines: list[_LineInfo]) -> list[tuple[str, int, int]]:
    """把行拼接成片段（含行末连字符断词处理）。

    返回 [(text, line_start, line_end_exclusive)]：
    - 行末无连字符：片段 = 单独一行
    - 行末有连字符：
        下一行首词带连字符（复合词延续，如 the-art）→ 保留连字符直接拼接
        否则视为断词（如 ex- + amples）→ 去掉连字符拼接
    """
    pieces: list[tuple[str, int, int]] = []
    for i, ln in enumerate(lines):
        raw = ln.text.strip()
        if not raw:
            continue
        if pieces and _ends_with_hyphen(pieces[-1][0]):
            if _first_word_has_hyphen(raw):
                merged = pieces[-1][0].rstrip() + raw
            else:
                merged = pieces[-1][0].rstrip()[:-1] + raw
            pieces[-1] = (merged, pieces[-1][1], i + 1)
        else:
            pieces.append((raw, i, i + 1))
    return pieces


def _pack_pieces(pieces: list[tuple[str, int, int]]) -> list[tuple[int, int]]:
    """按字符数把片段打包成 ≤ MAX_SEG_CHARS 的块（返回 pieces 的 [start, end)）。"""
    ranges: list[tuple[int, int]] = []
    n = len(pieces)
    s = 0
    while s < n:
        e = s
        acc = 0
        while e < n and (acc == 0 or acc + len(pieces[e][0]) + 1 <= MAX_SEG_CHARS):
            acc += len(pieces[e][0]) + 1
            e += 1
        ranges.append((s, e))
        s = e
    return ranges


def _segment_from_lines(lines: list[_LineInfo], text: str) -> CoverSegment:
    x0 = min(ln.x0 for ln in lines)
    y0 = min(ln.y0 for ln in lines)
    x1 = max(ln.x1 for ln in lines)
    y1 = max(ln.y1 for ln in lines)
    return CoverSegment(
        page=lines[0].page,
        text=text,
        pdf_x=x0,
        pdf_y=y0,
        pdf_width=x1 - x0,
        pdf_height=y1 - y0,
        font_size=max(ln.font_size for ln in lines),
        line_rects=[(ln.x0, ln.y0, ln.x1, ln.y1) for ln in lines],
        line_texts=[ln.text for ln in lines],
    )
