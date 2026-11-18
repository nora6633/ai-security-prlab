"""Local GLiNER detector and Presidio text anonymizer."""
from contextlib import asynccontextmanager
from threading import Lock

import torch
from fastapi import FastAPI, HTTPException
from gliner import GLiNER
from pydantic import BaseModel, Field
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig, RecognizerResult

MODEL = None
ANONYMIZER = AnonymizerEngine()
MODEL_LOCK = Lock()
LABELS = ["密碼", "API key", "access token"]
THRESHOLD = 0.4
WINDOW_SIZE = 256
WINDOW_OVERLAP = 64


@asynccontextmanager
async def lifespan(_app):
    global MODEL
    torch.set_num_threads(2)
    MODEL = GLiNER.from_pretrained("urchade/gliner_multi-v2.1", local_files_only=True)
    yield


app = FastAPI(title="Lab output masking", lifespan=lifespan)


class MaskRequest(BaseModel):
    text: str = Field(max_length=30000)


@app.get("/health")
def health():
    if MODEL is None:
        raise HTTPException(status_code=503, detail="Model is loading")
    return {"status": "ok"}


@app.post("/mask")
def mask(request: MaskRequest):
    if MODEL is None:
        raise HTTPException(status_code=503, detail="Model is loading")
    text = request.text
    if not text:
        return {"text": "", "detected": False}
    findings = []
    with MODEL_LOCK:
        for offset in range(0, len(text), WINDOW_SIZE - WINDOW_OVERLAP):
            chunk = text[offset:offset + WINDOW_SIZE]
            for item in MODEL.predict_entities(chunk, LABELS, threshold=THRESHOLD):
                start, end = offset + item["start"], offset + item["end"]
                if not 0 <= start < end <= len(text):
                    raise HTTPException(status_code=500, detail="Invalid detector result")
                findings.append(RecognizerResult(entity_type="SECRET", start=start, end=end, score=float(item["score"])))
            if offset + WINDOW_SIZE >= len(text):
                break
    result = ANONYMIZER.anonymize(text=text, analyzer_results=findings,
                                 operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})})
    return {"text": result.text, "detected": bool(findings)}
