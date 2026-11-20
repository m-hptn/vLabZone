# hwpool/edge – lab edge (EDGE_MODE=vrf|pbr)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
LABEL org.opencontainers.image.title="hwpool/edge" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
