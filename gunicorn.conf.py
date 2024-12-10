#
# gunicorn.conf
#

# Listen on all interfaces on port 8443
bind = "0.0.0.0:8443"
# Number of worker processes
workers = 4
# Number of worker threads
worker_class = "uvicorn.workers.UvicornWorker"
# SSL cert and key files
certfile = "/app/certs/ssl_cert.pem"
keyfile = "/app/certs/ssl_key.pem"
# Access and error logs
accesslog = "/app/logs/gunicorn_access.log"
errorlog = "/app/logs/gunicorn_error.log"
loglevel = "info"
