"""Native Comfy V3 controls generated from the public request contract."""

from __future__ import annotations

import asyncio
import math
import re

from comfy_api.latest import io

from . import __version__
from .catalog import Catalog, Operation, digest, field_type, nullable, validate_payload
from .client import Client
from .media import Encoded, Reference, encode_media, materialize, preview_result

REFERENCE = io.Custom("PROTOFACE_REFERENCE")
FILE = io.Custom("PROTOFACE_FILE")
DATA = io.Custom("PROTOFACE_DATA")
OMIT = object()
NATIVE = {"image": io.Image, "video": io.Video, "audio": io.Audio}
OUTPUT_TYPES = {
    **NATIVE,
    "mask": io.Mask,
    "text": io.String,
    "data": DATA,
    "model.3d": io.File3DAny,
    "vector": io.SVG,
}
TASKS = {
    "video.general": "Generate Video",
    "video.reference": "Reference to Video",
    "video.extend": "Extend Video",
    "video.edit": "Edit Video",
    "video.motion_control": "Motion Control",
    "video.lipsync": "Lip Sync",
    "video.avatar": "Talking Avatar",
    "video.interpolate": "Interpolate Video",
    "music.generate": "Generate Music",
    "speech.generate": "Text to Speech",
    "speech.convert": "Voice Changer",
    "3d.generate": "Text to 3D",
    "3d.generate.from_image": "Image to 3D",
    "image.remove_background": "Remove Background",
    "image.upscale": "Upscale Image",
    "image.vectorize": "Text to Vector",
    "image.decompose": "Decompose Image",
    "image.generate.utility": "Text to Image Utility",
    "image.generate.turbo": "Text to Image Turbo",
    "video.generate.turbo": "Text to Video Turbo",
    "video.animate.turbo": "Image to Video Turbo",
    "video.edit.turbo": "Edit Video Turbo",
    "image.generate": "Text to Image",
    "image.edit": "Edit Image",
    "video.generate": "Text to Video",
    "video.animate": "Image to Video",
    "video.transition": "Image Transitions",
    "audio.generate": "Generate Audio",
    "audio.speech": "Text to Speech",
}

# Keep this ordering stable for serialized widget values within a node schema revision.
CONTROL_PRIORITY = [
    "prompt",
    "text",
    "first_frame",
    "image",
    "images",
    "video",
    "audio",
    "speech",
    "reference",
    "reference_video",
    "last_frame",
    "quality",
    "resolution",
    "duration_seconds",
    "duration",
    "aspect_ratio",
]


def control_order(fields):
    return sorted(
        fields.items(),
        key=lambda pair: (
            CONTROL_PRIORITY.index(pair[0]) if pair[0] in CONTROL_PRIORITY else len(CONTROL_PRIORITY),
            pair[0],
        ),
    )


def label(value: str) -> str:
    return value.replace("_", " ")


def ordered(values: dict) -> list:
    return [
        value
        for _, value in sorted(
            values.items(),
            key=lambda pair: (
                int(re.search(r"(\d+)$", pair[0]).group(1)) if re.search(r"(\d+)$", pair[0]) else pair[0]
            ),
        )
    ]


def primitive(name: str, schema: dict):
    kind = field_type(schema)
    default = schema.get("default")
    common = {"display_name": label(name), "tooltip": schema.get("description", "")}
    if schema.get("enum_by_quality"):
        choices = "; ".join(
            f"{quality}: {', '.join(map(str, values))}"
            for quality, values in schema["enum_by_quality"].items()
        )
        common["tooltip"] += f"\nAllowed by quality — {choices}."
    enums = [v for v in schema.get("enum", []) if v is not None]
    if enums and kind != "boolean":
        return io.Combo.Input(
            name, options=enums, default=default if default is not None else enums[0], **common
        )
    if kind == "string":
        return io.String.Input(
            name,
            default=default or "",
            multiline="prompt" in name or schema.get("maxLength", 0) > 200,
            dynamic_prompts=False,
            **common,
        )
    if kind == "boolean":
        return io.Boolean.Input(name, default=bool(default), **common)
    if kind in {"integer", "number"}:
        lo, hi = schema.get("minimum"), schema.get("maximum")
        if "exclusiveMinimum" in schema:
            lo = (
                math.floor(schema["exclusiveMinimum"]) + 1
                if kind == "integer"
                else math.nextafter(schema["exclusiveMinimum"], math.inf)
            )
        if "exclusiveMaximum" in schema:
            hi = (
                math.ceil(schema["exclusiveMaximum"]) - 1
                if kind == "integer"
                else math.nextafter(schema["exclusiveMaximum"], -math.inf)
            )
        default = (
            default
            if default is not None
            else max(0, lo)
            if lo is not None
            else min(0, hi)
            if hi is not None
            else 0
        )
        factory = io.Int if kind == "integer" else io.Float
        return factory.Input(
            name,
            default=default,
            min=lo,
            max=hi,
            step=schema.get("multipleOf", 1 if kind == "integer" else 0.1),
            **common,
        )
    raise ValueError(f"Primitive adapter missing for {kind}")


def value_socket(schema: dict):
    return io.Custom("PROTOFACE_VALUE_" + digest(schema)[:16])


class Generator:
    def __init__(self, client: Client):
        self.client = client
        self.helpers: dict[str, type[io.ComfyNode]] = {}

    def helper(self, schema: dict, display_name: str):
        socket = value_socket(schema)
        helper_id = "ProtofaceValue_" + digest(schema)[:16]
        if helper_id in self.helpers:
            return socket
        kind = field_type(schema)
        if kind == "object":
            inputs = [
                self.control(k, s, k in schema.get("required", []), display_name)
                for k, s in schema["properties"].items()
            ]
        else:
            inputs = [primitive("value", schema)]

        def define_schema(cls):
            return io.Schema(
                node_id=helper_id,
                display_name=display_name,
                category="Protoface/Structured Controls",
                inputs=inputs,
                outputs=[socket.Output("value_out")],
                description="Connect one item per shot, transition, or list entry. Controls come from the model schema.",
            )

        def execute(cls, **kwargs):
            if kind == "object":
                result = {}
                for key, child in schema["properties"].items():
                    value = decode_control(key, child, kwargs.get(key, OMIT))
                    if value is not OMIT:
                        result[key] = value
            else:
                result = kwargs["value"]
            return io.NodeOutput(result)

        self.helpers[helper_id] = type(
            helper_id,
            (io.ComfyNode,),
            {"define_schema": classmethod(define_schema), "execute": classmethod(execute)},
        )
        return socket

    def control(self, name: str, schema: dict, required: bool, model_name: str):
        if "const" in schema:
            return io.String.Input(name, default=str(schema["const"]), advanced=True)
        media = schema.get("x-protoface-media")
        if media:
            options = [] if required else [io.DynamicCombo.Option("Default", [])]
            array = field_type(schema) == "array"
            for modality in media["modalities"]:
                socket = NATIVE[modality].Input("value")
                if array:
                    socket = io.Autogrow.Input(
                        "items",
                        template=io.Autogrow.TemplatePrefix(
                            socket,
                            prefix="item_",
                            min=max(1, schema.get("minItems", 0)),
                            max=schema["maxItems"],
                        ),
                    )
                options.append(io.DynamicCombo.Option(modality.title(), [socket]))
            ref = REFERENCE.Input("value")
            if array:
                ref = io.Autogrow.Input(
                    "items",
                    template=io.Autogrow.TemplatePrefix(
                        ref, prefix="item_", min=max(1, schema.get("minItems", 0)), max=schema["maxItems"]
                    ),
                )
            options.append(io.DynamicCombo.Option("References", [ref]))
            if nullable(schema):
                options.append(io.DynamicCombo.Option("Null", []))
            return io.DynamicCombo.Input(
                name, options=options, display_name=label(name), tooltip=schema.get("description")
            )
        kind = field_type(schema)
        if kind in {"array", "object"}:
            child = schema["items"] if kind == "array" else schema
            item_name = {
                "multi_prompt": "Shot",
                "transitions": "Transition",
                "keyframe_positions": "Keyframe Position",
            }.get(name, label(name).title() + " Item")
            helper_label = (
                f"Protoface {model_name} {item_name}"
                if kind == "array"
                else f"Protoface {model_name} {label(name).title()}"
            )
            socket = self.helper(child, helper_label).Input("value")
            if kind == "array":
                socket = io.Autogrow.Input(
                    "items",
                    template=io.Autogrow.TemplatePrefix(
                        socket, prefix="item_", min=max(1, schema.get("minItems", 0)), max=schema["maxItems"]
                    ),
                )
            options = [] if required else [io.DynamicCombo.Option("Default", [])]
            options.append(io.DynamicCombo.Option("Set", [socket]))
            if nullable(schema):
                options.append(io.DynamicCombo.Option("Null", []))
            return io.DynamicCombo.Input(
                name, options=options, display_name=label(name), tooltip=schema.get("description")
            )
        permits_null = nullable(schema)
        if (not required and ("default" not in schema or schema["default"] is None)) or permits_null:
            options = [] if required else [io.DynamicCombo.Option("Default", [])]
            options.append(io.DynamicCombo.Option("Set", [primitive("value", schema)]))
            if permits_null:
                options.append(io.DynamicCombo.Option("Null", []))
            return io.DynamicCombo.Input(
                name, options=options, display_name=label(name), tooltip=schema.get("description")
            )
        return primitive(name, schema)

    def node(self, operation: Operation):
        generator = self
        model_name = operation.model["display_name"]
        inputs = [
            self.control(k, s, k in operation.schema.get("required", []), model_name)
            for k, s in control_order(operation.fields)
        ]
        inputs.extend(
            [
                io.Int.Input(
                    "generation_id",
                    default=0,
                    min=0,
                    max=2**53 - 1,
                    control_after_generate=io.ControlAfterGenerate.fixed,
                    tooltip="Keep fixed to reuse/resume this paid job. Increment for a fresh paid generation; independent of the model seed.",
                ),
                io.String.Input(
                    "request_scope",
                    default="",
                    advanced=True,
                    tooltip="Automatically assigned per node. Headless clients must supply a stable unique workflow/node ID. This is not a secret.",
                ),
            ]
        )
        output_types = []
        for descriptor in operation.outputs:
            modality = descriptor["modality"]
            if modality == "image" and descriptor.get("kind") == "vector":
                modality = "vector"
            if modality not in output_types:
                output_types.append(modality)
                if modality == "image":
                    output_types.append("mask")
        outputs = [
            OUTPUT_TYPES[m].Output(f"{m}_out", display_name=m, is_output_list=True) for m in output_types
        ]
        outputs.extend(
            [
                FILE.Output("files_out", display_name="files", is_output_list=True),
                io.String.Output("run_id_out", display_name="run id"),
            ]
        )

        def define_schema(cls):
            prefix = "" if model_name.lower().startswith("protoface") else "Protoface "
            task = TASKS.get(operation.name, label(operation.name.replace(".", " ")).title())
            return io.Schema(
                node_id=operation.node_id,
                display_name=f"{prefix}{model_name} · {task}"
                + (
                    " [Archived]"
                    if operation.retired
                    else " [Unavailable]"
                    if not operation.model.get("available", True)
                    else ""
                ),
                category=f"Protoface/{operation.name.split('.')[0].title()}/{operation.model['owner']}",
                description=operation.model.get("description", "")
                + "\nPaid generation through your Protoface account. Keep generation_id fixed to reuse/resume; increment to generate again.",
                inputs=inputs,
                outputs=outputs,
                hidden=[io.Hidden.unique_id],
                search_aliases=[
                    operation.model["id"],
                    operation.model["family"],
                    operation.name,
                    *operation.model.get("aliases", []),
                    *operation.model.get("tags", []),
                ],
                is_output_node=True,
                not_idempotent=True,
                is_deprecated=operation.retired,
            )

        def fingerprint_inputs(cls, **kwargs):
            return generator.client.config.namespace if generator.client.config.api_key else "unconfigured"

        async def execute(cls, generation_id=0, request_scope="", **kwargs):
            client = generator.client.snapshot()
            async with client.pipeline_semaphore:
                return await execute_request(cls, client, generation_id, request_scope, **kwargs)

        async def execute_request(cls, client, generation_id, request_scope, **kwargs):
            if operation.retired:
                raise RuntimeError(
                    "This saved model contract is archived. Use Resume Run for existing results, or select the current model node before creating another paid job."
                )
            if not operation.model.get("available", True):
                raise RuntimeError(
                    "This model is currently unavailable through Protoface. Refresh the catalog after it becomes available; use Resume Run for existing results."
                )
            if not request_scope.strip():
                raise ValueError(
                    "Protoface request_scope is missing. Reload the frontend and recreate this node, or supply a stable scope in a headless workflow."
                )
            payload = {"operation": operation.name}
            encoded_fields = {}
            identities = {}
            for name, schema in operation.fields.items():
                value = decode_control(name, schema, kwargs.get(name, OMIT))
                if value is OMIT:
                    continue
                if value is None:
                    payload[name] = None
                    continue
                if schema.get("x-protoface-media"):
                    items = value if isinstance(value, list) else [value]
                    encoded = []
                    for item in items:
                        modality = item.modality if isinstance(item, Reference) else media_mode(kwargs[name])
                        if modality not in schema["x-protoface-media"]["modalities"]:
                            raise ValueError(f"{name}: reference modality is unsupported by this model.")
                        encoded.extend(await asyncio.to_thread(encode_media, item, modality))
                    if field_type(schema) != "array" and len(encoded) != 1:
                        raise ValueError(f"{name}: expects one asset; split the image/audio batch first.")
                    if len(encoded) > schema.get("maxItems", 1):
                        raise ValueError(f"{name}: too many references, including items in input batches.")
                    encoded_fields[name] = encoded
                    identities[name] = [x.identity if isinstance(x, Encoded) else x.value for x in encoded]
                    payload[name] = (
                        [x.value if isinstance(x, Reference) else "asset_pending" for x in encoded]
                        if field_type(schema) == "array"
                        else encoded[0].value
                        if isinstance(encoded[0], Reference)
                        else "asset_pending"
                    )
                else:
                    payload[name] = value
            validate_payload(operation, payload)  # Validate before upload, quoting, or paid submission.
            intent_payload = dict(payload)
            intent_payload.update(identities)
            node_id = getattr(getattr(cls, "hidden", None), "unique_id", "headless")
            intent = digest(
                {
                    "account": client.config.namespace,
                    "scope": request_scope,
                    "node": node_id,
                    "generation": generation_id,
                    "model": operation.model["id"],
                    "schema": operation.fingerprint,
                    "payload": intent_payload,
                }
            )
            if not client.journal.intent(intent)["run_id"]:
                for name, encoded in encoded_fields.items():
                    values = [
                        x.value
                        if isinstance(x, Reference)
                        else await client.upload(x.content, x.modality, x.filename, x.content_type)
                        for x in encoded
                    ]
                    payload[name] = values if field_type(operation.fields[name]) == "array" else values[0]
            payload["metadata"] = {
                "integration": "comfyui",
                "integration_version": __version__,
                "schema_fingerprint": operation.fingerprint,
            }
            from comfy.model_management import throw_exception_if_processing_interrupted
            from comfy.utils import ProgressBar

            progress = ProgressBar(100)
            throw_exception_if_processing_interrupted()
            view = await client.generate(
                operation.model["id"],
                payload,
                intent,
                progress=lambda p: progress.update_absolute(min(100, max(0, p))),
                interrupt=throw_exception_if_processing_interrupted,
            )
            result = await materialize(client, view)
            preview = await preview_result(result, view["id"])
            return io.NodeOutput(*(result[m] for m in output_types), result["files"], view["id"], ui=preview)

        return type(
            operation.node_id,
            (io.ComfyNode,),
            {
                "define_schema": classmethod(define_schema),
                "execute": classmethod(execute),
                "fingerprint_inputs": classmethod(fingerprint_inputs),
            },
        )

    def generate(self, catalog: Catalog) -> list[type[io.ComfyNode]]:
        nodes = [self.node(operation) for operation in catalog.operations]
        return nodes + list(self.helpers.values())


def media_mode(value: dict) -> str:
    return next(
        value[k].lower()
        for k in value
        if isinstance(value[k], str) and value[k] in {"Image", "Video", "Audio"}
    )


def decode_control(name: str, schema: dict, value):
    if "const" in schema:
        return schema["const"]
    if value is OMIT:
        return OMIT
    if isinstance(value, dict) and name in value:
        mode = value[name]
        if mode == "Default":
            return OMIT
        if mode == "Null":
            return None
        if "items" in value:
            return ordered(value["items"])
        return value.get("value", OMIT)
    return value
