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
import ssl
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urljoin

import requests


class TextTranslationError(RuntimeError):
    """文本翻译失败。"""


class BatchFormatError(TextTranslationError):
    """批量翻译返回不符合编号结构约定（调用方应回退逐段翻译）。"""


@dataclass(frozen=True)
class TextTranslationProfile:
    translator: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    lang_in: str = "en"
    lang_out: str = "zh"
    glossary: str = ""          # 术语表，每行一条「原文=译法」，注入 system prompt


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


def _certifi_cafile() -> str | None:
    """返回 certifi 的 CA 证书包路径；不可用时返回 None。"""
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def _create_ssl_context(tls12_only: bool = False) -> ssl.SSLContext:
    """创建 SSL 上下文，优先使用 certifi 的 CA 证书包。

    规避 Windows 证书库中存在 OpenSSL 无法解析的损坏证书时，
    ssl.load_default_certs() 报 [ASN1: NOT_ENOUGH_DATA] not enough data 的问题
    （cafile 指定后 create_default_context 会跳过 load_default_certs）。

    tls12_only=True 时限制最大 TLS 版本为 1.2（应对部分服务器/中间设备
    对 TLS 1.3 握手处理不当的情况）。
    """
    cafile = _certifi_cafile()
    try:
        ctx = (
            ssl.create_default_context(cafile=cafile)
            if cafile
            else ssl.create_default_context()
        )
    except (ssl.SSLError, OSError):
        # 兜底：证书库 / CA 包均不可用时，退化为不校验服务端证书
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if tls12_only:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _is_ssl_handshake_failure(exc: BaseException) -> bool:
    """判断异常是否为 TLS 握手失败（值得回退 TLS 版本重试）。"""
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return isinstance(getattr(exc, "args", [None])[0], ssl.SSLError)
    return False


# ── 连接复用（粗糙翻译高频调用的关键优化）─────────────────────
# 粗糙翻译在 asyncio.to_thread 的线程池里发起数百次请求，
# 线程局部 Session 启用 HTTP keep-alive 连接池，省去每次握手的 RTT。
_tls = threading.local()


class _TLS12Adapter(requests.adapters.HTTPAdapter):
    """强制 TLS ≤1.2 的连接适配器。

    部分服务器/中间设备对 TLS 1.3 握手处理不当；默认会话失败时
    用本适配器回退重试一次（与旧 urllib 版 _urlopen_with_tls_fallback 等价）。
    """

    def __init__(self, **kwargs) -> None:
        self._ssl_ctx = _create_ssl_context(tls12_only=True)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["ssl_context"] = self._ssl_ctx
        return super().init_poolmanager(*args, **kwargs)


def _openai_session(tls12: bool = False) -> requests.Session:
    key = "openai_session_tls12" if tls12 else "openai_session"
    s: requests.Session | None = getattr(_tls, key, None)
    if s is None:
        s = requests.Session()
        if tls12:
            s.mount("https://", _TLS12Adapter())
        setattr(_tls, key, s)
    return s


def _native_session() -> requests.Session:
    s: requests.Session | None = getattr(_tls, "native_session", None)
    if s is None:
        s = requests.Session()
        _tls.native_session = s
    return s


# ── Bing 令牌缓存 ──────────────────────────────────────────
# 旧实现每段都重新 GET 页面解析令牌（2 次 RTT/段）；令牌一般 ~10 分钟有效，
# 按 endpoint 缓存后数百段只需解析一两次。缓存值含 root（重定向后的真实
# 域名，POST ttranslatev3 必须用它）。
_BING_TOKEN_TTL = 480.0  # 秒
_bing_token_cache: dict[str, tuple[float, str, str, str, str, str]] = {}


def _get_cached_bing_auth(endpoint: str) -> tuple[str, str, str, str, str] | None:
    cached = _bing_token_cache.get(endpoint)
    if cached and cached[0] > time.monotonic():
        return cached[1:]  # (root, ig, iid, key, token)
    return None


def _store_bing_auth(
    endpoint: str, root: str, ig: str, iid: str, key: str, token: str
) -> None:
    _bing_token_cache[endpoint] = (
        time.monotonic() + _BING_TOKEN_TTL, root, ig, iid, key, token,
    )


def _invalidate_bing_auth(endpoint: str) -> None:
    _bing_token_cache.pop(endpoint, None)


async def translate_text(
    text: str,
    profile: Mapping[str, str] | TextTranslationProfile,
    source_lang: str,
    target_lang: str,
    *,
    context: str | None = None,
    is_heading: bool = False,
    glossary: str | None = None,
) -> str:
    """异步翻译短文本。

    :param context:   前文语境（如上一段末尾），仅供模型理解，不会被翻译输出
    :param is_heading: 文本是否为章节标题（影响措辞提示）
    :param glossary:  术语表文本（每行「原文=译法」），覆盖 profile 内的同名字段
    """
    resolved_profile = normalize_translation_profile(profile)
    if glossary:
        resolved_profile = TextTranslationProfile(
            translator=resolved_profile.translator,
            api_key=resolved_profile.api_key,
            model=resolved_profile.model,
            base_url=resolved_profile.base_url,
            lang_in=resolved_profile.lang_in,
            lang_out=resolved_profile.lang_out,
            glossary=glossary,
        )
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
        context or "",
        bool(is_heading),
    )


def _build_system_prompt(
    source_lang: str,
    target_lang: str,
    *,
    is_heading: bool = False,
    glossary: str = "",
) -> str:
    """学术论文向 system prompt：保留公式/引用标记、术语表约束。"""
    prompt = (
        "你是一位专业的学术论文翻译助手。"
        f"请将输入内容从{_language_name(source_lang)}翻译成{_language_name(target_lang)}。"
        "要求：译文准确流畅、符合学术表达习惯；"
        "公式、变量、数字、单位、引用标记（如 [12]、(Smith et al., 2020)、Fig. 3）保持原样不译；"
        "专业术语按学界通用译法；不要添加任何解释、前后缀、编号或代码块。"
    )
    if is_heading:
        prompt += "当前文本是章节标题，译文应简洁凝练。"
    glossary = (glossary or "").strip()
    if glossary:
        prompt += "\n术语表（必须严格遵守）：\n" + glossary
    return prompt


def _build_user_content(text: str, context: str = "") -> str:
    """组装用户消息；前文语境放在 <context> 块中并声明不译。"""
    ctx = (context or "").strip()
    if ctx:
        tail = ctx[-200:]
        return f"<context>\n{tail}\n</context>\n以上上下文仅供理解参考，不要翻译或输出。\n\n{text}"
    return text


def _build_headers(profile: TextTranslationProfile) -> dict[str, str]:
    translator = profile.translator.lower()
    api_key = profile.api_key.strip()
    headers = {"Content-Type": "application/json"}
    if translator == "azure":
        if api_key:
            headers["api-key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_chat_completion(
    endpoint: str,
    headers: dict[str, str],
    payload: dict,
) -> dict:
    """POST chat/completions（线程局部 Session 复用连接）。

    TLS 握手失败时用 TLS1.2 会话回退一次；HTTP ≥400 抛带响应体摘要的
    TextTranslationError；网络异常原样抛出由调用方包装。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        resp = _openai_session().post(endpoint, data=body, headers=headers, timeout=60)
    except requests.exceptions.SSLError:
        resp = _openai_session(tls12=True).post(
            endpoint, data=body, headers=headers, timeout=60
        )
    if resp.status_code >= 400:
        raise TextTranslationError(
            f"翻译请求失败：HTTP {resp.status_code} {resp.reason}。"
            f"{_extract_error_detail(resp.text)}"
        )
    try:
        return json.loads(resp.text)
    except ValueError as exc:
        raise TextTranslationError(
            f"翻译服务返回了无法解析的结果。{_shorten(resp.text, 200)}"
        ) from exc


# ── 批量翻译（OpenAI 兼容通道）─────────────────────────────
# 一次请求携带多段（编号分隔），请求数降为 1/N。模型不守格式时抛
# BatchFormatError，调用方回退逐段翻译。
BATCH_MAX_SEGMENTS = 8
BATCH_MAX_CHARS = 2000

_BATCH_SEP_RE = re.compile(r"<<<<(\d+)>>>>")


def supports_batch(profile: Mapping[str, str] | TextTranslationProfile) -> bool:
    """该翻译服务是否支持批量接口（原生网页通道与 DeepL 不支持）。"""
    t = _profile_get(profile, "translator").lower()
    return t not in NATIVE_TRANSLATOR_DEFAULTS and t != "deepl"


async def translate_batch(
    texts: list[str],
    profile: Mapping[str, str] | TextTranslationProfile,
    source_lang: str,
    target_lang: str,
    *,
    context: str | None = None,
    glossary: str | None = None,
) -> list[str]:
    """批量翻译多段（仅 OpenAI 兼容通道）。

    返回与 texts 等长的译文列表；结构不符时抛 BatchFormatError。
    """
    resolved_profile = normalize_translation_profile(profile)
    if not supports_batch(resolved_profile):
        raise BatchFormatError("当前服务不支持批量翻译")

    joined = "\n".join(f"<<<<{i}>>>>\n{t}" for i, t in enumerate(texts))
    system = _build_system_prompt(
        source_lang, target_lang, glossary=glossary or resolved_profile.glossary
    )
    system += (
        "\n输入包含多个以 <<<<n>>>> 编号的段落；请逐段翻译，"
        "输出必须保持完全相同的 <<<<n>>>> 编号结构：每个编号后紧跟对应段落的译文，"
        "不得合并、拆分、省略段落，也不得增删编号。"
    )
    user = _build_user_content(joined, context or "")

    endpoint = _resolve_endpoint(resolved_profile.translator.lower(), resolved_profile.base_url)
    payload = {
        "model": resolved_profile.model.strip() or DEFAULT_FALLBACK_MODEL.get(
            resolved_profile.translator.lower(), "gpt-4o-mini"
        ),
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    data = await asyncio.to_thread(
        _post_chat_completion, endpoint, _build_headers(resolved_profile), payload
    )
    content = _extract_text(data).strip()

    matches = list(_BATCH_SEP_RE.finditer(content))
    if not matches:
        raise BatchFormatError("返回缺少 <<<<n>>>> 编号分隔符")
    parts: dict[int, str] = {}
    for mi, m in enumerate(matches):
        idx = int(m.group(1))
        end = matches[mi + 1].start() if mi + 1 < len(matches) else len(content)
        parts[idx] = content[m.end():end].strip()
    if set(parts.keys()) != set(range(len(texts))):
        raise BatchFormatError(f"编号不完整或缺段: {sorted(parts.keys())}")
    return [parts[i] for i in range(len(texts))]


def _translate_text_sync(
    text: str,
    profile: TextTranslationProfile,
    source_lang: str,
    target_lang: str,
    context: str = "",
    is_heading: bool = False,
) -> str:
    if not text.strip():
        return ""

    translator = profile.translator.lower()

    if translator in UNSUPPORTED_DIRECT_SERVICES:
        raise TextTranslationError(
            f"当前服务「{translator}」不支持即时翻译。"
        )

    endpoint = _resolve_endpoint(translator, profile.base_url)
    payload = {
        "model": profile.model.strip() or DEFAULT_FALLBACK_MODEL.get(
            translator, "gpt-4o-mini"
        ),
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": _build_system_prompt(
                    source_lang, target_lang,
                    is_heading=is_heading, glossary=profile.glossary,
                ),
            },
            {"role": "user", "content": _build_user_content(text, context)},
        ],
    }

    try:
        data = _post_chat_completion(endpoint, _build_headers(profile), payload)
    except TextTranslationError:
        raise
    except requests.exceptions.RequestException as exc:
        reason = getattr(exc, "reason", None) or exc
        raise TextTranslationError(f"翻译请求失败：{reason}") from exc

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
        return _translate_bing_sync(text, profile, source_lang, target_lang)
    if translator == "google":
        return _translate_google_sync(text, profile, source_lang, target_lang)
    raise TextTranslationError(f"当前服务「{translator}」不支持即时翻译。")


def _translate_bing_sync(
    text: str,
    profile: TextTranslationProfile,
    source_lang: str,
    target_lang: str,
) -> str:
    session = _native_session()
    endpoint = profile.base_url.strip() or NATIVE_TRANSLATOR_DEFAULTS["bing"]

    source_code = _map_bing_language(source_lang, is_target=False)
    target_code = _map_bing_language(target_lang, is_target=True)

    last_exc: Exception | None = None
    for attempt in (0, 1):  # 第二次用刷新后的令牌
        try:
            cached = _get_cached_bing_auth(endpoint)
            if cached is not None:
                root, ig, iid, key, token = cached
            else:
                root, ig, iid, key, token = _fetch_bing_auth(session, endpoint)

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
            return str(data[0]["translations"][0]["text"]).strip()
        except TextTranslationError:
            raise  # 解析类错误重试无意义
        except Exception as exc:  # noqa: BLE001 - 令牌过期/网络抖动统一刷新重试一次
            _invalidate_bing_auth(endpoint)
            last_exc = exc
    raise TextTranslationError(f"Bing 翻译失败：{last_exc}")


def _fetch_bing_auth(
    session: requests.Session, endpoint: str
) -> tuple[str, str, str, str, str]:
    """抓取 Bing 翻译页面并解析令牌（带缓存），返回 (root, ig, iid, key, token)。"""
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
    _store_bing_auth(endpoint, root, ig, iid, key, token)
    return root, ig, iid, key, token


def _translate_google_sync(
    text: str,
    profile: TextTranslationProfile,
    source_lang: str,
    target_lang: str,
) -> str:
    session = _native_session()
    endpoint = profile.base_url.strip() or NATIVE_TRANSLATOR_DEFAULTS["google"]
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
            glossary=str(profile.get("glossary", "") or ""),
        )

    base_url = data.base_url.strip() or _default_base_url(data.translator.lower())
    return TextTranslationProfile(
        translator=data.translator,
        api_key=data.api_key,
        model=data.model,
        base_url=base_url,
        lang_in=data.lang_in,
        lang_out=data.lang_out,
        glossary=data.glossary,
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


def _extract_error_detail(body: str) -> str:
    """从 HTTP 错误响应体中提取可读的错误信息。

    优先解析 OpenAI 兼容格式 {"error": {"message": ...}} / {"message": ...}，
    否则回退为原始响应体（截断到 500 字符）。
    """
    if not body:
        return ""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return _shorten(body, 500)
    message = None
    if isinstance(data, dict):
        err = data.get("error", data)
        message = err.get("message") if isinstance(err, dict) else str(err)
        if not message:
            message = data.get("message")
    if message:
        return _shorten(str(message), 500)
    return _shorten(body, 500)
