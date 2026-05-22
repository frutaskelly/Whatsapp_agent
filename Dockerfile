#syntax=docker/dockerfile:1

# Multi-stage build: Dependencies + Runtime
FROM dhi.io/python:3.11-alpine3.23-dev as builder

WORKDIR /build
RUN apk add --no-cache gcc g++

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM dhi.io/python:3.11-alpine3.23

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder --chown=nonroot:nonroot /root/.local /home/nonroot/.local

# Copy app code
COPY --chown=nonroot:nonroot . .

# Set PATH for pip-installed binaries
ENV PATH=/home/nonroot/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000

USER nonroot

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/health')" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app.main:app"]
