# One image definition for all three services.
#
# The build context is the portfolio root rather than a single service directory,
# because every service imports `shared/`. A per-service Dockerfile cannot see a
# sibling directory, which is why they were consolidated here.
#
#   docker build --build-arg SERVICE=01-groundedcorp --build-arg PORT=8001 .

FROM python:3.12-slim

ARG SERVICE
ARG PORT=8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=${PORT} \
    PYTHONPATH=/platform:/platform/service

WORKDIR /platform

# Dependencies first so a code change does not invalidate the pip layer.
COPY ${SERVICE}/requirements.txt ./service/requirements.txt
RUN pip install --no-cache-dir -r ./service/requirements.txt

COPY shared ./shared
COPY ${SERVICE}/app ./service/app
COPY ${SERVICE}/data ./service/data

# Run unprivileged: the service writes only to its own data directory.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /platform
USER appuser

WORKDIR /platform/service
EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/health\").read()"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
