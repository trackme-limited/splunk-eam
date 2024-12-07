#!/bin/bash
set -e

# Start Redis in the background
redis-server /app/config/redis.conf >/app/logs/redis.log 2>&1 &
echo "Starting Redis server and logging to /app/logs/redis.log..."

# Wait for Redis to become available
until redis-cli ping >/dev/null 2>&1; do
    echo "Waiting for Redis to be ready..."
    sleep 1
done

echo "Redis is ready. Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port 8443 --log-level debug >/app/logs/app.log 2>&1
