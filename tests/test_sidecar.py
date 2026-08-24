"""粗译 sidecar 持久化测试（records.py）。"""

from __future__ import annotations

from src.core.translation.records import (
    load_rough_sidecar,
    rough_cache_path,
    rough_translations_from_sidecar,
    save_rough_sidecar,
)


def test_path_naming(tmp_path):
    path = rough_cache_path(tmp_path, "a" * 64, "b" * 64)
    assert path.parent.name == "rough_cache"
    assert path.name.startswith("a" * 32)
    assert path.name.endswith(".rough.json")
    # 内容指纹缺失时退回字节指纹
    path2 = rough_cache_path(tmp_path, "", "b" * 64)
    assert path2.name.startswith("b" * 32)


def test_roundtrip_with_source_info(tmp_path):
    path = rough_cache_path(tmp_path, "a" * 64, "")
    pages = {0: [(0, "hello", "你好"), (1, "world", None)],
             1: [(0, "foo", "富")]}
    save_rough_sidecar(
        path,
        source_hash="a" * 64, bytes_hash="",
        lang_in="en", lang_out="zh",
        translator="openai", model="gpt-4o-mini",
        pages=pages,
        source_name="paper.pdf",
        source_path=r"D:\Papers\paper.pdf",
    )
    data = load_rough_sidecar(path)
    assert data is not None
    assert data["source_name"] == "paper.pdf"
    assert data["source_path"] == r"D:\Papers\paper.pdf"
    assert data["lang_out"] == "zh"

    t = rough_translations_from_sidecar(data)
    # 空译文（None）被剔除，不产生错位键
    assert t == {(0, 0): "你好", (1, 0): "富"}


def test_load_missing_or_corrupt(tmp_path):
    assert load_rough_sidecar(tmp_path / "nonexist.rough.json") is None
    bad = tmp_path / "bad.rough.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_rough_sidecar(bad) is None


def test_version_mismatch(tmp_path):
    import json
    p = tmp_path / "v.rough.json"
    p.write_text(json.dumps({"version": 999, "pages": {}}), encoding="utf-8")
    assert load_rough_sidecar(p) is None
