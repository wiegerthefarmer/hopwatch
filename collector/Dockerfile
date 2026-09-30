FROM python:3.13-slim

# mtr-tiny = mtr without the GTK GUI
RUN apt-get update \
    && apt-get install -y --no-install-recommends mtr-tiny \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hopwatch ./hopwatch
COPY tests ./tests

# Runs as root so mtr can open raw sockets (Docker grants NET_RAW by default).
CMD ["python", "-m", "hopwatch", "/config/hopwatch.yml"]
