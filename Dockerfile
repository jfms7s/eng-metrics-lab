# quality-report CI image: the CLI plus the analysis tools it shells out to.
# amd64 only. No USER directive on purpose: Woodpecker steps share a root-owned
# workspace; the GitHub composite action passes --user itself.
FROM docker.io/library/node:22-bookworm-slim AS machete
ARG MACHETE_VERSION=0.9.2
ARG MACHETE_SHA256=48200087f54c55aabcd4db4af1e25742b49846c02a1b1bfa134711945b35b2e9
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /tmp/machete.tgz \
       "https://github.com/bnjbvr/cargo-machete/releases/download/v${MACHETE_VERSION}/cargo-machete-v${MACHETE_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && echo "${MACHETE_SHA256}  /tmp/machete.tgz" | sha256sum -c - \
    && tar -xzf /tmp/machete.tgz -C /tmp --strip-components=1 \
       "cargo-machete-v${MACHETE_VERSION}-x86_64-unknown-linux-musl/cargo-machete"

FROM docker.io/library/node:22-bookworm-slim
ARG JSCPD_VERSION=4.3.0
ARG LIZARD_VERSION=1.24.1
ARG VULTURE_VERSION=2.16
ARG DEPTRY_VERSION=0.25.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g "jscpd@${JSCPD_VERSION}" && npm cache clean --force
COPY --from=machete /tmp/cargo-machete /usr/local/bin/cargo-machete
COPY . /src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir "lizard==${LIZARD_VERSION}" "vulture==${VULTURE_VERSION}" "deptry==${DEPTRY_VERSION}" /src \
    && rm -rf /src
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /work
