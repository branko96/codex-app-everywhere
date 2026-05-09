#!/usr/bin/env python3
"""
Proxy entre Codex y LiteLLM.
Fixes aplicados al formato OpenAI Responses API antes de convertir:
  1. Filtra tool definitions con tipos no soportados (namespace, etc.)
  2. Elimina function_call items del input que no tienen function_call_output correspondiente
  3. Elimina function_call items que referencian herramientas namespace filtradas
  4. Mantiene coherencia del historial de conversación
"""
import json
import os
import aiohttp
from aiohttp import web

UPSTREAM = "http://localhost:4001"
LISTEN_PORT = 4000
DEBUG_LOG = os.path.expanduser("~/.codex/proxy_debug.jsonl")

SUPPORTED_TOOL_TYPES = {"function"}


def log_debug(path: str, data: dict) -> None:
    try:
        entry = {"path": path, "keys": list(data.keys())}
        if "input" in data and isinstance(data["input"], list):
            entry["input"] = data["input"][:30]  # primeros 30 items
        with open(DEBUG_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def filter_tools(data: dict) -> tuple[dict, set[str]]:
    """
    Filtra tool definitions con tipos no soportados.
    Retorna (data_modificado, set_de_nombres_filtrados).
    """
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
            # Capturar nombre para filtrar function_calls en input
            name = t.get("name") or (t.get("function") or {}).get("name")
            if name:
                filtered_names.add(name)

    if removed:
        removed_types = [t.get("type") for t in removed]
        print(f"[proxy] filtradas {len(removed)} tool def(s): {removed_types}", flush=True)

    if kept:
        data["tools"] = kept
    else:
        data.pop("tools", None)
        data.pop("tool_choice", None)
    return data, filtered_names


def fix_input(input_items: list, filtered_tool_names: set[str]) -> list:
    """
    Limpia y reordena el array input de la Responses API:
    - Elimina function_call de herramientas filtradas (+ sus outputs)
    - Elimina function_call sin function_call_output correspondiente
    - Elimina function_call_output huérfanos
    - REORDENA: mueve function_call_output inmediatamente después de su function_call
      (DeepSeek exige que el tool response siga al tool_call sin interrupciones)
    """
    if not isinstance(input_items, list):
        return input_items

    # Mapear call_id → (índice, item)
    fc_by_id: dict[str, tuple[int, dict]] = {}
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

    # IDs a eliminar
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

    # Índices de fco que van a ser reubicados (después de su fc correspondiente)
    # Detectar cuáles necesitan reordenarse: fco_idx > fc_idx + 1
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

    # Índices originales a saltar (fcos que serán insertados en otro lugar)
    skip_original_fco = {fco_by_id[cid][0] for cid in reorder_ids}

    result = []
    for i, item in enumerate(input_items):
        if not isinstance(item, dict):
            result.append(item)
            continue

        t = item.get("type")

        # Saltar fcos que serán reubicados
        if i in skip_original_fco:
            continue

        if t == "function_call":
            cid = item.get("call_id") or item.get("id")
            if cid in drop_ids:
                continue
            result.append(item)
            # Insertar fco inmediatamente después si necesita reordenarse
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


def fix_messages(messages: list) -> list:
    """
    Limpia el array messages de Chat Completions format:
    Elimina tool_calls de assistant sin tool response y responses huérfanas.
    """
    if not isinstance(messages, list):
        return messages

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

    if not orphan_calls and not orphan_responses:
        return messages

    if orphan_calls:
        print(f"[proxy] eliminando {len(orphan_calls)} tool_call(s) sin respuesta", flush=True)
    if orphan_responses:
        print(f"[proxy] eliminando {len(orphan_responses)} tool response(s) huérfana(s)", flush=True)

    result = []
    for m in messages:
        if not isinstance(m, dict):
            result.append(m)
            continue
        role = m.get("role")
        if role == "tool" and m.get("tool_call_id") in orphan_responses:
            continue
        if role == "assistant" and m.get("tool_calls"):
            valid = [tc for tc in m["tool_calls"]
                     if isinstance(tc, dict) and tc.get("id") not in orphan_calls]
            m = dict(m)
            if valid:
                m["tool_calls"] = valid
            else:
                m.pop("tool_calls", None)
                m.pop("tool_choice", None)
        result.append(m)
    return result


def fix_request(data: dict, path: str) -> dict:
    if path in ("/responses", "/v1/responses"):
        log_debug(path, data)

    data, filtered_names = filter_tools(data)

    if "input" in data and isinstance(data["input"], list):
        data["input"] = fix_input(data["input"], filtered_names)

    if "messages" in data and isinstance(data["messages"], list):
        data["messages"] = fix_messages(data["messages"])

    return data


async def proxy(request: web.Request) -> web.StreamResponse | web.Response:
    body = await request.read()

    content_type = request.headers.get("Content-Type", "")
    if "application/json" in content_type and body and request.method != "GET":
        try:
            data = json.loads(body)
            data = fix_request(data, request.path)
            body = json.dumps(data).encode()
        except (json.JSONDecodeError, Exception) as e:
            print(f"[proxy] error procesando body: {e}", flush=True)

    upstream_url = f"{UPSTREAM}{request.path_qs}"
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

            if "text/event-stream" in upstream_ct:
                response = web.StreamResponse(
                    status=upstream.status,
                    reason=upstream.reason,
                )
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
                    headers={
                        k: v for k, v in upstream.headers.items()
                        if k.lower() not in ("transfer-encoding", "content-encoding")
                    },
                    body=content,
                )


app = web.Application(client_max_size=50 * 1024 * 1024)
app.router.add_route("*", "/{path_info:.*}", proxy)

if __name__ == "__main__":
    print(f"[proxy] Listening on :{LISTEN_PORT} → upstream {UPSTREAM}", flush=True)
    web.run_app(app, host="0.0.0.0", port=LISTEN_PORT, print=None)
