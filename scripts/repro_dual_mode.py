"""
双栏 mode 独立 + 暂停后续传 验证脚本（offscreen）。

覆盖两个真实 bug：
1. 问题2「暂停后点按钮不再重启翻译」：
   - 布局切换时 rough.cancel() 暂停翻译，session 里留有部分译文；
   - 旧逻辑 _on_rough_toggled 见 has_rough_translations()==True 就只复用不续传 → 永久卡住；
   - 新逻辑：未在运行一律 start_rough_translation（内部自动分派 复用/续传/全量）。
2. 问题3「双栏左右都显示译文」：
   - 旧逻辑 session["mode"] 被左右栏共享，_activate_session 恢复 mode → 左右都译文；
   - 新逻辑：mode 不入 session，激活会话一律原文态，dual.set_cover_mode 只作用右栏。
"""
from __future__ import annotations

import os
import sys
import glob

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, r"D:/Codes/FormTransPDF")

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
for fp in glob.glob("C:/Windows/Fonts/msyh.ttc") + glob.glob("C:/Windows/Fonts/segoeui.ttf"):
    QFontDatabase.addApplicationFont(fp)

import fitz
from types import SimpleNamespace

from src.ui.pdf.viewer import PDFViewer
from src.ui.pdf.dual_viewer import DualRoughViewer
from src.ui.pdf.cover import (
    build_segments, COVER_TRANSPARENT, COVER_TRANSLATED,
)

# ── 造一个 1 页文本 PDF ──
PDF = r"D:/Codes/FormTransPDF/output/repro_mode_test.pdf"
doc = fitz.open()
page = doc.new_page(width=595, height=842)
for i in range(6):
    page.insert_text((72, 100 + i * 60), f"Paragraph number {i} with some text to translate.")
doc.save(PDF)
doc.close()


def inject_segments(viewer: PDFViewer, doc_id: int) -> list:
    """用 fitz 提取 span → build_segments → 填入 content_rect，返回 segments 列表。"""
    fitz_doc = fitz.open(PDF)
    p = fitz_doc[0]
    spans = []
    for block_idx, b in enumerate(p.get_text("dict")["blocks"]):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                bbox = s["bbox"]
                spans.append(SimpleNamespace(
                    page=0, text=s["text"],
                    pdf_x=bbox[0], pdf_y=bbox[1],
                    pdf_width=bbox[2] - bbox[0], pdf_height=bbox[3] - bbox[1],
                    font_size=s.get("size", 10.0), block_id=block_idx,
                    content_rect=None,
                ))
    segs = build_segments(spans)
    for seg in segs:
        seg.content_rect = QRectF(0, seg.pdf_y * 2, seg.pdf_width * 2, seg.pdf_height * 2)
    viewer._text_spans[0] = spans
    viewer._cover_segments[0] = segs
    viewer._text_layer_done = True
    fitz_doc.close()
    return segs


def main() -> int:
    fail = 0

    # ══ 场景 A：问题 3 —— 双栏 mode 独立（左原文、右译文）══
    dual = DualRoughViewer()
    dual.load_pdf(PDF)
    inject_segments(dual._left, 1)
    inject_segments(dual._right, 1)
    # 给右栏已有部分译文（模拟切换前翻译过）
    dual._right._rough_translations[(0, 0)] = "第一段译文"
    dual._right._rough_translations[(0, 1)] = "第二段译文"

    # 初始：布局切换后应回到原文态（左右都透明，无共享 mode 污染）
    a1 = dual._left.cover_mode == COVER_TRANSPARENT
    a2 = dual._right.cover_mode == COVER_TRANSPARENT
    print(f"[A] 初始 左={dual._left.cover_mode} 右={dual._right.cover_mode}")
    print(f"    A1 左栏透明: {'✓' if a1 else '✗'}  A2 右栏透明: {'✓' if a2 else '✗'}")

    # 用户点「译文」：只应影响右栏
    dual.set_cover_mode(COVER_TRANSLATED)
    a3 = dual._right.cover_mode == COVER_TRANSLATED
    a4 = dual._left.cover_mode == COVER_TRANSPARENT
    print(f"[A] 点译文后 左={dual._left.cover_mode} 右={dual._right.cover_mode}")
    print(f"    A3 右栏译文: {'✓' if a3 else '✗'}  A4 左栏仍原文: {'✓' if a4 else '✗'}")

    # 用户再点「原文」：只影响右栏
    dual.set_cover_mode(COVER_TRANSPARENT)
    a5 = dual._right.cover_mode == COVER_TRANSPARENT
    a6 = dual._left.cover_mode == COVER_TRANSPARENT
    print(f"[A] 点原文后 左={dual._left.cover_mode} 右={dual._right.cover_mode}")
    print(f"    A5 右栏原文: {'✓' if a5 else '✗'}  A6 左栏仍原文: {'✓' if a6 else '✗'}")

    # 同一 session 对象在左右栏共享时，set_cover_mode 不应写回 session
    a7 = "mode" not in dual._right._sessions[PDF]
    print(f"    A7 session 不再携带 mode 字段: {'✓' if a7 else '✗'}")
    fail += 0 if (a1 and a2 and a3 and a4 and a5 and a6 and a7) else 1

    # ══ 场景 B：问题 2 —— 部分译文 + 未运行 → start_rough_translation 应续传 ══
    # 模拟布局切换后：rough 被 cancel（is_running False），已有 2 段译文
    mono = PDFViewer()
    mono.load_pdf(PDF)
    segs = inject_segments(mono, 1)
    mono._rough_translations[(0, 0)] = "第一段译文"
    mono._rough_translations[(0, 1)] = "第二段译文"

    # monkeypatch rough.start：记录收到的 pending，不真正启动（避免依赖事件循环）。
    # 新 viewer 的 rough 无任务 → is_running 自然为 False（模拟「被布局切换暂停」）。
    calls: dict = {}

    def fake_start(doc_id, segments, profile, lang_in, lang_out, concurrency=4):
        calls["segments"] = segments

    mono._rough.start = fake_start

    b1 = not mono.rough_is_running()
    ok = mono.start_rough_translation({}, "en", "zh")
    pending = calls.get("segments")
    # 段总数
    total = len(segs)
    b2 = ok is True
    b3 = len(pending) == total - 2  # 已译 2 段被跳过，其余续传
    b4 = all((0, i) not in mono._rough_translations for (_pg, i, _t) in pending)  # 不含已译段
    b5 = mono.cover_mode == COVER_TRANSLATED  # 启动即进入译文态
    print(f"\n[B] 暂停续传：总段数={total} 已有译文=2 待译={len(pending) if pending else 'N/A'}")
    print(f"    B1 已暂停(running=False): {'✓' if b1 else '✗'}")
    print(f"    B2 start 返回 True: {'✓' if b2 else '✗'}")
    print(f"    B3 续传剩余 {total - 2} 段: {'✓' if b3 else '✗'}")
    print(f"    B4 不重复译已译段: {'✓' if b4 else '✗'}")
    print(f"    B5 进入译文态: {'✓' if b5 else '✗'}")
    fail += 0 if (b1 and b2 and b3 and b4 and b5) else 1

    # 场景 C：全部已译 → 复用不重译
    mono2 = PDFViewer()
    mono2.load_pdf(PDF)
    segs2 = inject_segments(mono2, 2)
    for i in range(len(segs2)):
        mono2._rough_translations[(0, i)] = f"译文{i}"
    calls2: dict = {}

    def fake_start2(doc_id, segments, profile, lang_in, lang_out, concurrency=4):
        calls2["segments"] = segments

    mono2._rough.start = fake_start2
    ok2 = mono2.start_rough_translation({}, "en", "zh")
    c1 = ok2 is True
    c2 = "segments" not in calls2  # 没有调用 rough.start → 不重译
    c3 = mono2.cover_mode == COVER_TRANSLATED
    print(f"\n[C] 全部已译：start 返回={ok2}, rough.start 被调用={'segments' in calls2}")
    print(f"    C1 返回 True: {'✓' if c1 else '✗'}  C2 未重译: {'✓' if c2 else '✗'}  C3 进入译文态: {'✓' if c3 else '✗'}")
    fail += 0 if (c1 and c2 and c3) else 1

    print(f"\n== 结果: {'全部通过 ✅' if fail == 0 else f'{fail} 项失败 ❌'} ==")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
