"""Emit headless prompts from the same node IDs and native dynamic input definitions."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.environ.get("COMFYUI_PATH"):
    sys.path.insert(0, os.environ["COMFYUI_PATH"])
from comfy.cli_args import args

args.cpu = True
from comfy_api.latest import io

from protoface_comfyui.catalog import Catalog, atomic_json
from protoface_comfyui.client import Client
from protoface_comfyui.config import Config
from protoface_comfyui.journal import Journal
from protoface_comfyui.nodes import Generator


def flat_defaults(inputs, prefix=""):
    values = {}
    for field in inputs:
        name = prefix + field.id
        if isinstance(field, io.DynamicCombo.Input):
            values[name] = field.options[0].key
            values.update(flat_defaults(field.options[0].inputs, name + "."))
        elif isinstance(field, io.Autogrow.Input):
            continue
        else:
            values[name] = field.default
    return values


def main():
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        config = Config(Path(directory))
        generator = Generator(Client(config, Journal(config.state_dir)))
        catalog = Catalog(config.state_dir)
        operations = {(o.model["id"], o.name): o for o in catalog.operations}

        def generate(model, operation, scope, overrides):
            op = operations[model, operation]
            node = generator.node(op)
            values = flat_defaults(node.define_schema().inputs)
            values.update({"request_scope": scope, **overrides})
            return {"class_type": op.node_id, "inputs": values}

        h3 = generate(
            "minimax/minimax-h3",
            "video.general",
            "protoface-example-h3-v1",
            {
                "prompt": "A slow camera move through a misty forest at sunrise, cinematic natural light.",
                "quality": "480p",
            },
        )
        atomic_json(ROOT / "examples" / "api" / "h3-text-to-video.api.json", {"generate": h3})
        model = next(o.model["id"] for o in catalog.operations if "ltx-2.5" in o.model["id"])
        op = next(o for o in catalog.operations if o.model["id"] == model)
        image_field = next(
            k for k, s in op.fields.items() if s.get("x-protoface-media", {}).get("modalities") == ["image"]
        )
        ltx = generate(
            model,
            op.name,
            "protoface-example-ltx-v1",
            {
                "prompt": "A smooth camera orbit around the subject, preserving its identity.",
                image_field: "Image",
                image_field + ".value": ["load", 0],
            },
        )
        atomic_json(
            ROOT / "examples" / "api" / "ltx-image-to-video.api.json",
            {"load": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}, "generate": ltx},
        )
        qwen = generate(
            "alibaba/qwen-image-3.0",
            "image.generate",
            "protoface-example-image-v1",
            {
                "prompt": "A cinematic close-up of a handcrafted ceramic cup on a wooden table, morning window light."
            },
        )
        h3_chain = generate(
            "minimax/minimax-h3",
            "video.general",
            "protoface-example-chain-v1",
            {
                "prompt": "A slow cinematic camera push toward the cup. Soft steam rises.",
                "first_frame": "References",
                "first_frame.value": ["reuse", 0],
            },
        )
        qwen_op = operations["alibaba/qwen-image-3.0", "image.generate"]
        output_count = len(generator.node(qwen_op).define_schema().outputs)
        atomic_json(
            ROOT / "examples" / "api" / "image-to-video-reuse.api.json",
            {
                "image": qwen,
                "reuse": {
                    "class_type": "ProtofaceResultReference",
                    "inputs": {"file": ["image", output_count - 2]},
                },
                "video": h3_chain,
            },
        )
        atomic_json(
            ROOT / "examples" / "api" / "resume-run.api.json",
            {
                "resume": {
                    "class_type": "ProtofaceResumeRun",
                    "inputs": {"run_id": "REPLACE_WITH_YOUR_RUN_ID"},
                }
            },
        )
        for template in (ROOT / "example_workflows").glob("*.json"):
            graph = json.loads(template.read_text())
            for node in graph["nodes"]:
                props = node.get("properties", {})
                if "protoface_model" not in props:
                    continue
                op = operations[props["protoface_model"], props["protoface_operation"]]
                if props["protoface_template_schema"] != op.fingerprint or node["type"] != op.node_id:
                    raise ValueError(
                        f"{template.name}: model controls changed; re-export and review the GUI template"
                    )
        print("Generated four headless examples and checked native GUI template identities")


if __name__ == "__main__":
    main()
