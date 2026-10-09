FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends librsvg2-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY poster.py server.py ./
RUN mkdir -p /app/output /app/data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python3", "server.py"]
