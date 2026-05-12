# =============================================================================
# InRes Agent Development Makefile
# =============================================================================
#
# Quick commands for development workflow:
#
#   make dev          - Start development server with hot reload
#   make dev-docker   - Start with Docker Compose
#   make test         - Run unit tests
#   make test-ws      - Test WebSocket endpoints
#   make logs         - View Docker logs
#   make clean        - Stop and clean up
#
# =============================================================================

.PHONY: help dev dev-docker dev-debug test test-agent test-ws logs clean lint

# Default target
help:
	@echo "InRes Agent Commands"
	@echo "===================="
	@echo ""
	@echo "Production:"
	@echo "  make prod          Start production services (Docker)"
	@echo "  make logs-prod     View production logs"
	@echo "  make stop-prod     Stop production services"
	@echo ""
	@echo "Development:"
	@echo "  make dev           Start local dev server (uvicorn with reload)"
	@echo "  make dev-docker    Start with Docker Compose (dev mode)"
	@echo "  make dev-debug     Start Docker with Redis UI"
	@echo ""
	@echo "Testing:"
	@echo "  make test          Run pytest"
	@echo "  make test-agent    Test SDKHybridAgent directly"
	@echo "  make test-ws       Test WebSocket /ws/chat"
	@echo ""
	@echo "Docker:"
	@echo "  make build         Build Docker image"
	@echo "  make logs          View Docker logs"
	@echo "  make clean         Stop and remove containers"
	@echo ""
	@echo "Other:"
	@echo "  make lint          Run linter"
	@echo "  make format        Format code"
	@echo ""

# =============================================================================
# DEVELOPMENT
# =============================================================================

# =============================================================================
# PRODUCTION
# =============================================================================

# Start production services
prod:
	@echo "Starting production services..."
	docker compose up -d --build
	@echo "Services started. View logs: make logs-prod"

# View production logs
logs-prod:
	docker compose logs -f agent

# Stop production services
stop-prod:
	docker compose down

# =============================================================================
# DEVELOPMENT
# =============================================================================

# Start local development server (no Docker)
dev:
	@echo "Starting development server..."
	@echo "Make sure ANTHROPIC_API_KEY is set!"
	@test -n "$$SUPABASE_URL" || export SUPABASE_URL=http://127.0.0.1:54321; \
	uvicorn main:app --host 0.0.0.0 --port 8002 --reload --log-level debug

# Start with Docker Compose (development)
dev-docker:
	@echo "Starting Docker Compose development environment..."
	docker compose -f docker-compose.dev.yaml up --build

# Start with Docker + Redis UI for debugging
dev-debug:
	@echo "Starting Docker Compose with Redis UI..."
	docker compose -f docker-compose.dev.yaml --profile debug up --build

# Start in background (development)
dev-bg:
	docker compose -f docker-compose.dev.yaml up -d --build
	@echo "Services started in background"
	@echo "View logs: make logs"

# =============================================================================
# TESTING
# =============================================================================

# Run pytest
test:
	pytest -v

# Test SDK hybrid agent directly (no WebSocket)
test-agent:
	@echo "Testing SDKHybridAgent..."
	python3 scripts/test_sdk_hybrid.py "Hello, what can you help me with?"

# Test SDK hybrid with tool prompt
test-agent-tools:
	@echo "Testing SDKHybridAgent with tools..."
	python3 scripts/test_sdk_hybrid.py "What is the current time?" --tools

# Test legacy hybrid agent (for comparison)
test-agent-legacy:
	@echo "Testing legacy HybridAgent..."
	python3 scripts/test_hybrid.py "Hello, what can you help me with?"

# Test WebSocket endpoint
test-ws:
	@echo "Testing /ws/chat endpoint..."
	python3 scripts/test_websocket.py

# =============================================================================
# DOCKER
# =============================================================================

# Build Docker image
build:
	docker compose -f docker-compose.dev.yaml build

# View logs
logs:
	docker compose -f docker-compose.dev.yaml logs -f agent

# View all logs
logs-all:
	docker compose -f docker-compose.dev.yaml logs -f

# Stop containers
stop:
	docker compose -f docker-compose.dev.yaml down

# Clean up (stop and remove volumes)
clean:
	docker compose -f docker-compose.dev.yaml down -v
	@echo "Containers and volumes removed"

# =============================================================================
# CODE QUALITY
# =============================================================================

# Run linter
lint:
	python -m flake8 --max-line-length 120 --ignore=E501,W503

# Format code
format:
	python -m black .

# Type check
typecheck:
	python -m mypy --ignore-missing-imports .

# =============================================================================
# UTILITIES
# =============================================================================

# Show environment info
info:
	@echo "Python: $(shell python --version)"
	@echo "Docker: $(shell docker --version)"
	@echo "ANTHROPIC_API_KEY: $(if $(ANTHROPIC_API_KEY),set,NOT SET!)"
	@echo ""
	@docker compose -f docker-compose.dev.yaml ps 2>/dev/null || echo "Docker services: not running"

# Redis CLI (connect to dev Redis)
redis-cli:
	docker exec -it inres-agent-redis-dev redis-cli

# Shell into agent container
shell:
	docker exec -it inres-agent-dev /bin/bash
