FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    docker==7.1.0 \
    requests==2.32.3 \
    PyYAML==6.0.2 \
    "pyasn1==0.4.8" \
    "pyasn1-modules==0.2.8" \
    "pysnmp>=4.4.12,<5"

# Copy agent
COPY watchdog.py .

CMD ["python3", "-u", "watchdog.py"]
