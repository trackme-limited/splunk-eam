#!/bin/bash
set -e

# Start Redis in the background
redis-server /etc/redis/redis.conf >/app/logs/redis.log 2>&1 &
echo "Starting Redis server..."

# Wait for Redis to become available
until redis-cli ping >/dev/null 2>&1; do
    echo "Waiting for Redis..."
    sleep 1
done

echo "Redis is ready. Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port 8443 --log-level debug
