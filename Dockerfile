FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[docker,ui]" && useradd -u 10002 -m safeloop && mkdir /data && chown safeloop /data
USER 10002
EXPOSE 8000 8501
CMD ["uvicorn", "safeloop.api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
