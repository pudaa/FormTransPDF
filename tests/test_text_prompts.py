"""text.py 翻译辅助函数测试（prompt / 上下文 / 批量解析 / profile）。"""

from __future__ import annotations

import asyncio

import src.core.translation.text as text_mod


# ── system prompt ───────────────────────────────────────────

def test_system_prompt_academic():
    p = text_mod._build_system_prompt("en", "zh")
    assert "学术" in p
    assert "[12]" in p and "Fig. 3" in p  # 引用/图表标记保留要求


def test_system_prompt_heading_and_glossary():
    p = text_mod._build_system_prompt(
        "en", "zh", is_heading=True, glossary="transformer=变换器"
    )
    assert "章节标题" in p
    assert "transformer=变换器" in p


def test_user_content_context_wrapping():
    u = text_mod._build_user_content("Hello.", context="Prev tail sentence.")
    assert u.startswith("<context>")
    assert "不要翻译或输出" in u
    assert u.endswith("Hello.")
    # 超长上下文截断到 200 字符
    long_ctx = "x" * 500
    u2 = text_mod._build_user_content("Hi", context=long_ctx)
    assert "x" * 200 in u2 and "x" * 201 not in u2.split("</context>")[0].replace("<context>\n", "")


def test_user_content_no_context():
    assert text_mod._build_user_content("Hi") == "Hi"


# ── 批量支持判定 / profile ──────────────────────────────────

def test_supports_batch():
    assert text_mod.supports_batch({"translator": "openai"})
    assert text_mod.supports_batch({"translator": "deepseek"})
    assert not text_mod.supports_batch({"translator": "bing"})
    assert not text_mod.supports_batch({"translator": "google"})
    assert not text_mod.supports_batch({"translator": "deepl"})


def test_normalize_carries_glossary():
    prof = text_mod.normalize_translation_profile(
        {"translator": "openai", "glossary": "attention=注意力"}
    )
    assert prof.glossary == "attention=注意力"
    prof2 = text_mod.normalize_translation_profile({"translator": "openai"})
    assert prof2.glossary == ""


# ── 批量解析 ────────────────────────────────────────────────

def test_batch_parse(monkeypatch):
    def fake_post(endpoint, headers, payload):
        return {"choices": [{"message": {"content":
            "<<<<0>>>>\n译文甲\n<<<<1>>>>\n译文乙\n<<<<2>>>>\n译文丙"}}]}

    monkeypatch.setattr(text_mod, "_post_chat_completion", fake_post)
    outs = asyncio.run(text_mod.translate_batch(["a", "b", "c"], {"translator": "openai"}, "en", "zh"))
    assert outs == ["译文甲", "译文乙", "译文丙"]


def test_batch_parse_malformed(monkeypatch):
    def bad_post(endpoint, headers, payload):
        return {"choices": [{"message": {"content": "模型无视编号"}}]}

    monkeypatch.setattr(text_mod, "_post_chat_completion", bad_post)
    try:
        asyncio.run(text_mod.translate_batch(["a"], {"translator": "openai"}, "en", "zh"))
        raised = False
    except text_mod.BatchFormatError:
        raised = True
    assert raised


def test_batch_rejects_native(monkeypatch):
    raised = False
    try:
        asyncio.run(text_mod.translate_batch(["a"], {"translator": "bing"}, "en", "zh"))
    except text_mod.BatchFormatError:
        raised = True
    assert raised
