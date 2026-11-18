import json
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
from tools import TOOL_DEFINITIONS, run_tool
from guardrail import (
    GuardrailDecision,
    append_system_text,
    judge_settings,
    load_challenge,
    load_config,
    output_settings,
    refusal_for,
)


app = FastAPI(title="Lab Chatbot API", version="0.1.0")
turns = {}
turns_lock = Lock()
TURN_TTL_SECONDS = 3600
MAX_TOOL_ROUNDS = 5


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


async def run_model(client: httpx.AsyncClient, base_url: str, headers: dict[str, str], model: str,
                    messages: list[dict], references: dict[str, str]) -> tuple[str, str]:
    """Call the model, running requested tools until it gives a final answer."""
    for round_number in range(MAX_TOOL_ROUNDS + 1):
        payload = {"model": model, "messages": messages, "stream": False}
        # The last round omits tools so the model has to answer with what it has.
        if round_number < MAX_TOOL_ROUNDS:
            payload["tools"] = TOOL_DEFINITIONS
        response = await client.post(f"{base_url}/api/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls or round_number == MAX_TOOL_ROUNDS:
            return message.get("content") or "", data.get("model", model)
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            name = call["function"]["name"]
            arguments = call["function"].get("arguments") or "{}"
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            # Log the request only; tool results may contain lab secrets.
            log_arguments = arguments.replace("\r", " ").replace("\n", " ")
            print(f"tool_call: name={name} arguments={log_arguments}", flush=True)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": run_tool(name, arguments, references)})
    raise RuntimeError("Tool loop ended without a reply")


def blocked_response(reply: str, model: str, judge_config: dict, input_decision: GuardrailDecision,
                     output_decision: GuardrailDecision) -> dict:
    return {
        "reply": reply,
        "model": model,
        "turn_id": None,
        "guardrail": {"triggered": input_decision.triggered, "categories": list(input_decision.categories),
                      "action": "block" if input_decision.triggered else "allow"},
        "output_guardrail": {"triggered": output_decision.triggered, "categories": list(output_decision.categories),
                             "action": "block" if output_decision.triggered else "allow"},
        "challenge": {
            "model": judge_config.get("model") or judge_config.get("type"),
            "type": judge_config.get("type"),
            "skipped": True,
            "objectives": [],
        },
    }


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

    # Inspect each request synchronously before calling Open WebUI/the model.
    input_decision = inspect_content(user_message or "", guardrail_config)
    if input_decision.triggered:
        log_text = (user_message or "").replace("\r", " ").replace("\n", " ")
        print(f"guardrail_alert: categories={','.join(input_decision.categories)} user_message: {log_text}", flush=True)
        if user_message is not None:
            log_chat_turn(user_message, refusal)
        return blocked_response(refusal, model, judge_config, input_decision, allowed)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            reply, reply_model = await run_model(client, base_url, headers, model, messages, challenge["_references"])
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Open WebUI returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Open WebUI") from exc
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail="Unexpected response from Open WebUI") from exc

    output_decision = inspect_output(reply, guardrail_config, challenge["_references"])
    if output_decision.triggered:
        # The blocked reply is not logged so the secret does not reach Elasticsearch.
        print(f"output_guardrail_alert: categories={','.join(output_decision.categories)}", flush=True)
        if user_message is not None:
            log_chat_turn(user_message, refusal)
        return blocked_response(refusal, reply_model, judge_config, input_decision, output_decision)

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
        "model": reply_model,
        "turn_id": turn_id,
        "guardrail": {"triggered": False, "categories": [], "action": "allow"},
        "output_guardrail": {"triggered": False, "categories": [], "action": "allow"},
        "challenge": challenge_result,
    }
