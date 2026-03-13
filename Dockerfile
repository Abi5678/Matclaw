# MatClaw daemon: Python 3.11 + system deps + optional MATLAB Engine for Python.
# Build the MATLAB engine wheel on a machine with MATLAB:
#   cd $MATLABROOT/extern/engines/python && python setup.py bdist_wheel && cp dist/*.whl /path/to/MatClaw/wheels/
# Then uncomment the COPY + pip install block below and rebuild.

FROM python:3.11-slim

WORKDIR /app

# System dependencies: build (for compiling Python deps), rsync (sync handler), OpenGL (MATLAB figures)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    rsync \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Project dependencies from pyproject.toml
COPY pyproject.toml ./
COPY main.py ./
COPY src ./src

RUN pip install --no-cache-dir .

# Optional: MATLAB Engine for Python (uncomment when you have the wheel in wheels/)
# COPY wheels/matlab_engine*.whl /tmp/
# RUN pip install --no-cache-dir /tmp/matlab_engine*.whl

# Volumes: persist ChromaDB and incoming data (Sentry watches data_in)
VOLUME ["/app/.matclaw_chromadb", "/app/data_in", "/app/reports"]

# Default: run the daemon (use python -m or entrypoint to main)
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
