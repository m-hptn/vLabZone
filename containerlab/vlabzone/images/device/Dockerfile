# hwpool/device – simulated lab device (SETUP, NAME, IP, GW)
ARG VLAB_VERSION=dev
FROM hwpool/base:${VLAB_VERSION}
ARG VLAB_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends rsyslog && rm -rf /var/lib/apt/lists/* \
 && useradd -m -s /bin/bash lab && echo 'lab:lab' | chpasswd
COPY sshd_config /etc/ssh/sshd_config
COPY rsyslog.conf /etc/rsyslog.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
COPY ident-http.py /usr/local/bin/ident-http.py
LABEL org.opencontainers.image.title="hwpool/device" org.opencontainers.image.version="${VLAB_VERSION}"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
