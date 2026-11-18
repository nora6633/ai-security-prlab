FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 HF_HOME=/opt/huggingface
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 && pip install --no-cache-dir -r requirements.txt
RUN python -c "from huggingface_hub import snapshot_download; from transformers import AutoTokenizer; snapshot_download('urchade/gliner_multi-v2.1', allow_patterns=['*.json','*.safetensors','*.model','*.txt']); AutoTokenizer.from_pretrained('microsoft/mdeberta-v3-base')"
ENV HF_HUB_OFFLINE=1
COPY main.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
