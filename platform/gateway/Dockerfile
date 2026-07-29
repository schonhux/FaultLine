FROM rust:1-bookworm AS builder
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake clang pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml ./
COPY platform ./platform
RUN cargo build --release --bin gateway

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/target/release/gateway /usr/local/bin/gateway
CMD ["gateway"]
