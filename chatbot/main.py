import os
import time
import uuid
from threading import Lock
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from evaluator import evaluate_response
from input_guardrail import inspect_content
from output_guardrail import inspect_output
from guardrail import (
    append_system_text,
    instruction_for,
    input_settings,
    judge_settings,
    load_challenge,
    load_config,
)


app = FastAPI(title="Lab Chatbot API", version="0.1.0")
turns = {}
turns_lock = Lock()
TURN_TTL_SECONDS = 3600


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)


class EvaluateRequest(BaseModel):
    turn_ids: list[str] = Field(min_length=1, max_length=1000)


@app.post("/evaluate")
async def evaluate(request: EvaluateRequest):
    now = time.monotonic()
    with turns_lock:
        saved_turns = [turns.get(turn_id) for turn_id in request.turn_ids]
    if any(not turn or now - turn[0] > TURN_TTL_SECONDS for turn in saved_turns):
        raise HTTPException(status_code=404, detail="部分聊天紀錄已過期，請重新開始對話後再檢查。")
    try:
        config = load_config(os.getenv("GUARDRAIL_CONFIG"))
        challenge = load_challenge(os.getenv("CHALLENGE_CONFIG"))
        headers = {"Content-Type": "application/json"}
        if os.getenv("OPENWEBUI_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['OPENWEBUI_API_KEY']}"
        responses = [turn[2] for turn in saved_turns]
        return await evaluate_response(os.getenv("BASEURL", "http://host.docker.internal:8080").rstrip("/"), headers, responses, challenge, judge_settings(config))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"challenge_evaluator_error: {type(exc).__name__}", flush=True)
        raise HTTPException(status_code=502, detail="AI Check 無法完成，請確認評分器與模型服務。") from exc


def log_chat_turn(user_message: str, assistant_message: str):
    # Keep both sides of the turn together on one searchable container-log line.
    user_text = user_message.replace("\r", "\\r").replace("\n", "\\n")
    assistant_text = assistant_message.replace("\r", "\\r").replace("\n", "\\n")
    print(f"user_message: {user_text} | assistant_response: {assistant_text}", flush=True)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest):
    base_url = os.getenv("BASEURL", "http://host.docker.internal:8080").rstrip("/")
    model = os.getenv("OPENWEBUI_MODEL", "")
    api_key = os.getenv("OPENWEBUI_API_KEY", "")
    if not model:
        raise HTTPException(status_code=503, detail="OPENWEBUI_MODEL is not configured")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": [m.model_dump() for m in request.messages], "stream": False}
    user_message = next((message.content for message in reversed(request.messages) if message.role == "user"), None)

    try:
        guardrail_config = load_config(os.getenv("GUARDRAIL_CONFIG"))
        challenge = load_challenge(os.getenv("CHALLENGE_CONFIG"))
        judge_config = judge_settings(guardrail_config)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Chatbot lab configuration is invalid") from exc

    # The lab-specific system prompt is sent on every turn and also serves as
    # the private grading target for this lab's objectives.
    append_system_text(payload["messages"], challenge["system_prompt"])

    # Inspect each request synchronously before calling Open WebUI/the model.
    user_context = "\n".join(message.content for message in request.messages if message.role == "user")
    guardrail = inspect_content(user_context, guardrail_config, user_message or "")
    if guardrail.triggered:
        categories = ",".join(guardrail.categories)
        log_text = (user_message or "").replace("\r", " ").replace("\n", " ")
        print(f"guardrail_alert: categories={categories} user_message: {log_text}", flush=True)
        input_action = input_settings(guardrail_config).get("action", "instruct")
        if input_action == "custom_response":
            reply = input_settings(guardrail_config).get("response", "I cannot provide that information.")
            if user_message is not None:
                log_chat_turn(user_message, reply)
            return {
                "reply": reply,
                "model": model,
                "guardrail": {"triggered": True, "categories": list(guardrail.categories), "action": input_action},
                "challenge": {
                    "model": judge_config.get("model") or judge_config.get("type"),
                    "type": judge_config.get("type"),
                    "skipped": True,
                    "objectives": [],
                },
            }
        if input_action == "instruct":
            # Preserve existing prompts, then append this lab's guardrail notice.
            append_system_text(payload["messages"], instruction_for(guardrail_config))

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{base_url}/api/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Open WebUI returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Open WebUI") from exc

    try:
        reply = data["choices"][0]["message"]["content"]
        try:
            output_guardrail = await inspect_output(reply, guardrail_config)
        except RuntimeError as exc:
            print("output_guardrail_error: scan failed", flush=True)
            raise HTTPException(status_code=503, detail="輸出遮罩檢查失敗，請稍後重試。") from exc
        reply = output_guardrail.content
        if user_message is not None:
            log_chat_turn(user_message, reply)
        if judge_config.get("type") in {"ai_judge", "ai_confidence"}:
            turn_id = uuid.uuid4().hex
            now = time.monotonic()
            with turns_lock:
                expired = [key for key, value in turns.items() if now - value[0] > TURN_TTL_SECONDS]
                for key in expired:
                    turns.pop(key, None)
                if len(turns) >= 1000:
                    turns.pop(next(iter(turns)))
                turns[turn_id] = (now, user_message or "", reply)
            challenge_result = {"type": judge_config.get("type"), "pending": True, "objectives": []}
        else:
            turn_id = None
            try:
                # Rule-based grading remains automatic.
                challenge_result = await evaluate_response(base_url, headers, reply, challenge, judge_config)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                print(f"challenge_evaluator_error: {type(exc).__name__}", flush=True)
                challenge_result = {"type": judge_config.get("type"), "error": "評分器目前無法完成評分。", "objectives": []}
        return {
            "reply": reply,
            "model": data.get("model", model),
            "turn_id": turn_id,
            "guardrail": {
                "triggered": guardrail.triggered,
                "categories": list(guardrail.categories),
                "action": input_settings(guardrail_config).get("action", "instruct") if guardrail.triggered else "allow",
            },
            "output_guardrail": {
                "triggered": bool(output_guardrail.categories),
                "categories": list(output_guardrail.categories),
                "action": "mask" if output_guardrail.categories else "allow",
            },
            "challenge": challenge_result,
        }
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Unexpected response from Open WebUI") from exc
