FROM golang:1.25 AS builder
WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY go/go.mod go/go.sum ./go/
COPY proto ./proto
COPY scripts/generate_protos.sh ./scripts/generate_protos.sh

RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11 \
    && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1 \
    && bash scripts/generate_protos.sh generated

COPY go/cmd ./go/cmd
COPY go/internal ./go/internal
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
