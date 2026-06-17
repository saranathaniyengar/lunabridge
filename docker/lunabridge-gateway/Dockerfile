FROM python:3.11-slim
LABEL description="LunaBridge N6 gateway skeleton"
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnetfilter-queue-dev \
    libmnl-dev \
    build-essential \
    iproute2 \
    iptables \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
WORKDIR /app
CMD ["python", "--version"]
