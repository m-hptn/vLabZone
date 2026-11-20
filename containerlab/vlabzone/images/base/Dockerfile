# Common base for all vLabZone lab nodes.
# Pinned by digest for reproducible builds on every host; bump deliberately.
ARG BASE_IMAGE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
FROM ${BASE_IMAGE}

ARG VLAB_VERSION=dev
# Optional apt mirror for restricted networks, e.g. http://mirror.example/debian
ARG APT_MIRROR=

ENV DEBIAN_FRONTEND=noninteractive \
    VLAB_VERSION=${VLAB_VERSION}
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s#http://deb.debian.org/debian#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources; \
    fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      iproute2 iputils-ping wireguard-tools nftables iptables tcpdump \
      openssh-server openssh-client socat ser2net iperf3 curl jq \
      python3 python3-yaml procps less tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY rootfs/ /

LABEL org.opencontainers.image.title="hwpool/base" \
      org.opencontainers.image.version="${VLAB_VERSION}"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
