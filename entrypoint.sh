#!/bin/bash
set -e

# Start Redis in the background
redis-server /app/config/redis.conf > >(tee -a /app/logs/redis.log) 2> >(tee -a /app/logs/redis.log >&2) &
echo "Starting Redis server and logging to /app/logs/redis.log..."

# Wait for Redis to become available
until redis-cli ping >/dev/null 2>&1; do
    echo "Waiting for Redis to be ready..."
    sleep 1
done

echo "Redis is ready. Starting application..."

# Ensure certificates are available
python -c "from main import ensure_certificates; ensure_certificates()"
echo "Certificates are ready. Starting application..."

# Start the application using Gunicorn with Uvicorn workers
exec gunicorn main:app -c /app/config/gunicorn.conf
