# hwpool/workspace – workspace VM (WS_MODE=desktop|power|alias)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends python3-requests sshpass && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
LABEL org.opencontainers.image.title="hwpool/workspace" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
