# hwpool/broker – booking/peer broker prototype (FastAPI + SQLite)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-fastapi python3-uvicorn python3-paramiko sqlite3 \
 && rm -rf /var/lib/apt/lists/*
COPY app /opt/broker/app
WORKDIR /opt/broker
LABEL org.opencontainers.image.title="hwpool/broker" org.opencontainers.image.version="${VLAB_VERSION}"
# Stage 0: plain HTTP health endpoint. TLS on 443 + API follow in stage 3.
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
