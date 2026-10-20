import time

import httpx
import pytest

from protoface_comfyui.client import Client


async def test_ambiguous_submission_reuses_key_and_resumes(config, journal, monkeypatch):
    attempts = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("protoface_comfyui.client.asyncio.sleep", no_sleep)

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadError("lost response", request=request)
        return httpx.Response(202, json={"id": "run_same"})

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    assert await client.submit("protoface/test", {"prompt": "one"}, "intent") == "run_same"
    assert await client.submit("protoface/test", {"prompt": "one"}, "intent") == "run_same"
    assert len(attempts) == 2
    assert attempts[0].headers["idempotency-key"] == attempts[1].headers["idempotency-key"]
    assert attempts[0].content == attempts[1].content
    contents = journal.path.read_bytes()
    assert b"mock-secret" not in contents
    assert b"prompt" not in contents


async def test_in_progress_and_rate_limits_retry(config, journal, monkeypatch):
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("protoface_comfyui.client.asyncio.sleep", sleep)
    responses = iter(
        [
            httpx.Response(409, json={"error": {"code": "idempotency_key_in_progress"}}),
            httpx.Response(429, headers={"Retry-After": "0.1"}),
            httpx.Response(202, json={"id": "run_x"}),
        ]
    )
    client = Client(config, journal, transport=httpx.MockTransport(lambda _: next(responses)))
    assert await client.submit("test", {}, "intent") == "run_x"
    assert delays == [0.5, 0.1]


async def test_expired_ambiguous_job_never_resubmits(config, journal):
    journal.intent("intent")
    journal.update("intent", state="submitting")
    with journal.connect() as db:
        db.execute("UPDATE jobs SET created=?", (time.time() - 24 * 3600,))
    client = Client(config, journal, transport=httpx.MockTransport(lambda _: pytest.fail("must not submit")))
    with pytest.raises(RuntimeError, match="idempotency window"):
        await client.submit("test", {}, "intent")


async def test_upload_not_retried_and_success_cached(config, journal):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"id": "asset_1", "status": "ready", "modality": "image"})
        return httpx.Response(201, json={"id": "asset_1"})

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    assert await client.upload(b"image", "image", "a.png", "image/png") == "asset_1"
    assert await client.upload(b"image", "image", "a.png", "image/png") == "asset_1"
    assert len(requests) == 2
    assert [r.method for r in requests] == ["POST", "GET"]
    failing = Client(config, journal, transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(RuntimeError):
        await failing.upload(b"new", "image", "a.png", "image/png")


async def test_deleted_cached_upload_is_replaced_before_new_generation(config, journal):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "asset.not_found"}})
        return httpx.Response(201, json={"id": "asset_" + str(len(requests))})

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    assert await client.upload(b"same image", "image", "a.png", "image/png") == "asset_1"
    assert await client.upload(b"same image", "image", "a.png", "image/png") == "asset_3"
    assert [r.method for r in requests] == ["POST", "GET", "POST"]


async def test_live_catalog_authenticates_to_configured_origin(config, journal):
    config.base_url = "https://proxy.example.com"
    seen = []
    client = Client(
        config,
        journal,
        transport=httpx.MockTransport(
            lambda request: (seen.append(request), httpx.Response(200, json={"data": []}))[1]
        ),
    )
    assert await client.discover() == {"data": []}
    assert str(seen[0].url) == "https://proxy.example.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer mock-secret"
    config.api_key = ""
    with pytest.raises(ValueError, match="PROTOFACE_API_KEY"):
        await client.discover()
    assert len(seen) == 1


async def test_result_origin_never_gets_bearer(config, journal):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"png")

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    target = config.state_dir / "result.png"
    await client.download("https://media.example/result?signed=secret", target)
    assert target.read_bytes() == b"png"
    assert "authorization" not in seen[0].headers
    assert "signed" not in journal.path.read_bytes().decode(errors="ignore")


async def test_cancellation_and_timeout_do_not_create_jobs(config, journal):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "run_x", "status": "running", "progress": 20})

    client = Client(config, journal, transport=httpx.MockTransport(handler))

    class InterruptProcessingException(BaseException):
        pass

    def interrupt():
        raise InterruptProcessingException()

    with pytest.raises(InterruptProcessingException):
        await client.wait("run_x", interrupt=interrupt)
    assert requests[-1].url.path.endswith("/cancel")
    config.job_timeout = 0
    with pytest.raises(TimeoutError, match="resume"):
        await client.wait("run_x")
    assert sum(r.method == "POST" for r in requests) == 1


async def test_pending_progress_and_full_outputs(config, journal):
    requests = []
    progress = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "completed", "progress": 100})
        return httpx.Response(
            200,
            json={
                "id": "run_x",
                "outputs": [
                    {"type": "text", "modality": "text", "text": "a"},
                    {"type": "text", "modality": "text", "text": "b"},
                ],
            },
        )

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    view = await client.wait("run_x", progress=progress.append)
    assert len(view["outputs"]) == 2
    assert progress == [100]


async def test_download_limit_removes_partial_file(config, journal):
    config.max_download_bytes = 2
    client = Client(
        config, journal, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"toolong"))
    )
    with pytest.raises(RuntimeError, match="download limit"):
        await client.download("https://example.com/file", config.state_dir / "a.bin")
    assert not list(config.state_dir.glob("*.part"))


async def test_ambiguous_retry_keeps_original_attribution_across_upgrade(config, journal, monkeypatch):
    import json

    attempts = []

    async def no_sleep(_):
        pass

    monkeypatch.setattr("protoface_comfyui.client.asyncio.sleep", no_sleep)

    def failing(request):
        attempts.append(json.loads(request.content))
        raise httpx.ReadError("ambiguous", request=request)

    client = Client(config, journal, transport=httpx.MockTransport(failing))
    with pytest.raises(RuntimeError, match="network failure"):
        await client.submit("model", {"prompt": "a", "metadata": {"integration_version": "0.1.0"}}, "intent")
    client.transport = httpx.MockTransport(
        lambda r: (attempts.append(json.loads(r.content)), httpx.Response(202, json={"id": "run_old"}))[1]
    )
    await client.submit("model", {"prompt": "a", "metadata": {"integration_version": "0.2.0"}}, "intent")
    assert all(a["metadata"]["integration_version"] == "0.1.0" for a in attempts)


def test_credential_snapshot_does_not_change_mid_job(config, journal):
    client = Client(config, journal)
    frozen = client.snapshot()
    config.api_key = "different-account"
    assert frozen.config.api_key == "mock-secret"
    assert frozen.config.namespace != client.config.namespace


async def test_network_library_logs_do_not_expose_signed_urls(config, journal, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    client = Client(
        config, journal, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok"))
    )
    await client.download("https://media.example/file?secret-signature=private", config.state_dir / "a.bin")
    assert "secret-signature" not in caplog.text
    logging.getLogger("httpx").info("Unrelated extension log remains visible")
    assert "Unrelated extension log remains visible" in caplog.text
