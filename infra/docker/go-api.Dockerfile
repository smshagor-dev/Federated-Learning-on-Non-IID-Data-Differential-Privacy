FROM golang:1.25 AS builder
WORKDIR /src
COPY go/go.mod go/go.sum ./go/
COPY go/cmd ./go/cmd
COPY go/internal ./go/internal
COPY go/generated ./go/generated
WORKDIR /src/go
RUN go mod download
RUN go build -o /out/api ./cmd/api

FROM debian:bookworm-slim
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /out/api /app/api
EXPOSE 8080
ENTRYPOINT ["/app/api"]
