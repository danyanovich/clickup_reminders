import os

# Gunicorn configuration file
# https://docs.gunicorn.org/en/stable/configure.html#configuration-file

# Server socket
bind = "0.0.0.0:5000"
backlog = 2048

# Worker processes
workers = os.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process naming
proc_name = "clickup_reminders_webhook"
