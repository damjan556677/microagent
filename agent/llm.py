"""OpenRouter chat client over `requests` — a dependency-free stand-in for litellm.

We own the HTTP round-trip: POST the OpenAI-compatible /chat/completions endpoint,
stream Server-Sent Events, reassemble streamed tool-call deltas, and surface the
model's reasoning trace. Patterns (model registry, retry/backoff, tool-call markup
recovery) are ported from /amd4/cpu/ebpf-opt4/agentic.py — reimplemented without
litellm/anyio per the project's no-pip-dependency rule.

Public surface:
    resolve_model(alias) -> provider model id
    model_label(alias)   -> pretty name
    stream_complete(cfg, messages, tools) -> generator of StreamDelta, then a final Completion
"""
import json
import re
import sys
import time
from dataclasses import dataclass, field

import requests

from .events import StreamDelta


# ============================================================ model registry
DEFAULT_MODEL = "deepseek"

# alias -> OpenRouter model id (no "openrouter/" prefix; we call OpenRouter directly).
OPENROUTER = {
    "deepseek":      "deepseek/deepseek-v4-pro",   # reasoner WITH reliable tool use — default
    "ds-v4-pro":     "deepseek/deepseek-v4-pro",
    "ds-v4-flash":   "deepseek/deepseek-v4-flash",
    "deepseek-r1":   "deepseek/deepseek-r1",        # pure reasoner; weak function-calling
    "ds-r1":         "deepseek/deepseek-r1",
    "deepseek-chat": "deepseek/deepseek-chat-v3.1",
    "opus-4.8":      "anthropic/claude-opus-4.8",
    "opus":          "anthropic/claude-opus-4.8",
    "sonnet-4.6":    "anthropic/claude-sonnet-4.6",
    "sonnet":        "anthropic/claude-sonnet-4.6",
    "kimi":          "moonshotai/kimi-k2.7-code",
    "kimi-k2.7":     "moonshotai/kimi-k2.7-code",
    "glm":           "z-ai/glm-5.2",
}
_PRETTY = {
    "deepseek/deepseek-r1": "DeepSeek R1", "deepseek/deepseek-chat-v3.1": "DeepSeek Chat v3.1",
    "deepseek/deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek/deepseek-v4-flash": "DeepSeek V4 Flash",
    "anthropic/claude-opus-4.8": "Opus 4.8", "anthropic/claude-sonnet-4.6": "Sonnet 4.6",
    "moonshotai/kimi-k2.7-code": "Kimi K2.7", "z-ai/glm-5.2": "GLM-5.2",
}


def resolve_model(name: str | None) -> str:
    """alias -> OpenRouter model id; an id containing '/' passes through as a literal."""
    if not name:
        name = DEFAULT_MODEL
    key = name.lower()
    if key in OPENROUTER:
        return OPENROUTER[key]
    return name if "/" in name else OPENROUTER[DEFAULT_MODEL]


def model_label(name: str | None) -> str:
    mid = resolve_model(name)
    return _PRETTY.get(mid, mid)


# ========================================================== endpoint resolution
# cfg.model is a SELECTOR: a bare port number (or a configured alias) routes to an
# internal vLLM server; anything else falls back to OpenRouter.
_MODELS_CACHE: dict = {}   # base_url -> /v1/models `data` list (one probe per server)
_CTX_CACHE: dict = {}      # (base_url, model) -> context window in tokens (0 = unknown)


@dataclass
class Endpoint:
    base_url: str
    api_key: str
    model: str
    reasoning: bool        # whether to send OpenRouter's reasoning:{effort} param
    label: str
    decode: str = "sse"    # transport: "sse" (stream) | "json" (non-stream) | "auto"
    max_ctx: int = 0       # context window in tokens (0 = unknown)


def _probe_headers(api_key: str) -> dict:
    # Accept-Encoding: identity — some gateways send a malformed gzip header (e.g. :8007).
    h = {"Accept-Encoding": "identity"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _models(base_url: str, api_key: str) -> list:
    """Cached GET /v1/models -> the `data` list (empty on failure)."""
    if base_url in _MODELS_CACHE:
        return _MODELS_CACHE[base_url]
    data = []
    try:
        r = requests.get(base_url.rstrip("/") + "/models", headers=_probe_headers(api_key), timeout=5)
        if r.status_code == 200:
            data = r.json().get("data") or []
    except Exception:
        data = []
    _MODELS_CACHE[base_url] = data
    return data


def _detect_model(base_url: str, api_key: str) -> str:
    """Best-effort served model id from /v1/models."""
    data = _models(base_url, api_key)
    return (data[0].get("id") or "") if data else ""


_CTX_RE = re.compile(r"maximum context length (?:is|of)\s+(\d+)", re.I)
_CTX_RE2 = re.compile(r"max_(?:model_len|total_tokens)\D*?(\d+)", re.I)


def _ctx_from_overflow(base_url: str, api_key: str, model: str) -> int:
    """Fallback for gateways that hide max_model_len: request max_tokens far above the
    window so the server returns a 400 that *names* the limit. Non-generating + cheap."""
    try:
        r = requests.post(base_url.rstrip("/") + "/chat/completions",
                          headers={**_probe_headers(api_key), "Content-Type": "application/json"},
                          json={"model": model or "x", "messages": [{"role": "user", "content": "hi"}],
                                "max_tokens": 100_000_000, "stream": False}, timeout=15)
        m = _CTX_RE.search(r.text or "") or _CTX_RE2.search(r.text or "")
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _detect_ctx(base_url: str, api_key: str, model: str) -> int:
    """Context window for `model`: prefer /v1/models max_model_len (direct vLLM); else an
    overflow probe (gateways). Cached per (server, model). 0 if undetermined."""
    key = (base_url, model)
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    ctx = 0
    for m in _models(base_url, api_key):
        if not model or m.get("id") == model:
            if m.get("max_model_len"):
                ctx = int(m["max_model_len"])
                break
    if not ctx:
        ctx = _ctx_from_overflow(base_url, api_key, model)
    _CTX_CACHE[key] = ctx
    return ctx


def resolve_endpoint(cfg, detect: bool = True) -> Endpoint:
    """Map cfg.model (a selector) to a concrete Endpoint.

    Bare port / internal alias -> internal server (http://host:port/v1, no reasoning
    param, key only if configured). Otherwise -> OpenRouter (the fallback).
    """
    sel = (cfg.model or "").strip()
    hit = cfg.internal.resolve(sel)
    if hit:
        host, port, model, decode, max_ctx = hit
        base = f"{cfg.internal.scheme}://{host}:{port}/v1"
        key = cfg.internal.api_key
        if not model and detect:
            model = _detect_model(base, key)
        if not max_ctx and detect:
            max_ctx = _detect_ctx(base, key, model)
        label = f"{model} @:{port}" if model else f"internal:{port}"
        return Endpoint(base, key, model or sel, cfg.internal.reasoning,
                        label, decode or "auto", max_ctx)
    return Endpoint(cfg.api_base, cfg.api_key, resolve_model(sel), True, model_label(sel), "sse")


def current_label(cfg) -> str:
    """Pretty label for the active selector (no network probe)."""
    return resolve_endpoint(cfg, detect=False).label


# ============================================================ assembled result
@dataclass
class ToolCallSpec:
    id: str
    name: str
    arguments: str           # raw JSON string (may be empty); the loop parses it


@dataclass
class Completion:
    content: str = ""
    reasoning: str = ""
    tool_calls: list = field(default_factory=list)   # list[ToolCallSpec]
    usage: dict | None = None
    finish_reason: str = "stop"
    error: str | None = None


# ===================================================== tool-call markup recovery
# Some open models (notably DeepSeek via OpenRouter) intermittently emit a tool call as
# MARKUP in the text/reasoning instead of structured tool_calls. Recover those so the loop
# doesn't stall. (Ported from agentic.py:401-452.)
_RE_INVOKE = re.compile(r'invoke\s+name="([^"]+)"\s*>(.*?)</[^>]*?invoke\s*>', re.S)
_RE_PARAM = re.compile(r'parameter\s+name="([^"]+)"\s*>(.*?)</[^>]*?parameter\s*>', re.S)
_RE_DS_FN = re.compile(r'function[^A-Za-z0-9_]{1,12}([A-Za-z0-9_]+)\s*```(?:json)?\s*(\{.*?\})\s*```', re.S)
_RE_TC_BLOCK = re.compile(r'<[^>]*?tool_calls\s*>.*?</[^>]*?tool_calls\s*>', re.S)
_THINK_SPAN = re.compile(r"<think>.*?</think>\s*", re.S)


def _coerce(v):
    v = (v or "").strip()
    try:
        return json.loads(v)
    except Exception:
        return v


def strip_markup(s: str) -> str:
    """Remove leaked tool-call / <think> markup from text we display."""
    s = _RE_TC_BLOCK.sub("", s or "")
    s = _RE_INVOKE.sub("", s)
    s = _THINK_SPAN.sub("", s)
    return s.replace("<think>", "").replace("</think>", "")


def extract_text_tool_calls(content: str) -> list:
    """Recover (name, args_dict) tool calls a model leaked into text as markup."""
    if not content:
        return []
    out = []
    if "invoke name=" in content:
        for m in _RE_INVOKE.finditer(content):
            body = m.group(2)
            args = {p.group(1).strip(): _coerce(p.group(2)) for p in _RE_PARAM.finditer(body)}
            if not args:
                jm = re.search(r'\{.*\}', body, re.S)
                if jm:
                    try:
                        args = json.loads(jm.group(0))
                    except Exception:
                        args = {}
            out.append((m.group(1).strip(), args if isinstance(args, dict) else {}))
    if not out:
        for m in _RE_DS_FN.finditer(content):
            try:
                args = json.loads(m.group(2))
            except Exception:
                args = {}
            out.append((m.group(1).strip(), args if isinstance(args, dict) else {}))
    return out


# ===================================================================== HTTP
def _headers(ep) -> dict:
    h = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/microagent",
        "X-Title": "microagent",
    }
    if ep.api_key:                       # internal servers usually run keyless
        h["Authorization"] = f"Bearer {ep.api_key}"
    return h


def _body(cfg, ep, messages, tools, stream) -> dict:
    body = {
        "model": ep.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "stream": stream,
    }
    if ep.reasoning and cfg.reasoning_effort:
        body["reasoning"] = {"effort": cfg.reasoning_effort}   # OpenRouter unified reasoning
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _finalize(content, reasoning, tool_map, usage, finish_reason) -> Completion:
    """Assemble the streamed pieces; recover markup tool calls if none were structured."""
    tcs = []
    for idx in sorted(tool_map):
        tc = tool_map[idx]
        if tc.get("name"):
            tcs.append(ToolCallSpec(id=tc.get("id") or f"call_{idx}",
                                    name=tc["name"], arguments=tc.get("arguments", "")))
    if not tcs:
        recovered = extract_text_tool_calls((content or "") + "\n" + (reasoning or ""))
        for i, (nm, args) in enumerate(recovered):
            tcs.append(ToolCallSpec(id=f"text_{i}", name=nm, arguments=json.dumps(args)))
        if recovered:
            finish_reason = "tool_calls"
    return Completion(content=content, reasoning=reasoning, tool_calls=tcs,
                      usage=usage, finish_reason=finish_reason or "stop")


def _merge_tool_delta(tool_map: dict, deltas: list):
    """Accumulate streamed tool_call deltas keyed by their index."""
    for d in deltas or []:
        idx = d.get("index", 0)
        slot = tool_map.setdefault(idx, {"id": None, "name": None, "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]


def _stream_once(cfg, messages, tools):
    """One streaming attempt. Yields StreamDelta; the final yielded item is a Completion."""
    ep = resolve_endpoint(cfg)
    url = ep.base_url.rstrip("/") + "/chat/completions"
    content, reasoning = [], []
    tool_map: dict = {}
    usage = None
    finish_reason = "stop"

    resp = requests.post(url, headers=_headers(ep), json=_body(cfg, ep, messages, tools, True),
                         stream=True, timeout=600)
    if resp.status_code != 200:
        detail = (resp.text or "")[:500]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")

    # SSE has no charset, so requests would default to ISO-8859-1 and mangle UTF-8
    # (box-drawing ├└─, arrows →, etc.). Force UTF-8 before decoding the stream.
    resp.encoding = "utf-8"
    for line in resp.iter_lines(decode_unicode=True):
        if not line or line.startswith(":"):        # keep-alive comment / blank
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            r = delta.get("reasoning") or delta.get("reasoning_content")
            if r:
                reasoning.append(r)
                yield StreamDelta("thinking", r)
            c = delta.get("content")
            if c:
                content.append(c)
                yield StreamDelta("text", c)
            if delta.get("tool_calls"):
                _merge_tool_delta(tool_map, delta["tool_calls"])
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    yield _finalize("".join(content), "".join(reasoning), tool_map, usage, finish_reason)


def _complete_nonstream(cfg, messages, tools) -> Completion:
    """Non-streaming fallback (single POST)."""
    ep = resolve_endpoint(cfg)
    url = ep.base_url.rstrip("/") + "/chat/completions"
    resp = requests.post(url, headers=_headers(ep), json=_body(cfg, ep, messages, tools, False),
                         timeout=600)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    tool_map = {}
    _merge_tool_delta(tool_map, [
        {"index": i, "id": tc.get("id"), "function": tc.get("function")}
        for i, tc in enumerate(msg.get("tool_calls") or [])
    ])
    finish = (data.get("choices") or [{}])[0].get("finish_reason", "stop")
    return _finalize(msg.get("content") or "",
                     msg.get("reasoning") or msg.get("reasoning_content") or "",
                     tool_map, data.get("usage"), finish)


def stream_complete(cfg, messages, tools=None, stream=True, tries=3):
    """Drive one model round-trip with retry/backoff.

    Yields StreamDelta(kind, text) as tokens arrive; the FINAL yielded item is a
    Completion (assembled content/reasoning/tool_calls/usage). On total failure the
    final item is a Completion with `.error` set (never raises).

    Transport follows the endpoint's `decode`: "json" => non-streaming, "sse" => streaming,
    "auto" => stream first and fall back to non-streaming (some hub ports 200 with no SSE body).
    """
    decode = resolve_endpoint(cfg, detect=False).decode
    use_stream = stream and decode != "json"
    last = None
    for attempt in range(tries):
        try:
            if not use_stream:
                yield _complete_nonstream(cfg, messages, tools)
                return
            final = None
            for item in _stream_once(cfg, messages, tools):
                if isinstance(item, Completion):
                    final = item
                else:
                    yield item
            # "auto": a 200 that streamed nothing usually means the port wants JSON, not SSE.
            if decode == "auto" and final is not None and not final.error \
                    and not final.content and not final.reasoning and not final.tool_calls:
                yield _complete_nonstream(cfg, messages, tools)
                return
            yield final if final is not None else Completion(error="empty stream")
            return
        except Exception as e:                      # transient -> back off and retry
            last = e
            time.sleep(2 * (attempt + 1))
    if use_stream and decode == "auto":             # streaming exhausted — try JSON once
        try:
            yield _complete_nonstream(cfg, messages, tools)
            return
        except Exception as e:
            last = e
    sys.stderr.write(f"[llm] model call failed after {tries} tries: {last}\n")
    yield Completion(error=str(last), finish_reason="error")
