FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements-watchdog.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent
COPY watchdog.py .

CMD ["python3", "-u", "watchdog.py"]
