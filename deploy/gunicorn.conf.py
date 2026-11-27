"""Small shared-VPS defaults; the host proxy owns public HTTPS."""

import os

bind = "0.0.0.0:8000"
chdir = "/app"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5
# Gunicorn 23 gthread can drop accepted sockets while recycling a worker at a
# request-count limit. Local authenticated load reproduced that disconnect with
# the single-worker default. Keep workers running; planned releases replace them.
max_requests = 0
max_requests_jitter = 0
worker_tmp_dir = "/tmp"
accesslog = "-"
errorlog = "-"
capture_output = True
# Public access is only through host Nginx and the loopback-published port.
# Nginx overwrites forwarding headers before they reach this private network.
forwarded_allow_ips = "*"
# Avoid query strings in access logs: signed URLs and customer data can be there.
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(L)s'

if workers < 1 or threads < 1 or timeout < 1:
    raise ValueError("Gunicorn worker, thread and timeout settings must be positive.")
