# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — build the React frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /build/web

# Cache npm install layer separately from source
COPY web/package.json web/package-lock.json ./
RUN npm ci --silent

COPY web/ ./
RUN npm run build        # outputs to /build/web/dist

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Python API server
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# System deps: build tools for C extensions (chromadb, scipy etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgl1-mesa-glx \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cache-friendly)
COPY pyproject.toml ./
# Dummy src structure so pip can discover the package metadata
RUN mkdir -p src/matclaw && touch src/matclaw/__init__.py
RUN pip install --no-cache-dir ".[dev]"

# Copy application source
COPY src ./src

# Copy built frontend into the location server.py expects: ROOT/web/dist
COPY --from=frontend-builder /build/web/dist ./web/dist

# Runtime dirs
RUN mkdir -p plots projects

# Optional: MATLAB Engine for Python
# If you have a MATLAB install on the host, mount the engine wheel and install:
#   docker build --build-arg MATLAB_WHEEL=matlab_engine-*.whl .
ARG MATLAB_WHEEL=""
COPY ${MATLAB_WHEEL:-/dev/null} /tmp/ 2>/dev/null || true
RUN if [ -n "$MATLAB_WHEEL" ] && [ -f "/tmp/$MATLAB_WHEEL" ]; then \
        pip install --no-cache-dir "/tmp/$MATLAB_WHEEL"; \
    fi

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV MATCLAW_OPEN_BROWSER=false
ENV MATCLAW_MATLAB__ENABLED=false

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python", "-m", "src.matclaw.api.main"]
