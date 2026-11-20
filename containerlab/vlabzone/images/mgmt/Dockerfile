# hwpool/mgmt – L2 bridge for the lab management net 10.198.0.0/24
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
LABEL org.opencontainers.image.title="hwpool/mgmt" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
