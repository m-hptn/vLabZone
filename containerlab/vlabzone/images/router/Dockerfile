# hwpool/router – corp-fw, lab-sw, gw-N (ROLE selects the behaviour)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY roles/ /usr/local/lib/vlab/roles/
COPY sw-port /usr/local/bin/sw-port
LABEL org.opencontainers.image.title="hwpool/router" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
