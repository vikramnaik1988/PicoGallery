# ---- Build stage ----
FROM golang:1.22-alpine AS builder

RUN apk add --no-cache gcc musl-dev

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w" \
    -o picogallery \
    ./cmd/server

# ---- Runtime stage ----
FROM alpine:3.19

RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app
COPY --from=builder /app/picogallery .

# Optional: install ffmpeg for video thumbnail support
# RUN apk add --no-cache ffmpeg

EXPOSE 3456

ENV PICO_STORAGE_ROOT=/data/storage
ENV PICO_DB_PATH=/data/storage/picogallery.db

ENTRYPOINT ["/app/picogallery"]
