"""ComfyUI registration and deliberately small operator settings API."""

from __future__ import annotations

from urllib.parse import urlsplit

from aiohttp import web
from comfy_api.latest import ComfyExtension, io

from . import __version__
from .catalog import Catalog
from .client import Client
from .config import Config
from .journal import Journal
from .media import Artifact, Reference, materialize, preview_result
from .nodes import DATA, FILE, REFERENCE, Generator


class Extension(ComfyExtension):
    def __init__(self):
        self.config = Config.from_env()
        self.journal = Journal(self.config.state_dir)
        self.client = Client(self.config, self.journal)
        self.catalog = Catalog(self.config.state_dir)
        self.nodes = Generator(self.client).generate(self.catalog)
        self.nodes.extend(self.utilities())

    async def on_load(self):
        from server import PromptServer

        routes = PromptServer.instance.routes

        @routes.get("/protoface/settings")
        async def settings(request):
            return web.json_response(
                {
                    "configured": bool(self.config.api_key),
                    "api_base_url": self.config.base_url,
                    "version": __version__,
                    "catalog_revision": self.catalog.revision,
                    "active_nodes": sum(not op.retired for op in self.catalog.operations),
                    "cache_error": self.catalog.cache_error,
                }
            )

        @routes.post("/protoface/credentials")
        async def credentials(request):
            reject_cross_origin(request)
            body = await request.json()
            key = body.get("api_key", "")
            if not isinstance(key, str) or len(key) > 4096 or "\n" in key or "\r" in key:
                raise web.HTTPBadRequest(text="Invalid key")
            # Memory-only: headless/persistent credentials belong in operator env or key file.
            self.config.api_key = key.strip()
            return web.json_response({"configured": bool(self.config.api_key)})

        @routes.post("/protoface/catalog/refresh")
        async def refresh(request):
            reject_cross_origin(request)
            try:
                # Offline browsing uses the bundle; live discovery uses the operator's account.
                value = await self.client.discover()
                result = self.catalog.save_refresh(value)
            except (ValueError, KeyError, RuntimeError) as error:
                raise web.HTTPBadRequest(text=str(error)) from None
            return web.json_response(result)

    async def get_node_list(self):
        return self.nodes

    def utilities(self):
        extension = self

        class AssetReference(io.ComfyNode):
            @classmethod
            def define_schema(cls):
                return io.Schema(
                    node_id="ProtofaceAssetReference",
                    display_name="Protoface Asset Reference",
                    category="Protoface/Utilities",
                    inputs=[
                        io.String.Input(
                            "reference",
                            default="",
                            tooltip="Existing Protoface asset ID or public media URL. Connect to a model's References mode.",
                        ),
                        io.Combo.Input("modality", options=["image", "video", "audio"]),
                    ],
                    outputs=[REFERENCE.Output("reference_out")],
                )

            @classmethod
            def execute(cls, reference, modality):
                if not reference.strip():
                    raise ValueError("Supply an asset ID or public URL.")
                return io.NodeOutput(Reference(reference.strip(), modality))

        class ResultReference(io.ComfyNode):
            @classmethod
            def define_schema(cls):
                return io.Schema(
                    node_id="ProtofaceResultReference",
                    display_name="Protoface Reuse Result Asset",
                    category="Protoface/Utilities",
                    inputs=[FILE.Input("file")],
                    outputs=[REFERENCE.Output("reference_out")],
                    description="Chain a Protoface result into another Protoface node without reuploading the media. Select References on the destination input.",
                )

            @classmethod
            def execute(cls, file: Artifact):
                if not file.asset_id or file.modality not in {"image", "video", "audio"}:
                    raise ValueError("This result cannot be reused as a media reference.")
                return io.NodeOutput(Reference(file.asset_id, file.modality))

        class ResumeRun(io.ComfyNode):
            @classmethod
            def define_schema(cls):
                return io.Schema(
                    node_id="ProtofaceResumeRun",
                    display_name="Protoface Resume Run",
                    category="Protoface/Utilities",
                    inputs=[io.String.Input("run_id", default="")],
                    outputs=[
                        io.Image.Output("images", is_output_list=True),
                        io.Video.Output("videos", is_output_list=True),
                        io.Audio.Output("audio", is_output_list=True),
                        io.String.Output("text", is_output_list=True),
                        DATA.Output("data", is_output_list=True),
                        FILE.Output("files", is_output_list=True),
                    ],
                    is_output_node=True,
                    not_idempotent=True,
                    description="Retrieve an existing job by run ID. This node never submits a new paid generation.",
                )

            @classmethod
            async def execute(cls, run_id):
                from comfy.model_management import throw_exception_if_processing_interrupted

                client = extension.client.snapshot()
                view = await client.wait(run_id.strip(), interrupt=throw_exception_if_processing_interrupted)
                result = await materialize(client, view)
                return io.NodeOutput(
                    *(result[m] for m in ("image", "video", "audio", "text", "data", "files")),
                    ui=await preview_result(result, view["id"]),
                )

        class SaveArtifact(io.ComfyNode):
            @classmethod
            def define_schema(cls):
                return io.Schema(
                    node_id="ProtofaceSaveFile",
                    display_name="Protoface Save File",
                    category="Protoface/Utilities",
                    inputs=[FILE.Input("file"), io.String.Input("filename_prefix", default="Protoface")],
                    outputs=[io.String.Output("path")],
                    is_output_node=True,
                    description="Save original SVG, mesh, transcript or media bytes to the ComfyUI output directory.",
                )

            @classmethod
            def execute(cls, file: Artifact, filename_prefix):
                import re
                import shutil
                import uuid
                from pathlib import Path

                import folder_paths

                prefix = re.sub(r"[^A-Za-z0-9_-]", "_", filename_prefix)[:100] or "Protoface"
                source = Path(file.path)
                target = (
                    Path(folder_paths.get_output_directory())
                    / f"{prefix}_{uuid.uuid4().hex[:10]}{source.suffix}"
                )
                shutil.copyfile(source, target)
                return io.NodeOutput(str(target), ui={"text": [str(target)]})

        return [AssetReference, ResultReference, ResumeRun, SaveArtifact]


def reject_cross_origin(request):
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden(text="Cross-origin operator settings refused")
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Settings require JSON")
