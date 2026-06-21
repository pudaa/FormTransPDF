"""
即时文本翻译客户端。

用于主窗口中的短文本快速翻译，对接当前侧边栏的翻译配置。
默认优先使用 OpenAI 兼容的 chat/completions 接口；当服务方支持该协议时，
可直接复用现有配置中的服务名、模型、API Key 和 Base URL。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urljoin

import requests


class TextTranslationError(RuntimeError):
    """文本翻译失败。"""


@dataclass(frozen=True)
class TextTranslationProfile:
    translator: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    lang_in: str = "en"
    lang_out: str = "zh"


OPENAI_COMPATIBLE_DEFAULTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "grok": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "xinference": "http://localhost:9997/v1",
}

NATIVE_TRANSLATOR_DEFAULTS: dict[str, str] = {
    "bing": "https://www.bing.com/translator",
    "google": "https://translate.google.com/m",
}


DEFAULT_FALLBACK_MODEL = {
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "groq": "llama-3.3-70b-versatile",
    "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
    "grok": "grok-3-mini",
    "ollama": "llama3",
    "xinference": "qwen2.5",
}


UNSUPPORTED_DIRECT_SERVICES = {"deepl"}


async def translate_text(
    text: str,
    profile: Mapping[str, str] | TextTranslationProfile,
    source_lang: str,
    target_lang: str,
) -> str:
    """异步翻译短文本。"""
    resolved_profile = normalize_translation_profile(profile)
    translator = resolved_profile.translator.lower()

    if translator in NATIVE_TRANSLATOR_DEFAULTS:
        return await asyncio.to_thread(
            _translate_native_sync,
            text,
            resolved_profile,
            source_lang,
            target_lang,
        )

    if translator == "deepl":
        raise TextTranslationError(
            "当前即时翻译窗口暂不支持 DeepL；请切换到 OpenAI / DeepSeek / Bing / Google / Ollama 等服务。"
        )

    return await asyncio.to_thread(
        _translate_text_sync,
        text,
        resolved_profile,
        source_lang,
        target_lang,
    )


def _translate_text_sync(
    text: str,
    profile: TextTranslationProfile,
    source_lang: str,
    target_lang: str,
) -> str:
    if not text.strip():
        return ""

    translator = profile.translator.lower()
    api_key = profile.api_key.strip()
    model = profile.model.strip() or DEFAULT_FALLBACK_MODEL.get(
        translator, "gpt-4o-mini"
    )
    base_url = profile.base_url.strip() or _default_base_url(translator)

    if translator in UNSUPPORTED_DIRECT_SERVICES:
        raise TextTranslationError(
            f"当前服务「{translator}」不支持即时翻译。"
        )

    endpoint = _resolve_endpoint(translator, base_url)
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个专业、简洁的双语翻译助手。"
                    f"请将输入内容从{_language_name(source_lang)}翻译成{_language_name(target_lang)}。"
                    "只输出译文，不要添加解释、前后缀、编号或代码块。"
                    "如果原文含有换行，请尽量保留段落结构。"
                ),
            },
            {"role": "user", "content": text},
        ],
    }

    headers = {"Content-Type": "application/json"}
    if translator == "azure":
        api_key_header = "api-key"
    else:
        api_key_header = "Authorization"
        if api_key:
            headers[api_key_header] = f"Bearer {api_key}"
    if translator == "azure" and api_key:
        headers[api_key_header] = api_key

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise TextTranslationError(
            f"翻译请求失败：HTTP {exc.code} {exc.reason}。{_shorten(body)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TextTranslationError(f"翻译请求失败：{exc.reason}") from exc

    data = json.loads(raw)
    translated = _extract_text(data)
    if not translated.strip():
        raise TextTranslationError("翻译服务返回了空结果。")
    return translated.strip()


def _translate_native_sync(
    text: str,
    profile: TextTranslationProfile,
    source_lang: str,
    target_lang: str,
) -> str:
    translator = profile.translator.lower()
    if translator == "bing":
        return _translate_bing_sync(text, profile.base_url, source_lang, target_lang)
    if translator == "google":
        return _translate_google_sync(text, profile.base_url, source_lang, target_lang)
    raise TextTranslationError(f"当前服务「{translator}」不支持即时翻译。")


def _translate_bing_sync(
    text: str,
    base_url: str,
    source_lang: str,
    target_lang: str,
) -> str:
    session = requests.Session()
    endpoint = base_url or NATIVE_TRANSLATOR_DEFAULTS["bing"]
    response = session.get(endpoint, timeout=30)
    response.raise_for_status()

    page = response.text
    root = response.url
    if root.endswith("/translator"):
        root = root[: -len("translator")]
    elif root.endswith("translator"):
        root = root[: -len("translator")]

    ig_matches = re.findall(r'"ig":"(.*?)"', page)
    iid_matches = re.findall(r'data-iid="(.*?)"', page)
    helper_matches = re.findall(
        r'params_AbusePreventionHelper\s=\s\[(.*?),"(.*?)",', page
    )
    if not ig_matches or not iid_matches or not helper_matches:
        raise TextTranslationError("Bing 翻译页面结构发生变化，无法提取令牌。")

    key, token = helper_matches[0]
    ig = ig_matches[0]
    iid = iid_matches[-1]
    source_code = _map_bing_language(source_lang, is_target=False)
    target_code = _map_bing_language(target_lang, is_target=True)

    post = session.post(
        f"{root}ttranslatev3?IG={ig}&IID={iid}",
        data={
            "fromLang": source_code,
            "to": target_code,
            "text": text[:1000],
            "token": token,
            "key": key,
        },
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
        },
        timeout=30,
    )
    post.raise_for_status()

    data = post.json()
    try:
        return str(data[0]["translations"][0]["text"]).strip()
    except Exception as exc:
        raise TextTranslationError("Bing 翻译返回了无法解析的结果。") from exc


def _translate_google_sync(
    text: str,
    base_url: str,
    source_lang: str,
    target_lang: str,
) -> str:
    session = requests.Session()
    endpoint = base_url or NATIVE_TRANSLATOR_DEFAULTS["google"]
    response = session.get(
        endpoint,
        params={
            "tl": _map_google_language(target_lang),
            "sl": _map_google_language(source_lang),
            "q": text[:5000],
        },
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        },
        timeout=30,
    )
    response.raise_for_status()

    matches = re.findall(r'(?s)class="(?:t0|result-container)">(.*?)<', response.text)
    if not matches:
        raise TextTranslationError("Google 翻译返回了无法解析的结果。")
    return _remove_control_characters(html.unescape(matches[0]))


def _resolve_endpoint(translator: str, base_url: str) -> str:
    if translator == "azure":
        if not base_url:
            raise TextTranslationError(
                "Azure OpenAI 需要在侧边栏中填写 Base URL。"
            )
        base = base_url.rstrip("/")
        return f"{base}/chat/completions"

    if base_url:
        base = base_url.rstrip("/") + "/"
        return urljoin(base, "chat/completions")

    default = OPENAI_COMPATIBLE_DEFAULTS.get(translator)
    if default:
        return f"{default.rstrip('/')}/chat/completions"

    raise TextTranslationError(
        f"当前服务「{translator}」不支持即时翻译，且未提供可用 Base URL。"
    )


def _default_base_url(translator: str) -> str:
    return NATIVE_TRANSLATOR_DEFAULTS.get(translator, OPENAI_COMPATIBLE_DEFAULTS.get(translator, ""))


def normalize_translation_profile(
    profile: Mapping[str, str] | TextTranslationProfile,
) -> TextTranslationProfile:
    if isinstance(profile, TextTranslationProfile):
        data = profile
    else:
        data = TextTranslationProfile(
            translator=str(profile.get("translator", "openai") or "openai"),
            api_key=str(profile.get("api_key", "") or ""),
            model=str(profile.get("model", "") or ""),
            base_url=str(profile.get("base_url", "") or ""),
            lang_in=str(profile.get("lang_in", "en") or "en"),
            lang_out=str(profile.get("lang_out", "zh") or "zh"),
        )

    base_url = data.base_url.strip() or _default_base_url(data.translator.lower())
    return TextTranslationProfile(
        translator=data.translator,
        api_key=data.api_key,
        model=data.model,
        base_url=base_url,
        lang_in=data.lang_in,
        lang_out=data.lang_out,
    )


def _language_name(code: str) -> str:
    mapping = {
        "en": "English",
        "zh": "中文",
        "zh-cn": "中文（简体）",
        "zh-tw": "中文（繁体）",
        "ja": "日本語",
        "ko": "한국어",
        "fr": "Français",
        "de": "Deutsch",
        "es": "Español",
        "ru": "Русский",
        "auto": "自动检测",
    }
    return mapping.get(code.lower(), code)


def _map_bing_language(lang: str, is_target: bool) -> str:
    lang_lower = lang.lower()
    mapping = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh-tw": "zh-Hant",
        "auto": "en",
    }
    if is_target:
        return mapping.get(lang_lower, lang_lower)
    return mapping.get(lang_lower, lang_lower)


def _map_google_language(lang: str) -> str:
    lang_lower = lang.lower()
    mapping = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "zh-tw": "zh-TW",
    }
    return mapping.get(lang_lower, lang_lower)


def _remove_control_characters(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")


def _extract_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            text = first.get("text")
            if isinstance(text, str):
                return text

    for key in ("output_text", "translated_text", "content", "text"):
        value = data.get(key)
        if isinstance(value, str):
            return value

    return ""


def _profile_get(profile: Mapping[str, str] | TextTranslationProfile, key: str, default: str = "") -> str:
    if isinstance(profile, TextTranslationProfile):
        return getattr(profile, key, default)
    value = profile.get(key, default)
    return default if value is None else str(value)


def _shorten(text: str, limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
