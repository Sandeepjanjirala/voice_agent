"""Probe the local web process and its dependencies without external API calls."""

import os
import sys
import urllib.error
import urllib.request

host = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip()
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/health/ready/", headers={"Host": host}
)
try:
    with urllib.request.urlopen(request, timeout=8) as response:
        sys.exit(0 if response.status == 200 else 1)
except (OSError, urllib.error.URLError):
    sys.exit(1)
