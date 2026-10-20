"""Small REST client with bounded, idempotent submission retries and resumable jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from . import __version__
from .catalog import digest
from .config import Config
from .journal import Journal
from .network_logging import ACTIVE

SUCCESS = {"completed", "succeeded"}
TERMINAL = SUCCESS | {"failed", "canceled", "cancelled"}


class ApiError(RuntimeError):
    def __init__(self, response: httpx.Response):
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        self.code = error.get("code", f"http_{response.status_code}")
        self.status = response.status_code
        param = f" ({error['param']})" if error.get("param") else ""
        request_id = f" Request: {error['request_id']}." if error.get("request_id") else ""
        hint = (
            " Check your API key."
            if response.status_code == 401
            else " Check your credits or account permissions."
            if response.status_code in {402, 403}
            else ""
        )
        super().__init__(f"Protoface: {self.code}{param}.{hint}{request_id}")


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response:
        try:
            return min(30.0, max(0.1, float(response.headers.get("Retry-After", ""))))
        except ValueError:
            pass
    return min(8.0, 0.5 * 2**attempt)


class Client:
    def __init__(self, config: Config, journal: Journal, *, transport=None):
        self.config = config
        self.journal = journal
        self.transport = transport
        self.semaphore = asyncio.Semaphore(config.concurrency)
        self.pipeline_semaphore = asyncio.Semaphore(config.concurrency)

    def snapshot(self):
        frozen = Client(replace(self.config), self.journal, transport=self.transport)
        frozen.semaphore = self.semaphore
        frozen.pipeline_semaphore = self.pipeline_semaphore
        return frozen

    @asynccontextmanager
    async def http(self, *, authenticated=True):
        headers = {"User-Agent": f"protoface-comfyui/{__version__}"}
        if authenticated:
            self.config.namespace  # Fail before any unauthenticated upload/generation.
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        token = ACTIVE.set(True)
        try:
            async with httpx.AsyncClient(
                base_url=self.config.base_url if authenticated else "",
                headers=headers,
                timeout=httpx.Timeout(60, connect=15),
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                yield client
        finally:
            ACTIVE.reset(token)

    async def request(self, method: str, path: str, *, retry=False, authenticated=True, **kwargs) -> dict:
        async with self.http(authenticated=authenticated) as http:
            for attempt in range(5):
                response = None
                try:
                    response = await http.request(method, path, **kwargs)
                except httpx.TransportError:
                    if not retry or attempt == 4:
                        raise RuntimeError(
                            "Protoface network failure. The saved request will resume on the next queue; keep generation_id unchanged."
                        ) from None
                else:
                    if response.is_success:
                        return response.json()
                    error = ApiError(response)
                    retryable = (
                        response.status_code == 429
                        or response.status_code >= 500
                        or (response.status_code == 409 and error.code in {"idempotency_key_in_progress"})
                    )
                    if not retry or not retryable or attempt == 4:
                        raise error
                await asyncio.sleep(retry_delay(response, attempt))
        raise AssertionError("Unreachable")

    async def discover(self) -> dict:
        return await self.request("GET", "/v1/models", retry=True)

    async def upload(self, content: bytes, modality: str, filename: str, content_type: str) -> str:
        content_digest = digest({"modality": modality, "bytes": hashlib.sha256(content).hexdigest()})
        namespace = self.config.namespace
        cached = self.journal.upload(namespace, content_digest)
        if cached:
            try:
                asset = await self.request("GET", f"/v1/assets/{quote(cached, safe='')}", retry=True)
            except ApiError as error:
                if error.status != 404:
                    raise
            else:
                if asset.get("status") == "ready" and asset.get("modality") == modality:
                    return cached
            self.journal.forget_upload(namespace, content_digest)
        # Asset upload has no idempotency contract: never blindly retry this POST.
        view = await self.request(
            "POST",
            "/v1/assets",
            data={"modality": modality},
            files={"file": (filename, content, content_type)},
        )
        asset_id = view["id"]
        self.journal.save_upload(namespace, content_digest, asset_id)
        return asset_id

    async def submit(self, model: str, payload: dict, intent: str) -> str:
        job = self.journal.intent(intent)
        if job["run_id"]:
            return job["run_id"]
        if job["state"] == "submitting" and time.time() - job["created"] >= 23 * 3600:
            raise RuntimeError(
                "Submission outcome is unresolved and its idempotency window is closing. Find the run in your Protoface account and use Resume Run. A new generation_id can create another paid job."
            )
        payload = dict(payload)
        saved = json.loads(job["payload"]) if job["payload"] else None
        if saved and "metadata" in saved:
            payload["metadata"] = saved["metadata"]
        payload_hash = digest(payload)
        if saved and saved["digest"] != payload_hash:
            raise RuntimeError(
                "Saved submission payload changed. Recover the existing run before requesting another paid generation."
            )
        self.journal.update(
            intent,
            state="submitting",
            payload={
                "digest": payload_hash,
                **({"metadata": payload["metadata"]} if "metadata" in payload else {}),
            },
        )
        try:
            queued = await self.request(
                "POST",
                f"/v1/run/{quote(model, safe='/')}",
                retry=True,
                json=payload,
                headers={"Idempotency-Key": job["token"]},
            )
        except ApiError as error:
            if error.status in {400, 401, 402, 403, 404, 422}:
                self.journal.update(intent, state="rejected")
            raise
        run_id = queued["id"]
        self.journal.update(intent, state="submitted", run_id=run_id)
        return run_id

    async def cancel(self, run_id: str) -> None:
        try:
            await self.request("POST", f"/v1/runs/{quote(run_id, safe='')}/cancel")
        except (RuntimeError, httpx.HTTPError):
            # Cancellation is best effort; operator can cancel/resume by the recorded run ID.
            pass

    async def wait(
        self,
        run_id: str,
        *,
        progress: Callable[[float], None] | None = None,
        interrupt: Callable[[], None] | None = None,
    ) -> dict:
        started = time.monotonic()
        try:
            while True:
                if interrupt:
                    interrupt()
                status = await self.request("GET", f"/v1/runs/{quote(run_id, safe='')}/status", retry=True)
                if progress:
                    progress(float(status.get("progress") or 0))
                if status["status"] in TERMINAL:
                    if status["status"] not in SUCCESS:
                        code = (status.get("error") or {}).get("code", status["status"])
                        raise RuntimeError(
                            f"Protoface run {run_id}: {code}. Keep generation_id to inspect the same run; increment it to request a new paid job."
                        )
                    return await self.request("GET", f"/v1/runs/{quote(run_id, safe='')}", retry=True)
                if time.monotonic() - started >= self.config.job_timeout:
                    raise TimeoutError(
                        f"Protoface run {run_id} is still pending. Queue unchanged to resume, or use Resume Run; no new submission is needed."
                    )
                remaining = self.config.poll_interval
                while remaining > 0:
                    await asyncio.sleep(min(0.25, remaining))
                    remaining -= 0.25
                    if interrupt:
                        interrupt()
        except asyncio.CancelledError:
            await asyncio.shield(self.cancel(run_id))
            raise
        except BaseException as error:
            # ComfyUI's native interrupt deliberately inherits directly from BaseException.
            if type(error).__name__ in {"InterruptProcessingException", "InterruptedError"}:
                await self.cancel(run_id)
            raise

    async def generate(self, model: str, payload: dict, intent: str, **callbacks) -> dict:
        async with self.semaphore:
            run_id = await self.submit(model, payload, intent)
            view = await self.wait(run_id, **callbacks)
            self.journal.update(intent, state=view["status"])
            return view

    async def download(self, url: str, target: Path) -> Path:
        parsed = urlsplit(url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise RuntimeError("Protoface returned an unsafe result URL; HTTPS is required.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
        # Signed media origins never receive the API bearer key; redirects are handled explicitly.
        async with self.http(authenticated=False) as http:
            current_url = url
            for _ in range(5):
                async with http.stream("GET", current_url) as response:
                    if response.is_redirect:
                        current_url = str(response.url.join(response.headers["location"]))
                        if urlsplit(current_url).scheme != "https":
                            raise RuntimeError("Insecure result redirect refused.")
                        continue
                    if not response.is_success:
                        raise RuntimeError(
                            f"Result download failed (HTTP {response.status_code}); queue unchanged to refresh its URL."
                        )
                    size = 0
                    try:
                        with temporary.open("wb") as output:
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.config.max_download_bytes:
                                    raise RuntimeError("Result exceeds the configured download limit.")
                                output.write(chunk)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    return target
        raise RuntimeError("Too many result redirects.")
