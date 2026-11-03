"""HTTP for the free routing servers: polite rate limiting, and a curl fallback for old SSL.

The public OSRM and Valhalla servers only accept TLS 1.3. Some Pythons can't
speak it, notably the 3.9 that ships with macOS (LibreSSL 2.8.3). Rather than
falling back to unencrypted HTTP, routekit retries the request through the
system ``curl``, which ships with macOS, most Linux distributions, and
Windows 10+, and supports TLS 1.3. The connection stays encrypted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Optional
from urllib.parse import urlencode, urlsplit

import requests

from routekit import __version__

MIN_INTERVAL = 1.0  # seconds between requests to a public server (about 1 per second)
TIMEOUT = 60
USER_AGENT = f"routekit/{__version__} (+https://github.com/M-Scarnavack/routekit)"

_last_request: dict = {}


class HttpError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _wait_turn(url: str, interval: Optional[float] = None) -> None:
    interval = MIN_INTERVAL if interval is None else interval
    if interval <= 0:
        return
    host = urlsplit(url).netloc
    wait = interval - (time.monotonic() - _last_request.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def _via_curl(method: str, url: str, body: Optional[dict], headers: Optional[dict] = None) -> tuple:
    curl = shutil.which("curl")
    if not curl:
        raise HttpError("no curl available")
    cmd = [curl, "-sS", "--max-time", str(TIMEOUT), "-A", USER_AGENT, "-w", "\n%{http_code}"]
    # Headers go in a private temp config file (mode 0600, deleted right after) so secrets such as an
    # Authorization header never appear in the process list.
    config = "".join(f'header = "{k}: {v}"\n' for k, v in (headers or {}).items() if k.lower() != "user-agent")
    cfg_path = None
    if config:
        import tempfile
        fd, cfg_path = tempfile.mkstemp(prefix="routekit-curl-", suffix=".cfg")
        with os.fdopen(fd, "w") as fh:
            fh.write(config)
        cmd += ["--config", cfg_path]
    if method == "POST":
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, input=json.dumps(body) if body is not None else None,
                             capture_output=True, text=True, timeout=TIMEOUT + 10)
    finally:
        if cfg_path:
            os.unlink(cfg_path)
    if out.returncode != 0:
        raise HttpError(f"curl failed: {out.stderr.strip() or out.returncode}")
    text, _, code = out.stdout.rpartition("\n")
    return int(code), text


def request_json(method: str, url: str, params: Optional[dict] = None, body: Optional[dict] = None,
                 session=None, interval: Optional[float] = None, headers: Optional[dict] = None) -> dict:
    """GET (with `params`) or POST (with JSON `body`) and return parsed JSON.

    Raises HttpError with `.status` set for HTTP errors, or a plain HttpError when
    the server can't be reached.
    """
    if params:
        url = f"{url}?{urlencode(params)}"
    _wait_turn(url, interval)
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        if session is None:
            session = requests
        if method == "POST":
            resp = session.post(url, json=body, timeout=TIMEOUT, headers=hdrs)
        else:
            resp = session.get(url, timeout=TIMEOUT, headers=hdrs)
        status, text = resp.status_code, None
        try:
            data = resp.json()
        except ValueError:
            data = None
    except requests.exceptions.SSLError as e:
        try:
            status, text = _via_curl(method, url, body, hdrs)  # old Python SSL: let curl do TLS 1.3
        except (HttpError, subprocess.SubprocessError, OSError) as ce:
            import ssl
            raise HttpError(
                f"couldn't open a secure connection to {urlsplit(url).netloc}: your Python's SSL library "
                f"({ssl.OPENSSL_VERSION}) can't do TLS 1.3, and the curl fallback failed ({ce}). "
                "Install a current Python from python.org") from e
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    except requests.RequestException as e:
        raise HttpError(f"couldn't reach {urlsplit(url).netloc} ({e})") from e
    if status == 429:
        raise HttpError(f"{urlsplit(url).netloc} is rate-limiting requests; wait a minute, or use your own server",
                        status)
    if data is None:
        raise HttpError(f"{urlsplit(url).netloc} sent an unexpected response (HTTP {status})", status)
    if status >= 400 and not isinstance(data, dict):
        raise HttpError(f"{urlsplit(url).netloc} returned HTTP {status}", status)
    if isinstance(data, dict):
        data.setdefault("_http_status", status)
    return data
