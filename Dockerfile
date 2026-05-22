FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    docker==7.1.0 \
    requests==2.32.3 \
    PyYAML==6.0.2 \
    "pysnmp>=4.4.12"

# Copy agent
COPY watchdog.py .

CMD ["python3", "-u", "watchdog.py"]
