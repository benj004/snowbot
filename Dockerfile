# --- Base Python Image ---
FROM python:3.13-slim

# --- Set Working Directory ---
WORKDIR /app

# --- Install System Dependencies ---
# These help with SSL, requests, aiohttp, BS4, etc.
RUN apt-get update && apt-get install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# --- Install Python Dependencies ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy Bot Code ---
COPY . .

# --- Environment Variables (override in docker run or docker-compose) ---
ENV PYTHONUNBUFFERED=1

# --- Start Bot ---
CMD ["python", "mpls_snow_emergency_bot.py"]

RUN apt-get update && apt-get install -y tzdata
ENV TZ=America/Chicago

