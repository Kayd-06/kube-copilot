FROM python:3.12-slim

WORKDIR /src

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KNOWLEDGE_DIR=/knowledge/runbooks \
    PYTHONPATH=/src \
    ANOMALY_MODEL_PATH=/src/backend/models/isolation_forest.joblib

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/app /src/backend/app
COPY backend/models /src/backend/models
COPY ml /src/ml
COPY knowledge /knowledge

WORKDIR /src/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
