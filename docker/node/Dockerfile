# Data-plane node image: packages only, no configuration.
# All configuration is rendered by Ansible into /etc/sos (bind mount) and
# applied by sos-apply on every container start.
FROM debian:trixie-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      frr frr-pythontools \
      wireguard-tools \
      iproute2 nftables \
      iputils-ping traceroute mtr-tiny tcpdump iperf3 \
      jq procps ca-certificates \
      python3 \
 && rm -rf /var/lib/apt/lists/*

COPY sos-apply /usr/local/bin/sos-apply
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/sos-apply /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sleep", "infinity"]
