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

# UID 1000 account for install.sh's non-root hardening path (docker-compose.yaml
# defaults to root; install.sh instead sets WATCHDOG_UID/GID to run as this user).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin watchdog \
    && chown -R watchdog:watchdog /app

# Non-root by default so a bare `docker run` is restricted. Compose always sets
# `user:` explicitly, so manual installs still get their root default.
USER watchdog

CMD ["python3", "-u", "watchdog.py"]
