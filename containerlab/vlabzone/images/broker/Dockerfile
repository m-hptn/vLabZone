# hwpool/broker – booking/peer broker prototype (FastAPI + SQLite)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-fastapi python3-uvicorn python3-paramiko sqlite3 \
 && rm -rf /var/lib/apt/lists/*
COPY app /opt/broker/app
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
WORKDIR /opt/broker
LABEL org.opencontainers.image.title="hwpool/broker" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
