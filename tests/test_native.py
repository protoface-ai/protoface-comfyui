"""Exercise actual ComfyUI V3 validation/execution against a mocked Protoface API."""

import copy
import io as bytesio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("COMFYUI_PATH"), reason="Set COMFYUI_PATH to a tested official ComfyUI checkout"
)

if os.environ.get("COMFYUI_PATH"):
    import execution
    import nodes as comfy_nodes
    from comfy_api.latest import io
    from PIL import Image

    from protoface_comfyui.catalog import Catalog
    from protoface_comfyui.client import Client
    from protoface_comfyui.extension import Extension
    from protoface_comfyui.nodes import Generator


def defaults(inputs, prefix=""):
    flat = {}
    for spec in inputs:
        name = prefix + spec.id
        if isinstance(spec, io.DynamicCombo.Input):
            option = spec.options[0]
            if option.key in {"Image", "Video", "Audio"}:
                option = next(o for o in spec.options if o.key == "References")
            flat[name] = option.key
            flat.update(defaults(option.inputs, name + "."))
        elif isinstance(spec, io.Autogrow.Input):
            for index in range(spec.template.min):
                flat[name + "." + spec.template.prefix + str(index)] = ["reference", 0]
        elif spec.get_io_type() == "PROTOFACE_REFERENCE":
            flat[name] = ["reference", 0]
        elif spec.id == "request_scope":
            flat[name] = "test-workflow-unique-scope"
        else:
            flat[name] = spec.default
            if spec.get_io_type() == "STRING" and not flat[name]:
                flat[name] = "A test prompt"
    return flat


def api_transport(requests):
    image = bytesio.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, "PNG")

    def handler(request):
        requests.append(request)
        if request.url.path.startswith("/v1/run/"):
            return httpx.Response(202, json={"id": "run_test"})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"id": "run_test", "status": "completed", "progress": 100})
        if request.url.path == "/v1/runs/run_test":
            return httpx.Response(
                200,
                json={
                    "id": "run_test",
                    "status": "completed",
                    "outputs": [
                        {
                            "type": "asset",
                            "modality": "image",
                            "asset_id": "asset_result",
                            "file": {"url": "https://media.example/result.png", "content_type": "image/png"},
                        }
                    ],
                },
            )
        if request.url.host == "media.example":
            return httpx.Response(200, content=image.getvalue())
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_every_generated_node_uses_valid_public_v3_schema(config, journal):
    generator = Generator(Client(config, journal))
    catalog = Catalog(config.state_dir)
    classes = generator.generate(catalog)
    for node in classes:
        schema = node.define_schema()
        schema.validate()
        assert schema.is_api_node is False
        assert "api_key" not in {x.id for x in schema.inputs}
    assert len(classes) == len(catalog.operations) + len(generator.helpers)


async def test_mixed_catalog_keeps_native_execution_and_archives_unsupported_contract(
    config, journal, monkeypatch
):
    from protoface_comfyui.catalog import BUNDLE

    requests = []
    client = Client(config, journal, transport=api_transport(requests))
    catalog = Catalog(config.state_dir)
    fresh = json.loads(BUNDLE.read_text())
    model = next(m for m in fresh["models"] if m["id"] == "alibaba/qwen-image-3.0")
    model["request_schemas"]["image.edit"]["properties"]["boxes"] = {
        "type": "array",
        "maxItems": 1000,
        "items": {"type": "integer"},
    }
    changes = catalog.save_refresh(fresh)
    assert len(changes["unsupported_operations"]) == 1
    refreshed = Catalog(config.state_dir)
    generator = Generator(client)
    classes = generator.generate(refreshed)
    for cls in classes:
        cls.define_schema().validate()
    active = next(
        o
        for o in refreshed.operations
        if o.model["id"] == model["id"] and o.name == "image.generate" and not o.retired
    )
    node = next(cls for cls in classes if cls.__name__ == active.node_id)
    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, active.node_id, node)
    prompt = {"generate": {"class_type": active.node_id, "inputs": defaults(node.define_schema().inputs)}}
    valid, _, outputs, errors = await execution.validate_prompt("mixed", prompt, None)
    assert valid, errors
    server = SimpleNamespace(client_id=None, send_sync=Mock())
    executor = execution.PromptExecutor(
        server, cache_type=execution.CacheType.NONE, cache_args={"ram": 0, "ram_inactive": 0}
    )
    await executor.execute_async(prompt, "mixed", execute_outputs=outputs)
    assert executor.success, executor.status_messages
    assert sum(r.method == "POST" for r in requests) == 1
    archived = next(o for o in refreshed.operations if o.model["id"] == model["id"] and o.retired)
    old_node = next(cls for cls in classes if cls.__name__ == archived.node_id)
    with pytest.raises(RuntimeError, match="archived"):
        await old_node.execute(request_scope="saved-workflow")
    assert sum(r.method == "POST" for r in requests) == 1


async def test_operator_routes_report_skipped_operations_after_refresh_and_restart(config, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from server import PromptServer

    from protoface_comfyui.catalog import BUNDLE

    monkeypatch.setenv("PROTOFACE_STATE_DIR", str(config.state_dir))
    monkeypatch.setattr(PromptServer, "instance", SimpleNamespace(routes=web.RouteTableDef()), raising=False)
    extension = Extension()
    fresh = copy.deepcopy(json.loads(BUNDLE.read_text()))
    model = next(m for m in fresh["models"] if m["id"] == "alibaba/qwen-image-3.0")
    model["request_schemas"]["image.edit"]["properties"]["future"] = {"type": "object"}
    monkeypatch.setattr(extension.client, "discover", AsyncMock(return_value=fresh))
    await extension.on_load()
    app = web.Application()
    app.add_routes(PromptServer.instance.routes)
    async with TestClient(TestServer(app)) as session:
        settings = await (await session.get("/protoface/settings")).json()
        assert settings["unsupported_operations"] == []
        response = await session.post("/protoface/catalog/refresh", json={})
        assert response.status == 200
        changes = await response.json()
        assert changes["unsupported_operations"][0]["model_id"] == model["id"]
        assert changes["unsupported_operations"][0]["operation"] == "image.edit"
        extension.catalog = Catalog(config.state_dir)
        settings = await (await session.get("/protoface/settings")).json()
        assert settings["unsupported_operations"] == changes["unsupported_operations"]
        assert settings["active_nodes"] == sum(not o.retired for o in extension.catalog.operations)
        assert settings["cache_error"] is None


async def test_actual_comfy_prompt_executes_and_preserves_cache_intent(config, journal, monkeypatch):
    requests = []
    client = Client(config, journal, transport=api_transport(requests))
    catalog = Catalog(config.state_dir)
    op = next(
        o
        for o in catalog.operations
        if o.model["id"] == "alibaba/qwen-image-3.0" and o.name == "image.generate"
    )
    node = Generator(client).node(op)
    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, op.node_id, node)
    prompt = {"generate": {"class_type": op.node_id, "inputs": defaults(node.define_schema().inputs)}}
    valid, _, outputs, errors = await execution.validate_prompt("test", prompt, None)
    assert valid, errors
    server = SimpleNamespace(client_id=None, send_sync=Mock())
    executor = execution.PromptExecutor(
        server, cache_type=execution.CacheType.NONE, cache_args={"ram": 0, "ram_inactive": 0}
    )
    await executor.execute_async(prompt, "first", execute_outputs=outputs)
    assert executor.success, executor.status_messages
    await executor.execute_async(prompt, "resume", execute_outputs=outputs)
    assert executor.success, executor.status_messages
    assert sum(r.method == "POST" for r in requests) == 1
    prompt["generate"]["inputs"]["generation_id"] += 1
    await executor.execute_async(prompt, "new", execute_outputs=outputs)
    assert executor.success, executor.status_messages
    assert sum(r.method == "POST" for r in requests) == 2
    submissions = [json.loads(r.content) for r in requests if r.method == "POST"]
    assert submissions[0]["prompt"] == "A test prompt"
    assert submissions[0]["metadata"]["integration"] == "comfyui"


async def test_recorded_media_job_resumes_without_rechecking_or_reuploading_inputs(
    config, journal, monkeypatch
):
    import torch

    requests = []
    base_transport = api_transport(requests)

    def handler(request):
        if request.url.path.startswith("/v1/assets"):
            assert request.method == "POST", "Resume should not need its deleted input asset"
            requests.append(request)
            return httpx.Response(201, json={"id": "ast_uploaded"})
        return base_transport.handler(request)

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    op = next(
        o
        for o in Catalog(config.state_dir).operations
        if o.model["id"] == "alibaba/qwen-image-3.0" and o.name == "image.edit"
    )
    node = Generator(client).node(op)

    class SourceImage(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(node_id="TestSourceImage", inputs=[], outputs=[io.Image.Output()])

        @classmethod
        def execute(cls):
            return io.NodeOutput(torch.zeros(1, 16, 16, 3))

    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, op.node_id, node)
    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, "TestSourceImage", SourceImage)
    inputs = defaults(node.define_schema().inputs)
    inputs["images"] = "Image"
    inputs["images.items.item_0"] = ["source", 0]
    prompt = {
        "source": {"class_type": "TestSourceImage", "inputs": {}},
        "generate": {"class_type": op.node_id, "inputs": inputs},
    }
    valid, _, outputs, errors = await execution.validate_prompt("media-resume", prompt, None)
    assert valid, errors
    server = SimpleNamespace(client_id=None, send_sync=Mock())
    executor = execution.PromptExecutor(
        server, cache_type=execution.CacheType.NONE, cache_args={"ram": 0, "ram_inactive": 0}
    )
    for prompt_id in ("first-media", "resume-media"):
        await executor.execute_async(prompt, prompt_id, execute_outputs=outputs)
        assert executor.success, executor.status_messages
    assert sum(r.url.path == "/v1/assets" for r in requests) == 1
    assert sum(r.url.path.startswith("/v1/run/") for r in requests) == 1


async def test_same_type_nodes_keep_distinct_paid_intents_across_concurrent_media_encoding(
    config, journal, monkeypatch
):
    import threading

    import torch

    import protoface_comfyui.nodes as adapter_nodes

    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == "/v1/assets":
            return httpx.Response(201, json={"id": "asset_input"})
        if path.startswith("/v1/assets/"):
            return httpx.Response(200, json={"id": "asset_input", "status": "ready", "modality": "image"})
        if path.startswith("/v1/run/"):
            count = sum(r.url.path.startswith("/v1/run/") for r in requests)
            return httpx.Response(202, json={"id": f"run_{count}"})
        if path.startswith("/v1/runs/"):
            return httpx.Response(200, json={"id": path.split("/")[3], "status": "completed", "outputs": []})
        return httpx.Response(404)

    client = Client(config, journal, transport=httpx.MockTransport(handler))
    op = next(
        o
        for o in Catalog(config.state_dir).operations
        if o.model["id"] == "alibaba/qwen-image-3.0" and o.name == "image.edit"
    )
    node = Generator(client).node(op)

    class SourceImage(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(node_id="TestConcurrentSourceImage", inputs=[], outputs=[io.Image.Output()])

        @classmethod
        def execute(cls):
            return io.NodeOutput(torch.zeros(1, 16, 16, 3))

    barrier = threading.Barrier(2, timeout=5)
    original_encode = adapter_nodes.encode_media

    def overlapping_encode(*args):
        barrier.wait()  # Both same-type executions yield before computing paid intent.
        return original_encode(*args)

    monkeypatch.setattr(adapter_nodes, "encode_media", overlapping_encode)
    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, op.node_id, node)
    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, "TestConcurrentSourceImage", SourceImage)
    inputs = defaults(node.define_schema().inputs)
    inputs["images"] = "Image"
    inputs["images.items.item_0"] = ["source", 0]
    # Deliberately share request_scope to make the Comfy execution IDs decisive.
    prompt = {
        "source": {"class_type": "TestConcurrentSourceImage", "inputs": {}},
        "first": {"class_type": op.node_id, "inputs": dict(inputs)},
        "second": {"class_type": op.node_id, "inputs": dict(inputs)},
    }
    valid, _, outputs, errors = await execution.validate_prompt("concurrent-intents", prompt, None)
    assert valid, errors
    executor = execution.PromptExecutor(
        SimpleNamespace(client_id=None, send_sync=Mock()),
        cache_type=execution.CacheType.NONE,
        cache_args={"ram": 0, "ram_inactive": 0},
    )
    for prompt_id in ("first-concurrent", "retry-concurrent"):
        await executor.execute_async(prompt, prompt_id, execute_outputs=outputs)
        assert executor.success, executor.status_messages
        submissions = [r for r in requests if r.url.path.startswith("/v1/run/")]
        assert len(submissions) == 2
        assert len({r.headers["Idempotency-Key"] for r in submissions}) == 2


def test_invalid_cached_display_metadata_does_not_break_extension_startup(config, monkeypatch):
    from protoface_comfyui.catalog import BUNDLE
    from protoface_comfyui.config import Config

    bad = json.loads(BUNDLE.read_text())
    del bad["models"][0]["display_name"]
    (config.state_dir / "catalog.json").write_text(json.dumps(bad))
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: config))
    extension = Extension()
    assert extension.catalog.cache_error
    for node in extension.nodes:
        node.define_schema().validate()


async def test_nested_helper_connections_validate_in_real_comfy(config, journal, monkeypatch):
    extension = Extension()
    for node in extension.nodes:
        monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, node.define_schema().node_id, node)
    op = next(
        o
        for o in extension.catalog.operations
        if o.model["id"] == "kuaishou/kling-3.0-turbo" and o.name == "video.generate"
    )
    node = comfy_nodes.NODE_CLASS_MAPPINGS[op.node_id]
    helper = next(
        n
        for n in extension.nodes
        if n.define_schema().outputs[0].get_io_type()
        == node.define_schema()
        .inputs[next(i for i, x in enumerate(node.define_schema().inputs) if x.id == "multi_prompt")]
        .options[-1]
        .inputs[0]
        .template.input.get_io_type()
    )
    inputs = defaults(node.define_schema().inputs)
    inputs["prompt"] = "Default"
    inputs["multi_prompt"] = "Set"
    inputs["multi_prompt.items.item_0"] = ["shot", 0]
    prompt = {
        "shot": {
            "class_type": helper.define_schema().node_id,
            "inputs": defaults(helper.define_schema().inputs),
        },
        "generate": {"class_type": op.node_id, "inputs": inputs},
    }
    valid, _, _, errors = await execution.validate_prompt("nested", prompt, None)
    assert valid, errors


async def test_entire_catalog_runs_through_comfy_engine_with_mock_jobs(config, journal, monkeypatch):
    from protoface_comfyui.catalog import validate_payload
    from protoface_comfyui.media import Reference
    from protoface_comfyui.nodes import REFERENCE

    submissions = []

    class FakeClient(Client):
        def snapshot(self):
            return self

        async def generate(self, model, payload, intent, **callbacks):
            submissions.append((model, payload))
            return {"id": "mock_" + str(len(submissions)), "status": "completed", "outputs": []}

    client = FakeClient(config, journal)
    catalog = Catalog(config.state_dir)
    generator = Generator(client)
    for node in generator.generate(catalog):
        monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, node.define_schema().node_id, node)

    class SourceReference(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(
                node_id="TestReference",
                inputs=[io.Combo.Input("modality", options=["image", "video", "audio"])],
                outputs=[REFERENCE.Output()],
            )

        @classmethod
        def execute(cls, modality):
            return io.NodeOutput(Reference("asset_mock_" + modality, modality))

    monkeypatch.setitem(comfy_nodes.NODE_CLASS_MAPPINGS, "TestReference", SourceReference)
    server = SimpleNamespace(client_id=None, send_sync=Mock())
    executor = execution.PromptExecutor(
        server, cache_type=execution.CacheType.NONE, cache_args={"ram": 0, "ram_inactive": 0}
    )
    current = [o for o in catalog.operations if not o.retired]
    for index, op in enumerate(current):
        node = comfy_nodes.NODE_CLASS_MAPPINGS[op.node_id]
        inputs = defaults(node.define_schema().inputs)
        if inputs.get("prompt") == "Default":
            inputs["prompt"] = "Set"
            inputs["prompt.value"] = "A test prompt"
        prompt = {"generate": {"class_type": op.node_id, "inputs": inputs}}
        for field, schema in op.fields.items():
            media = schema.get("x-protoface-media")
            if not media or inputs.get(field) == "Default":
                continue
            source = "reference_" + field
            prompt[source] = {"class_type": "TestReference", "inputs": {"modality": media["modalities"][0]}}
            for key in list(inputs):
                if key.startswith(field + ".") and inputs[key] == ["reference", 0]:
                    inputs[key] = [source, 0]
        valid, _, outputs, errors = await execution.validate_prompt(str(index), prompt, None)
        assert valid, (op.model["id"], op.name, errors)
        await executor.execute_async(prompt, str(index), execute_outputs=outputs)
        assert executor.success, (op.model["id"], op.name, executor.status_messages)
        assert submissions[-1][0] == op.model["id"]
        validate_payload(op, submissions[-1][1])
    assert len(submissions) == len(current)


async def test_operator_refresh_authentication_and_secret_boundary(config, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from server import PromptServer

    from protoface_comfyui.config import Config

    routes = web.RouteTableDef()
    monkeypatch.setattr(PromptServer, "instance", SimpleNamespace(routes=routes), raising=False)
    monkeypatch.setattr(Config, "from_env", classmethod(lambda cls: config))
    extension = Extension()
    seen = []
    catalog = json.loads((Path(__file__).parents[1] / "protoface_comfyui/data/catalog.json").read_text())

    def handler(request):
        seen.append(request)
        if request.headers["authorization"] != "Bearer mock-secret":
            return httpx.Response(401, json={"error": {"code": "auth.invalid_key"}})
        return httpx.Response(200, json=catalog)

    extension.client.transport = httpx.MockTransport(handler)
    await extension.on_load()
    app = web.Application()
    app.add_routes(routes)
    async with TestClient(TestServer(app)) as operator:
        response = await operator.get("/protoface/settings")
        settings = await response.json()
        assert settings["configured"]
        assert "mock-secret" not in json.dumps(settings)
        response = await operator.post("/protoface/catalog/refresh", json={})
        assert response.status == 200
        assert seen[0].url.path == "/v1/models"
        baseline = extension.catalog.revision
        response = await operator.post(
            "/protoface/catalog/refresh", json={}, headers={"Origin": "https://untrusted.example"}
        )
        assert response.status == 403
        assert len(seen) == 1
        await operator.post("/protoface/credentials", json={"api_key": "wrong-key"})
        response = await operator.post("/protoface/catalog/refresh", json={})
        assert response.status == 400
        assert "Check your API key" in await response.text()
        assert extension.catalog.revision == baseline
        await operator.post("/protoface/credentials", json={"api_key": ""})
        response = await operator.post("/protoface/catalog/refresh", json={})
        assert response.status == 400
        assert len(seen) == 2


async def test_actual_comfy_interrupt_cancels_remote_job(config, journal):
    from comfy.model_management import InterruptProcessingException

    seen = []
    client = Client(
        config,
        journal,
        transport=httpx.MockTransport(
            lambda request: (seen.append(request), httpx.Response(200, json={"status": "canceled"}))[1]
        ),
    )

    def interrupt():
        raise InterruptProcessingException()

    with pytest.raises(InterruptProcessingException):
        await client.wait("run_existing", interrupt=interrupt)
    assert [(request.method, request.url.path) for request in seen] == [
        ("POST", "/v1/runs/run_existing/cancel")
    ]
