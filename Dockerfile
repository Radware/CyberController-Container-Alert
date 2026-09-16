FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    docker==7.1.0 \
    requests==2.32.3 \
    PyYAML==6.0.2 \
    "pysnmp>=6.2" \
    "cryptography>=42.0"

# Copy agent
COPY watchdog.py .

# Non-root — the docker.sock group membership needed to read the socket is
# granted at runtime via `group_add` in docker-compose.yaml (GID varies per host).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin watchdog \
    && chown -R watchdog:watchdog /app
USER watchdog

CMD ["python3", "-u", "watchdog.py"]
