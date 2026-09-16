# Multi-stage build for AI Agent Service
# Stage 1: Builder
FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies including Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    zip \
    unzip \
    ca-certificates \
    gnupg \
    apt-transport-https \
    git \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl
# TARGETARCH is supplied by BuildKit; without it an arm64 build would silently
# fetch the amd64 binary and produce a kubectl that cannot execute.
ARG TARGETARCH
RUN curl -fsSL "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/${TARGETARCH:-amd64}/kubectl" -o /usr/local/bin/kubectl \
    && chmod +x /usr/local/bin/kubectl

# Install Google Cloud SDK
RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y google-cloud-cli google-cloud-cli-gke-gcloud-auth-plugin \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally (required by claude-agent-sdk)
# Pinned so two builds of the same commit behave identically. Unpinned, a CLI
# release can start emitting message types the pinned SDK has never seen.
ARG CLAUDE_CODE_VERSION=2.1.197
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY . .

# Set PATH to include user-installed packages and npm global bin
ENV PATH=/root/.local/bin:/usr/local/bin:$PATH

# Environment variables with defaults
ENV PYTHONUNBUFFERED=1
ENV PORT=8002
ENV USER_WORKSPACES_DIR=/app/workspaces
ENV HOST=0.0.0.0
ENV HOME=/root

# Node.js environment variables for memory limits
ENV NODE_OPTIONS="--max-old-space-size=4096"

# Expose port
EXPOSE 8002

# Run the application
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8002", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--log-level", "info"]
