FROM zricethezav/gitleaks:v8.28.0 AS gitleaks
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py guardrail.py input_guardrail.py output_guardrail.py evaluator.py guardrail-default.json challenge-default.json ./
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
