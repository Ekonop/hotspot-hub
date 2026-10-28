FROM rust:1.80-bullseye as builder
WORKDIR /app
COPY . .
RUN cargo build --release

FROM debian:bullseye-slim
RUN apt-get update && apt-get install -y libpcap0.8 conntrack iptables sudo ca-certificates && rm -rf /var/lib/apt/lists/
COPY --from=builder /app/target/release/rustnet-hub-backend /usr/local/bin/rustnet-hub
COPY --from=ghcr.io/domcyrus/rustnet:latest /usr/local/bin/rustnet /usr/bin/rustnet
EXPOSE 9090
CMD ["rustnet-hub"]
