import os

# Runtime settings are configurable so the same image works as a standalone
# service or as a sidecar sharing a pod network namespace.
bind = os.environ.get("HTML2PDF__LISTEN_ADDRESS", "0.0.0.0:8080")
workers = int(os.environ.get("HTML2PDF__WORKERS", "2"))
timeout = int(os.environ.get("HTML2PDF__TIMEOUT", "45"))

# Give workers a short window to finish requests during graceful shutdown.
graceful_timeout = 10

# Keep all Gunicorn runtime files on the writable temporary filesystem.
worker_tmp_dir = "/tmp"

# Send request and application logs to the container's stdout/stderr.
accesslog = "-"
errorlog = "-"

# Avoid creating a Unix control socket in the otherwise read-only container.
control_socket_disable = True
