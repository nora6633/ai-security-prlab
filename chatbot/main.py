import asyncio
import json
import os
from typing import Any, Callable, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from evaluator import evaluate_response
from input_guardrail import inspect_content
from output_guardrail import inspect_output
from tools import run_tool, tool_definitions
from guardrail import (
    GuardrailDecision,
    append_system_text,
    guardrail_settings,
    input_settings,
    judge_settings,
    load_challenge,
    load_config,
    output_settings,
    refusal_for,
)


app = FastAPI(title="Lab Chatbot API", version="0.1.0")
MAX_TOOL_ROUNDS = 5

# Receives each step of a chat turn for the practice-mode sequence diagram.
Emit = Callable[[dict[str, Any]], None]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


def log_chat_turn(user_message: str, assistant_message: str):
    # Keep both sides of the turn together on one searchable container-log line.
    user_text = user_message.replace("\r", "\\r").replace("\n", "\\n")
    assistant_text = assistant_message.replace("\r", "\\r").replace("\n", "\\n")
    print(f"user_message: {user_text} | assistant_response: {assistant_text}", flush=True)


def step(source: str, target: str, label: str, status: str = "done", detail: str | None = None) -> dict[str, Any]:
    """One arrow between the user, input, chatbot, llm, tool and output nodes."""
    return {"type": "step", "from": source, "to": target, "label": label, "status": status, "detail": detail}


def ignore_step(event: dict[str, Any]) -> None:
    pass


async def run_model(client: httpx.AsyncClient, base_url: str, headers: dict[str, str], model: str,
                    messages: list[dict], references: dict[str, str], emit: Emit = ignore_step) -> tuple[str, str]:
    """Call the model, running requested tools until it gives a final answer."""
    tools = tool_definitions()
    for round_number in range(MAX_TOOL_ROUNDS + 1):
        payload = {"model": model, "messages": messages, "stream": False}
        # The last round omits tools so the model has to answer with what it has.
        if round_number < MAX_TOOL_ROUNDS:
            payload["tools"] = tools
        emit(step("chatbot", "llm", f"第 {round_number + 1} 輪：送出對話" + ("與工具定義" if "tools" in payload else "（不再提供工具）")))
        response = await client.post(f"{base_url}/api/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls or round_number == MAX_TOOL_ROUNDS:
            reply = message.get("content") or ""
            emit(step("llm", "chatbot", "產生回覆", detail=reply))
            return reply, data.get("model", model)
        emit(step("llm", "chatbot", "要求呼叫工具：" + ", ".join(call["function"]["name"] for call in tool_calls)))
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            arguments = call["function"].get("arguments") or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            # Log the request only; tool results may contain lab secrets.
            log_arguments = arguments.replace("\r", " ").replace("\n", " ")
            print(f"tool_call: name={name} arguments={log_arguments}", flush=True)
            emit(step("chatbot", "tool", f"{name}({log_arguments})"))
            result = run_tool(name, arguments, references)
            failed = "error" in json.loads(result)
            emit(step("tool", "chatbot", f"{name} 回傳" + ("錯誤" if failed else "結果"), "blocked" if failed else "done", result))
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
    raise RuntimeError("Tool loop ended without a reply")


def blocked_response(reply: str, model: str, judge_config: dict, input_decision: GuardrailDecision,
                     output_decision: GuardrailDecision) -> dict:
    return {
        "reply": reply,
        "model": model,
        "guardrail": {"triggered": input_decision.triggered, "categories": list(input_decision.categories),
                      "action": "block" if input_decision.triggered else "allow"},
        "output_guardrail": {"triggered": output_decision.triggered, "categories": list(output_decision.categories),
                             "action": "block" if output_decision.triggered else "allow"},
        "challenge": {"type": judge_config.get("type"), "skipped": True, "objectives": []},
    }


def guard_enabled(config: dict[str, Any], settings: dict[str, Any], default: bool) -> bool:
    return guardrail_settings(config).get("enabled", True) and settings.get("enabled", default)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/guardrail")
async def guardrail_state():
    """Report the guardrail switches this pod is using right now, after ConfigMap updates land."""
    try:
        config = load_config(os.getenv("GUARDRAIL_CONFIG"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Chatbot lab configuration is invalid") from exc
    return {
        "input": bool(guard_enabled(config, input_settings(config), True)),
        "output": bool(guard_enabled(config, output_settings(config), False)),
    }


async def process_chat(request: ChatRequest, emit: Emit = ignore_step) -> dict:
    base_url = os.getenv("BASEURL", "http://host.docker.internal:8080").rstrip("/")
    model = os.getenv("OPENWEBUI_MODEL", "")
    api_key = os.getenv("OPENWEBUI_API_KEY", "")
    if not model:
        raise HTTPException(status_code=503, detail="OPENWEBUI_MODEL is not configured")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    messages = [m.model_dump() for m in request.messages]
    user_message = next((message.content for message in reversed(request.messages) if message.role == "user"), None)

    try:
        guardrail_config = load_config(os.getenv("GUARDRAIL_CONFIG"))
        challenge = load_challenge(os.getenv("CHALLENGE_CONFIG"))
        judge_config = judge_settings(guardrail_config)
        if any(name not in challenge["_references"] for name in output_settings(guardrail_config).get("references", [])):
            raise ValueError("Output guardrail references an unknown challenge value")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Chatbot lab configuration is invalid") from exc
    refusal = refusal_for(guardrail_config)
    allowed = GuardrailDecision(triggered=False)

    # The lab-specific system prompt is sent on every turn.
    append_system_text(messages, challenge["system_prompt"])
    emit(step("user", "input", "送出訊息", detail=user_message or ""))

    # Inspect each request synchronously before calling Open WebUI/the model.
    input_decision = inspect_content(user_message or "", guardrail_config)
    if input_decision.triggered:
        log_text = (user_message or "").replace("\r", " ").replace("\n", " ")
        print(f"guardrail_alert: categories={','.join(input_decision.categories)} user_message: {log_text}", flush=True)
        if user_message is not None:
            log_chat_turn(user_message, refusal)
        emit(step("input", "user", "命中偵測規則，拒答", "blocked", "命中關鍵字：" + ", ".join(input_decision.matches)))
        return blocked_response(refusal, model, judge_config, input_decision, allowed)
    if guard_enabled(guardrail_config, input_settings(guardrail_config), True):
        emit(step("input", "chatbot", "未命中關鍵字，放行"))
    else:
        emit(step("input", "chatbot", "輸入護欄已關閉，直接放行", "skipped"))

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            reply, reply_model = await run_model(client, base_url, headers, model, messages, challenge["_references"], emit)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Open WebUI returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Open WebUI") from exc
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail="Unexpected response from Open WebUI") from exc

    emit(step("chatbot", "output", "檢查回覆"))
    output_decision = inspect_output(reply, guardrail_config, challenge["_references"])
    if output_decision.triggered:
        # The blocked reply is not logged so the secret does not reach Elasticsearch.
        print(f"output_guardrail_alert: categories={','.join(output_decision.categories)}", flush=True)
        if user_message is not None:
            log_chat_turn(user_message, refusal)
        emit(step("output", "user", "回覆含帳號密碼，拒答", "blocked", "命中：" + ", ".join(output_decision.categories)))
        return blocked_response(refusal, reply_model, judge_config, input_decision, output_decision)
    if guard_enabled(guardrail_config, output_settings(guardrail_config), False):
        emit(step("output", "user", "未含帳號密碼原文，送出回覆"))
    else:
        emit(step("output", "user", "輸出護欄已關閉，直接送出", "skipped"))

    if user_message is not None:
        log_chat_turn(user_message, reply)
    challenge_result = evaluate_response(reply, challenge, judge_config)
    return {
        "reply": reply,
        "model": reply_model,
        "guardrail": {"triggered": False, "categories": [], "action": "allow"},
        "output_guardrail": {"triggered": False, "categories": [], "action": "allow"},
        "challenge": challenge_result,
    }


@app.post("/chat")
async def chat(request: ChatRequest):
    return await process_chat(request)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Stream each step of the turn as NDJSON, ending with a result or error line."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def line(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    async def events():
        task = asyncio.create_task(process_chat(request, queue.put_nowait))
        try:
            while True:
                next_event = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({next_event, task}, return_when=asyncio.FIRST_COMPLETED)
                if next_event in done:
                    yield line(next_event.result())
                    continue
                next_event.cancel()
                while not queue.empty():
                    yield line(queue.get_nowait())
                break
            try:
                yield line({"type": "result", "data": task.result()})
            except HTTPException as exc:
                yield line({"type": "error", "detail": exc.detail})
            except Exception as exc:
                print(f"chat_stream_error: {type(exc).__name__}", flush=True)
                yield line({"type": "error", "detail": "Chatbot failed to answer"})
        finally:
            # Stop the model call when the browser leaves mid-turn.
            if not task.done():
                task.cancel()

    return StreamingResponse(events(), media_type="application/x-ndjson")
