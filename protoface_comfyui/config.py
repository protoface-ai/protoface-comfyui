"""Operator configuration; secrets never appear in node inputs or exported workflows."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from os import environ
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Config:
    state_dir: Path
    base_url: str = "https://api.protoface.com"
    api_key: str = ""
    concurrency: int = 2
    job_timeout: float = 7200
    poll_interval: float = 2
    max_download_bytes: int = 2_000_000_000

    @classmethod
    def from_env(cls) -> Config:
        state = Path(environ.get("PROTOFACE_STATE_DIR", "~/.cache/protoface-comfyui")).expanduser()
        key = environ.get("PROTOFACE_API_KEY", "")
        keyfile = environ.get("PROTOFACE_API_KEY_FILE")
        if not key and keyfile:
            key = Path(keyfile).expanduser().read_text().strip()
        base = environ.get("PROTOFACE_BASE_URL", "https://api.protoface.com").rstrip("/")
        url = urlsplit(base)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Invalid operator PROTOFACE_BASE_URL")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Protoface credentials require HTTPS outside localhost")
        timeout = float(environ.get("PROTOFACE_JOB_TIMEOUT_SECONDS", "7200"))
        poll = float(environ.get("PROTOFACE_POLL_INTERVAL_SECONDS", "2"))
        download_limit = int(environ.get("PROTOFACE_MAX_DOWNLOAD_BYTES", "2000000000"))
        if (
            not math.isfinite(timeout)
            or not math.isfinite(poll)
            or timeout <= 0
            or poll < 0.25
            or download_limit <= 0
        ):
            raise ValueError("Invalid Protoface timeout, polling interval or download limit")
        return cls(
            state,
            base,
            key,
            max(1, int(environ.get("PROTOFACE_CONCURRENCY", "2"))),
            timeout,
            poll,
            download_limit,
        )

    @property
    def namespace(self) -> str:
        if not self.api_key:
            raise ValueError(
                "Set PROTOFACE_API_KEY, PROTOFACE_API_KEY_FILE, or use the Protoface settings button."
            )
        return hashlib.sha256((self.base_url + "\0" + self.api_key).encode()).hexdigest()
