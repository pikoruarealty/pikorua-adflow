# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# System libs:
#   build-essential — compiles native extensions (onnxruntime, tokenizers)
#   libgomp1       — OpenMP runtime required by fastembed / ONNX
#   libgl1         — PIL JPEG/PNG support on slim images
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── dependency layer (cached until pyproject.toml changes) ──────────────────
COPY pyproject.toml ./
# Minimal stub so pip can resolve the package before the real src lands
RUN mkdir -p src/pikorua_adflow && touch src/pikorua_adflow/__init__.py

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e ".[all]" 2>/dev/null \
 || pip install --no-cache-dir -e .

# Pre-download the fastembed embedding model (~67 MB) so first run is instant.
# Model lands in /root/.cache/fastembed/ — persisted via a named volume in prod.
RUN python - <<'EOF'
from fastembed import TextEmbedding
TextEmbedding("BAAI/bge-small-en-v1.5")
EOF

# ── application source ───────────────────────────────────────────────────────
# Copy real src over the stub
COPY src/ ./src/
# Re-install in editable mode so entry-points resolve correctly
RUN pip install --no-cache-dir -e . --no-deps

# Static assets required at runtime
COPY portal/       ./portal/
COPY project_context/ ./project_context/

# outputs/ is mounted as a volume in production; create the dir so the app
# never crashes on first boot before the volume is attached.
RUN mkdir -p outputs

EXPOSE 8000

# Bind to 0.0.0.0 so the port is reachable outside the container.
CMD ["uvicorn", "pikorua_adflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
