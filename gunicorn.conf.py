# gunicorn.conf.py
import multiprocessing

# Bind to the correct port
bind = "0.0.0.0:10000"

# Use Uvicorn worker for ASGI support
worker_class = "uvicorn.workers.UvicornWorker"

# Number of workers
workers = multiprocessing.cpu_count() * 2 + 1

# Timeout settings
timeout = 120
graceful_timeout = 10
keepalive = 5

# Logging
loglevel = "info"
accesslog = "-"
errorlog = "-"
