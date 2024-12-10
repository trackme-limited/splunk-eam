#
# gunicorn.conf.py
#

# Bind to all interfaces on port 8443
bind = "0.0.0.0:8443"

# Worker configuration
workers = 8  # Adjust based on `2-4 * (CPU cores)`
worker_class = "uvicorn.workers.UvicornWorker"
threads = 4  # Threads per worker for concurrency

# Timeout settings for long-running tasks
timeout = 900  # Allow up to 15 minutes for Ansible tasks
graceful_timeout = 900  # Clean worker shutdown

# Max requests for stability
max_requests = 1000
max_requests_jitter = 100

# SSL configuration
certfile = "/app/certs/ssl_cert.pem"
keyfile = "/app/certs/ssl_key.pem"

# Logging
accesslog = "/app/logs/gunicorn_access.log"
errorlog = "/app/logs/gunicorn_error.log"
loglevel = "info"
