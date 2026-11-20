# hwpool/edge – Linux lab edge (EDGE_MODE=vrf|pbr|hybrid)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends conntrack && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY edge-vrf.sh edge-pbr.sh edge-hybrid.sh /usr/local/lib/vlab/
COPY edge-ctl /usr/local/bin/edge-ctl
LABEL org.opencontainers.image.title="hwpool/edge" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
