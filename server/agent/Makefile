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
	@echo "  make test          Run unit tests (no API key or CLI needed)"
	@echo "  make test-cov      Run unit tests with coverage"
	@echo "  make test-ws       End-to-end /ws/chat check (TOKEN=<jwt>)"
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

# Unit tests: no API key, no CLI, no database required
test:
	pytest -v

test-cov:
	pytest --cov=session --cov-report=term-missing

# End-to-end check against a running server (needs a valid JWT)
test-ws:
	@echo "Testing /ws/chat endpoint (set TOKEN=<jwt>)..."
	python3 scripts/test_websocket.py --token "$${TOKEN}"

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
