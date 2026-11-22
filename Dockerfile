FROM python:3.12-slim
WORKDIR /srv/backend
COPY backend/requirements.txt backend/requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt
COPY backend/ .
COPY frontend/ /srv/frontend/
COPY eval/ /srv/eval/
RUN useradd -m app && chown -R app /srv
USER app
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
