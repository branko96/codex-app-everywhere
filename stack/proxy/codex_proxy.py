#!/usr/bin/env python3
"""
Proxy entre Codex y LiteLLM.

Features:
  - Model routing: traduce modelos de Codex (gpt-4o, etc.) a modelos DeepSeek
  - Filtra tool definitions con tipos no soportados (namespace, etc.)
  - Elimina function_call items del input sin function_call_output correspondiente
  - Reordena function_call_output inmediatamente después de su function_call
  - Convierte Responses API → Chat Completions (agrega reasoning_content: "" en
    todos los mensajes de assistant para satisfacer el thinking mode de DeepSeek)
  - Convierte respuestas Chat Completions → Responses API (streaming y no-streaming)
  - Intercepta /v1/models para devolver modelos disponibles desde el mapping
  - Health endpoint
"""
import json
import os
import uuid
import aiohttp
from aiohttp import web

UPSTREAM = os.environ.get("LITELLM_URL", "http://localhost:4001")
LISTEN_PORT = int(os.environ.get("PROXY_PORT", "4000"))
DEBUG_LOG = os.path.expanduser("~/.codex/proxy_debug.jsonl")

SUPPORTED_TOOL_TYPES = {"function"}

# ── Model mapping ─────────────────────────────────────────────────────
MODEL_MAP = {
    # Rápido → flash
    "gpt-4o-mini":   "deepseek-v4-flash",
    "gpt-5.4-mini":  "deepseek-v4-flash",
    "gpt-5.5-mini":  "deepseek-v4-flash",
    # Capaz → pro
    "gpt-4o":        "deepseek-v4-pro",
    "o3":            "deepseek-v4-pro",
    "o3-mini":       "deepseek-v4-pro",
    "o1":            "deepseek-v4-pro",
    "o1-mini":       "deepseek-v4-pro",
    "gpt-5":         "deepseek-v4-pro",
    "gpt-5.4":       "deepseek-v4-pro",
    "gpt-5.5":       "deepseek-v4-pro",
    # DeepSeek names directos
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro":   "deepseek-v4-pro",
}


def resolve_model(model: str) -> str:
    resolved = MODEL_MAP.get(model, model)
    if resolved != model:
        print(f"[proxy] model route: '{model}' → '{resolved}'", flush=True)
    return resolved


# ── Modelos expuestos via GET /v1/models ──────────────────────────────
EXPOSED_MODELS = [
    {"id": "deepseek-v4-flash", "object": "model", "created": 1747000000, "owned_by": "codex-proxy", "description": "DeepSeek V4 Flash — rápido, tool calls ✅"},
    {"id": "deepseek-v4-pro",   "object": "model", "created": 1747000001, "owned_by": "codex-proxy", "description": "DeepSeek V4 Pro — capaz, tool calls ✅"},
    {"id": "gpt-4o-mini",       "object": "model", "created": 1747000002, "owned_by": "codex-proxy", "description": "→ deepseek-v4-flash"},
    {"id": "gpt-4o",            "object": "model", "created": 1747000003, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
    {"id": "gpt-5.4-mini",      "object": "model", "created": 1747000004, "owned_by": "codex-proxy", "description": "→ deepseek-v4-flash"},
    {"id": "gpt-5",             "object": "model", "created": 1747000005, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
    {"id": "gpt-5.4",           "object": "model", "created": 1747000006, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
    {"id": "gpt-5.5",           "object": "model", "created": 1747000007, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
    {"id": "gpt-5.5-mini",      "object": "model", "created": 1747000008, "owned_by": "codex-proxy", "description": "→ deepseek-v4-flash"},
    {"id": "o3",                "object": "model", "created": 1747000009, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
    {"id": "o1",                "object": "model", "created": 1747000010, "owned_by": "codex-proxy", "description": "→ deepseek-v4-pro"},
]


# ── Helpers ───────────────────────────────────────────────────────────

def log_debug(path: str, data: dict) -> None:
    try:
        entry = {
            "path": path,
            "keys": list(data.keys()),
            "model": data.get("model"),
            "top_level": {k: v for k, v in data.items() if k not in ("input", "messages", "tools")},
        }
        if "input" in data and isinstance(data["input"], list):
            entry["input"] = data["input"][:30]
        with open(DEBUG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def filter_tools(data: dict) -> tuple[dict, set[str]]:
    """Filtra tool definitions con tipos no soportados."""
    tools = data.get("tools")
    filtered_names: set[str] = set()
    if not isinstance(tools, list):
        return data, filtered_names

    kept, removed = [], []
    for t in tools:
        if t.get("type") in SUPPORTED_TOOL_TYPES:
            kept.append(t)
        else:
            removed.append(t)
            name = t.get("name") or (t.get("function") or {}).get("name")
            if name:
                filtered_names.add(name)

    if removed:
        print(f"[proxy] filtradas {len(removed)} tool def(s): {[t.get('type') for t in removed]}", flush=True)

    if kept:
        data["tools"] = kept
    else:
        data.pop("tools", None)
        data.pop("tool_choice", None)
    return data, filtered_names


def fix_input(input_items: list, filtered_tool_names: set[str]) -> list:
    """Limpia y reordena el array input de la Responses API."""
    if not isinstance(input_items, list):
        return input_items

    fc_by_id:  dict[str, tuple[int, dict]] = {}
    fco_by_id: dict[str, tuple[int, dict]] = {}

    for i, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "function_call":
            cid = item.get("call_id") or item.get("id")
            if cid:
                fc_by_id[cid] = (i, item)
        elif t == "function_call_output":
            cid = item.get("call_id")
            if cid:
                fco_by_id[cid] = (i, item)

    drop_ids: set[str] = set()
    for cid, (_, fc) in fc_by_id.items():
        name = fc.get("name", "")
        if name in filtered_tool_names:
            drop_ids.add(cid)
            print(f"[proxy] eliminando function_call de tool filtrada: {name}", flush=True)
        elif cid not in fco_by_id:
            drop_ids.add(cid)
            print(f"[proxy] eliminando function_call sin respuesta: {name} (id={cid})", flush=True)

    orphan_outputs: set[str] = set(fco_by_id.keys()) - set(fc_by_id.keys())
    if orphan_outputs:
        print(f"[proxy] eliminando {len(orphan_outputs)} function_call_output huérfano(s)", flush=True)

    reorder_ids: set[str] = set()
    for cid, (fc_idx, _) in fc_by_id.items():
        if cid in drop_ids:
            continue
        if cid in fco_by_id:
            fco_idx = fco_by_id[cid][0]
            if fco_idx > fc_idx + 1:
                reorder_ids.add(cid)
                print(f"[proxy] reordenando function_call_output de '{fc_by_id[cid][1].get('name')}' "
                      f"(estaba en pos {fco_idx}, debería estar después de {fc_idx})", flush=True)

    skip_original_fco = {fco_by_id[cid][0] for cid in reorder_ids}

    result = []
    for i, item in enumerate(input_items):
        if not isinstance(item, dict):
            result.append(item)
            continue
        t = item.get("type")

        if i in skip_original_fco:
            continue

        if t == "function_call":
            cid = item.get("call_id") or item.get("id")
            if cid in drop_ids:
                continue
            result.append(item)
            if cid in reorder_ids:
                result.append(fco_by_id[cid][1])
        elif t == "function_call_output":
            cid = item.get("call_id")
            if cid in drop_ids or cid in orphan_outputs:
                continue
            result.append(item)
        else:
            result.append(item)

    return result


# ── Responses API ↔ Chat Completions ─────────────────────────────────

def _text_from_content(content) -> str:
    """Extrae texto plano del campo content de un item de la Responses API."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text")
        )
    return str(content) if content else ""


def _tool_to_cc(tool: dict) -> dict:
    """Convierte una tool definition de Responses API a Chat Completions format."""
    if "function" in tool:
        return tool  # ya está en CC format
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {}),
        },
    }


def responses_to_chat(data: dict) -> dict:
    """Convierte un request de Responses API a Chat Completions.

    Agrega reasoning_content: "" en TODOS los mensajes de assistant para
    satisfacer el requisito de DeepSeek thinking mode en conversaciones
    multi-turn sin necesitar estado server-side.
    """
    messages: list[dict] = []

    # System prompt (campo top-level en Responses API)
    if "system" in data:
        text = _text_from_content(data["system"])
        if text:
            messages.append({"role": "system", "content": text})

    for item in data.get("input", []):
        if not isinstance(item, dict):
            continue
        t = item.get("type")

        if t == "message":
            role = item.get("role", "user")
            text = _text_from_content(item.get("content", []))
            msg: dict = {"role": role, "content": text}
            if role == "assistant":
                # Siempre incluir reasoning_content (vacío si no hay)
                # para satisfacer el thinking mode de DeepSeek.
                msg["reasoning_content"] = item.get("reasoning_content", "")
            messages.append(msg)

        elif t == "function_call":
            call_id = item.get("call_id") or item.get("id", "")
            args = item.get("arguments", "{}")
            if isinstance(args, dict):
                args = json.dumps(args)
            tc = {
                "id": call_id,
                "type": "function",
                "function": {"name": item.get("name", ""), "arguments": args},
            }
            # Merge en el mensaje assistant anterior si ya es de tool_calls
            if messages and messages[-1].get("role") == "assistant" and messages[-1].get("content") is None:
                messages[-1].setdefault("tool_calls", []).append(tc)
            else:
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "",
                    "tool_calls": [tc],
                })

        elif t == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id", ""),
                "content": str(item.get("output", "")),
            })

    chat: dict = {
        "model": data.get("model", ""),
        "messages": messages,
        "stream": data.get("stream", False),
    }

    tools = data.get("tools", [])
    if tools:
        chat["tools"] = [_tool_to_cc(t) for t in tools]
    if "tool_choice" in data:
        chat["tool_choice"] = data["tool_choice"]

    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if key in data:
            chat[key] = data[key]
    if "max_tokens" in data:
        chat["max_tokens"] = data["max_tokens"]
    elif "max_output_tokens" in data:
        chat["max_tokens"] = data["max_output_tokens"]

    return chat


def chat_to_response(cc: dict) -> dict:
    """Convierte una respuesta Chat Completions a Responses API format."""
    choices = cc.get("choices", [])
    if not choices:
        return cc
    choice = choices[0]
    message = choice.get("message", {})
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls") or []
    finish_reason = choice.get("finish_reason", "stop")

    cc_id = cc.get("id", uuid.uuid4().hex)
    resp_id = f"resp_{cc_id}"
    item_id = f"msg_{cc_id}"

    output: list[dict] = []
    for tc in tool_calls:
        output.append({
            "type": "function_call",
            "id": tc.get("id"),
            "call_id": tc.get("id"),
            "name": tc["function"]["name"],
            "arguments": tc["function"]["arguments"],
            "status": "completed",
        })
    if content:
        output.append({
            "type": "message",
            "id": item_id,
            "role": "assistant",
            "content": [{"type": "output_text", "text": content, "annotations": []}],
            "status": "completed",
        })

    stop_reason = "end_turn"
    if finish_reason == "tool_calls":
        stop_reason = "tool_calls"
    elif finish_reason == "length":
        stop_reason = "max_tokens"

    return {
        "id": resp_id,
        "object": "response",
        "created_at": cc.get("created", 0),
        "model": cc.get("model", ""),
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "usage": cc.get("usage"),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def stream_responses(upstream, resp_id: str):
    """Convierte un SSE stream de Chat Completions a Responses API SSE."""
    item_id = f"msg_{resp_id}"
    fc_items: dict[int, dict] = {}
    text_started = False
    full_text: list[str] = []
    finish_reason = "stop"
    usage = None
    model_name = ""

    yield _sse("response.created", {
        "type": "response.created",
        "response": {"id": resp_id, "object": "response", "status": "in_progress", "output": []},
    })

    buffer = b""
    async for raw in upstream.content.iter_any():
        buffer += raw
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line or not line.startswith(b"data: "):
                continue
            raw_data = line[6:].decode("utf-8", errors="ignore").strip()
            if raw_data == "[DONE]":
                break
            try:
                chunk = json.loads(raw_data)
            except json.JSONDecodeError:
                continue

            if "model" in chunk:
                model_name = chunk["model"]
            if chunk.get("usage"):
                usage = chunk["usage"]

            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            fr = choices[0].get("finish_reason")
            if fr:
                finish_reason = fr

            # ── Text delta ────────────────────────────────────────────
            text = delta.get("content") or ""
            if text:
                if not text_started:
                    text_started = True
                    yield _sse("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {"id": item_id, "type": "message", "role": "assistant",
                                 "content": [], "status": "in_progress"},
                    })
                    yield _sse("response.content_part.added", {
                        "type": "response.content_part.added",
                        "item_id": item_id, "output_index": 0, "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    })
                full_text.append(text)
                yield _sse("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "item_id": item_id, "output_index": 0, "content_index": 0,
                    "delta": text,
                })

            # ── Tool call deltas ──────────────────────────────────────
            for tc_delta in (delta.get("tool_calls") or []):
                idx = tc_delta.get("index", 0)
                out_idx = idx  # tool calls start at output_index 0 (no text in tool-call turns)
                if idx not in fc_items:
                    fc_id = tc_delta.get("id") or f"fc_{uuid.uuid4().hex[:8]}"
                    fc_name = (tc_delta.get("function") or {}).get("name", "")
                    fc_item = {
                        "type": "function_call",
                        "id": fc_id, "call_id": fc_id,
                        "name": fc_name, "arguments": "",
                        "status": "in_progress",
                    }
                    fc_items[idx] = fc_item
                    yield _sse("response.output_item.added", {
                        "type": "response.output_item.added",
                        "output_index": out_idx, "item": fc_item,
                    })

                fc = fc_items[idx]
                fn = tc_delta.get("function") or {}
                if fn.get("name"):
                    fc["name"] = fn["name"]
                args_delta = fn.get("arguments", "")
                if args_delta:
                    fc["arguments"] += args_delta
                    yield _sse("response.function_call_arguments.delta", {
                        "type": "response.function_call_arguments.delta",
                        "item_id": fc["id"], "output_index": out_idx,
                        "delta": args_delta,
                    })

    # ── Closing events ────────────────────────────────────────────────
    full = "".join(full_text)
    output: list[dict] = []

    if text_started:
        yield _sse("response.output_text.done", {
            "type": "response.output_text.done",
            "item_id": item_id, "output_index": 0, "content_index": 0, "text": full,
        })
        yield _sse("response.content_part.done", {
            "type": "response.content_part.done",
            "item_id": item_id, "output_index": 0, "content_index": 0,
            "part": {"type": "output_text", "text": full, "annotations": []},
        })
        done_item = {
            "id": item_id, "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": full, "annotations": []}],
            "status": "completed",
        }
        output.append(done_item)
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done", "output_index": 0, "item": done_item,
        })

    for idx, fc in sorted(fc_items.items()):
        fc["status"] = "completed"
        output.append(fc)
        out_idx = idx
        yield _sse("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "item_id": fc["id"], "output_index": out_idx,
            "arguments": fc["arguments"],
        })
        yield _sse("response.output_item.done", {
            "type": "response.output_item.done", "output_index": out_idx, "item": fc,
        })

    stop_reason = "end_turn"
    if finish_reason == "tool_calls":
        stop_reason = "tool_calls"
    elif finish_reason == "length":
        stop_reason = "max_tokens"

    # Convertir usage de CC format → Responses API format
    u = usage or {}
    responses_usage = {
        "input_tokens":  u.get("prompt_tokens", 0),
        "output_tokens": u.get("completion_tokens", 0),
        "total_tokens":  u.get("total_tokens", 0),
    }

    yield _sse("response.completed", {
        "type": "response.completed",
        "response": {
            "id": resp_id, "object": "response", "created_at": 0,
            "model": model_name, "output": output,
            "status": "completed", "usage": responses_usage, "error": None,
            "incomplete_details": None,
        },
    })
    yield b"data: [DONE]\n\n"


# ── Handlers ─────────────────────────────────────────────────────────

async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "upstream": UPSTREAM, "models": list(MODEL_MAP.keys())})


async def handle_models(request: web.Request) -> web.Response:
    return web.json_response({"object": "list", "data": EXPOSED_MODELS})


async def proxy(request: web.Request) -> web.StreamResponse | web.Response:
    path = request.path

    if path in ("/", "/health") and request.method == "GET":
        return await handle_health(request)
    if path in ("/v1/models", "/models") and request.method == "GET":
        return await handle_models(request)

    body = await request.read()
    is_responses = path in ("/responses", "/v1/responses") and request.method == "POST"

    data = None
    chat_data = None
    content_type = request.headers.get("Content-Type", "")

    if "application/json" in content_type and body and request.method != "GET":
        try:
            data = json.loads(body)

            # Model routing + strippear response_format
            if "model" in data:
                data["model"] = resolve_model(data["model"])
            if "response_format" in data:
                rf = data.pop("response_format")
                rf_type = rf.get("type") if isinstance(rf, dict) else rf
                print(f"[proxy] eliminando response_format: {rf_type}", flush=True)

            data, filtered_names = filter_tools(data)

            if is_responses:
                log_debug(path, data)
                data["input"] = fix_input(data.get("input", []), filtered_names)
                # Convertir a Chat Completions (agrega reasoning_content: "" en assistant msgs)
                chat_data = responses_to_chat(data)
                body = json.dumps(chat_data).encode()
            else:
                if "input" in data and isinstance(data["input"], list):
                    data["input"] = fix_input(data["input"], filtered_names)
                if "messages" in data and isinstance(data["messages"], list):
                    data["messages"] = _fix_messages(data["messages"])
                body = json.dumps(data).encode()

        except Exception as e:
            print(f"[proxy] error procesando body: {e}", flush=True)
            is_responses = False

    upstream_url = (
        f"{UPSTREAM}/v1/chat/completions" if is_responses
        else f"{UPSTREAM}{request.path_qs}"
    )
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    forward_headers["Content-Length"] = str(len(body))

    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=request.method,
            url=upstream_url,
            headers=forward_headers,
            data=body,
        ) as upstream:
            upstream_ct = upstream.headers.get("Content-Type", "")

            if is_responses and chat_data is not None:
                is_streaming = chat_data.get("stream", False)

                if is_streaming:
                    resp_id = f"resp_{uuid.uuid4().hex}"
                    response = web.StreamResponse(status=upstream.status)
                    response.headers["Content-Type"] = "text/event-stream"
                    response.headers["Cache-Control"] = "no-cache"
                    response.headers["X-Accel-Buffering"] = "no"
                    await response.prepare(request)
                    async for chunk in stream_responses(upstream, resp_id):
                        await response.write(chunk)
                    await response.write_eof()
                    return response
                else:
                    content = await upstream.read()
                    if upstream.status == 200:
                        try:
                            resp = chat_to_response(json.loads(content))
                            return web.json_response(resp)
                        except Exception as e:
                            print(f"[proxy] error convirtiendo respuesta: {e}", flush=True)
                    return web.Response(
                        status=upstream.status,
                        headers={k: v for k, v in upstream.headers.items()
                                 if k.lower() not in ("transfer-encoding", "content-encoding")},
                        body=content,
                    )

            # Pass-through estándar (no-Responses API)
            if "text/event-stream" in upstream_ct:
                response = web.StreamResponse(status=upstream.status, reason=upstream.reason)
                response.headers.update({
                    k: v for k, v in upstream.headers.items()
                    if k.lower() not in ("transfer-encoding", "content-encoding")
                })
                await response.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
                return response
            else:
                content = await upstream.read()
                return web.Response(
                    status=upstream.status,
                    reason=upstream.reason,
                    headers={k: v for k, v in upstream.headers.items()
                             if k.lower() not in ("transfer-encoding", "content-encoding")},
                    body=content,
                )


def _fix_messages(messages: list) -> list:
    """Limpia tool_calls sin response y viceversa. Inyecta reasoning_content: ""."""
    if not isinstance(messages, list):
        return messages

    has_reasoning = any(
        isinstance(m, dict) and m.get("role") == "assistant" and "reasoning_content" in m
        for m in messages
    )
    responded: set[str] = {
        m.get("tool_call_id") for m in messages
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id")
    }
    requested: set[str] = {
        tc.get("id") for m in messages if isinstance(m, dict) and m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("id")
    }
    orphan_calls = requested - responded
    orphan_responses = responded - requested

    result = []
    for m in messages:
        if not isinstance(m, dict):
            result.append(m)
            continue
        role = m.get("role")
        if role == "tool" and m.get("tool_call_id") in orphan_responses:
            continue
        if role == "assistant":
            if m.get("tool_calls"):
                valid = [tc for tc in m["tool_calls"]
                         if isinstance(tc, dict) and tc.get("id") not in orphan_calls]
                m = dict(m)
                m["tool_calls"] = valid if valid else None
                if not valid:
                    m.pop("tool_calls", None)
                    m.pop("tool_choice", None)
            if has_reasoning and "reasoning_content" not in m:
                m = dict(m)
                m["reasoning_content"] = ""
        result.append(m)
    return result


app = web.Application(client_max_size=50 * 1024 * 1024)
app.router.add_route("*", "/{path_info:.*}", proxy)

if __name__ == "__main__":
    print(f"[proxy] Listening on :{LISTEN_PORT} → upstream {UPSTREAM}", flush=True)
    print(f"[proxy] Model routes: {len(MODEL_MAP)} entries", flush=True)
    for k, v in MODEL_MAP.items():
        print(f"        '{k}' → '{v}'", flush=True)
    web.run_app(app, host="0.0.0.0", port=LISTEN_PORT, print=None)
