FROM rust:1-bookworm AS builder
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake clang pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml ./
COPY platform ./platform
RUN cargo build --release --bin faultline

FROM debian:bookworm-slim
# docker.io provides the `docker` CLI client used to hard-restart a service container
# when a fault's effect can't be undone by a config-level reset (see reset_restart in
# scenario YAMLs). Requires the host Docker socket to be mounted at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /app/target/release/faultline /usr/local/bin/faultline
COPY scenarios ./scenarios
ENTRYPOINT ["faultline"]
