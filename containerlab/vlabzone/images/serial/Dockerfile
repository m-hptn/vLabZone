# hwpool/serial – serial device server stand-in (Moxa NPort 5600, RFC 2217)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends python3-serial && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY nport-sim.py /usr/local/bin/nport-sim.py
LABEL org.opencontainers.image.title="hwpool/serial" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
