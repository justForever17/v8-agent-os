ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}
COPY node /opt/node
ENV PATH=/opt/npm/bin:/opt/node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY engine /opt/build-engine
COPY assets /opt/assets
COPY package.tgz /opt/package.tgz
COPY probes /opt/probes
# Package construction may access npm/apt. The acceptance container runs with
# --network none: first-start cannot fetch pip packages or a browser binary.
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates git util-linux passwd \
    && /opt/build-engine/apps/v8-agent-os-engine/.python/bin/python3 -m playwright install-deps chromium \
    && test ! -e /usr/bin/python3 && test ! -e /usr/bin/python \
    && npm install --global --prefix /opt/npm --ignore-scripts --no-audit --no-fund /opt/package.tgz \
    && useradd --create-home --shell /bin/bash server-test \
    && chmod -R a+rX /opt/assets /opt/npm /opt/probes \
    && rm -rf /opt/build-engine /opt/package.tgz /var/lib/apt/lists/*
USER server-test
ENV V8_AGENT_OS_HOME="/home/server-test/portable state"
WORKDIR /home/server-test
CMD ["node", "/opt/probes/portable_smoke.mjs", "--live"]
