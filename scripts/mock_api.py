"""Local-only Protoface API fixture. Never calls Protoface or any generation provider."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import uuid
from pathlib import Path

import av
import numpy as np
from aiohttp import web
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protoface_comfyui.catalog import BUNDLE, parse_catalog, validate_payload


def fixtures():
    image = io.BytesIO()
    Image.new("RGB", (320, 192), "#2975a5").save(image, "PNG")
    video = io.BytesIO()
    with av.open(video, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=12)
        stream.width, stream.height, stream.pix_fmt = 320, 192, "yuv420p"
        for i in range(12):
            array = np.zeros((192, 320, 3), dtype=np.uint8)
            array[..., 0] = i * 20
            array[..., 2] = 120
            for packet in stream.encode(av.VideoFrame.from_ndarray(array, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    audio = io.BytesIO()
    import wave

    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(np.zeros(24000, dtype="<i2").tobytes())
    return {
        "image": (image.getvalue(), "image/png"),
        "video": (video.getvalue(), "video/mp4"),
        "audio": (audio.getvalue(), "audio/wav"),
        "text": (b"Synthetic transcript fixture.", "text/plain"),
        "vector": (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="192"><rect width="320" height="192" fill="#2975a5"/></svg>',
            "image/svg+xml",
        ),
        "model.3d": (b"glTF" + bytes(8), "model/gltf-binary"),
    }


def create_app(port=8199, delay=0):
    catalog = json.loads(BUNDLE.read_text())
    operations = {(o.model["id"], o.name): o for o in parse_catalog(catalog)}
    media = fixtures()
    runs, tokens, submissions, assets = {}, {}, [], {}
    routes = web.RouteTableDef()

    @routes.get("/v1/models")
    async def models(request):
        return web.json_response({"object": "list", "data": catalog["models"]})

    @routes.post("/v1/assets")
    async def upload(request):
        fields = await request.post()
        asset = {"id": "asset_mock_" + uuid.uuid4().hex, "modality": fields["modality"], "status": "ready"}
        assets[asset["id"]] = asset
        return web.json_response(asset, status=201)

    @routes.get("/v1/assets/{asset_id}")
    async def get_asset(request):
        asset = assets.get(request.match_info["asset_id"])
        if asset is None:
            return web.json_response({"error": {"code": "asset.not_found"}}, status=404)
        return web.json_response(asset)

    @routes.post("/v1/run/{model:.+}")
    async def submit(request):
        payload = await request.json()
        key = request.headers.get("Idempotency-Key")
        if key and key in tokens:
            return web.json_response({"id": tokens[key], "status": "queued"}, status=202)
        model = request.match_info["model"]
        op_name = payload.get("operation") or next(name for m, name in operations if m == model)
        try:
            op = operations[model, op_name]
            validate_payload(op, payload)
        except (KeyError, ValueError) as error:
            return web.json_response(
                {"error": {"code": "mock_validation", "message": str(error)}}, status=422
            )
        run_id = "run_mock_" + uuid.uuid4().hex[:16]
        outputs = []
        for index, descriptor in enumerate(op.outputs):
            modality = descriptor["modality"]
            fixture = "vector" if descriptor.get("kind") == "vector" else modality
            outputs.append(
                {
                    "type": "asset",
                    "modality": modality,
                    "asset_id": "asset_" + run_id + "_" + str(index),
                    "file": {
                        "url": f"http://127.0.0.1:{port}/media/{fixture}",
                        "content_type": media[fixture][1],
                        "file_name": fixture,
                    },
                }
            )
        runs[run_id] = {
            "id": run_id,
            "model": model,
            "operation": op_name,
            "status": "completed",
            "progress": 100,
            "outputs": outputs,
            "_start": time.monotonic(),
        }
        tokens[key] = run_id
        submissions.append({"run_id": run_id, "model": model, "operation": op_name})
        return web.json_response({"id": run_id, "status": "queued"}, status=202)

    @routes.get("/v1/runs/{run_id}/status")
    async def status(request):
        run = runs[request.match_info["run_id"]]
        running = time.monotonic() - run["_start"] < delay
        return web.json_response(
            {
                "id": run["id"],
                "status": "running" if running else run["status"],
                "progress": 30 if running else 100,
                "error": None,
            }
        )

    @routes.get("/v1/runs/{run_id}")
    async def result(request):
        return web.json_response(
            {k: v for k, v in runs[request.match_info["run_id"]].items() if not k.startswith("_")}
        )

    @routes.post("/v1/runs/{run_id}/cancel")
    async def cancel(request):
        run = runs[request.match_info["run_id"]]
        run["status"] = "canceled"
        return web.json_response({k: v for k, v in run.items() if not k.startswith("_")})

    @routes.get("/media/{kind}")
    async def download(request):
        content, mime = media[request.match_info["kind"]]
        return web.Response(body=content, content_type=mime)

    @routes.get("/mock/stats")
    async def stats(request):
        return web.json_response({"submissions": submissions, "paid_generations": 0})

    app = web.Application(client_max_size=128 * 1024**2)
    app.add_routes(routes)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8199)
    parser.add_argument("--delay", type=float, default=0)
    args = parser.parse_args()
    web.run_app(create_app(args.port, args.delay), host="127.0.0.1", port=args.port)
