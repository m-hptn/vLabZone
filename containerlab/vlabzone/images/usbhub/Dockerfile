# hwpool/usbhub – USB hub stand-in (Digi AnywhereUSB 24 Plus, USB/IP protocol)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY usbhub-sim.py hub-ctl /usr/local/bin/
LABEL org.opencontainers.image.title="hwpool/usbhub" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
