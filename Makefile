.PHONY: build run dev test clean docker arm64 deploy

# Local build
build:
	CGO_ENABLED=0 go build -ldflags="-s -w" -o bin/picogallery ./cmd/server

# Run locally with config.yaml
run: build
	./bin/picogallery --config config.yaml

# Development mode with hot reload (requires 'air': go install github.com/cosmtrek/air@latest)
dev:
	air -c .air.toml

# Run tests
test:
	go test ./...

# Cross-compile for Raspberry Pi (ARM64)
arm64:
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build \
		-ldflags="-s -w" \
		-o bin/picogallery-arm64 \
		./cmd/server

# Cross-compile for older RPi (ARM 32-bit, Raspberry Pi 2/3 with 32-bit OS)
arm32:
	CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build \
		-ldflags="-s -w" \
		-o bin/picogallery-armv7 \
		./cmd/server

# Docker build
docker:
	docker build -t picogallery/picogallery:latest .

# Docker multi-arch build (push to registry)
docker-multiarch:
	docker buildx build \
		--platform linux/amd64,linux/arm64,linux/arm/v7 \
		-t picogallery/picogallery:latest \
		--push .

# Deploy to Raspberry Pi over SSH
# Usage: make deploy PI=192.168.1.50 ARCH=arm64
PI   ?= raspberry.local
ARCH ?= arm64
deploy:
	bash deploy/deploy-to-pi.sh $(PI) $(ARCH)

clean:
	rm -rf bin/
