import io
import os

import httpx
import numpy as np
import pytest
import torch
from PIL import Image

from protoface_comfyui.client import Client
from protoface_comfyui.media import Reference, decode_audio, encode_media


def test_image_batches_and_reference_modality():
    encoded = encode_media(torch.zeros((2, 8, 12, 3)), "image")
    assert len(encoded) == 2
    with Image.open(io.BytesIO(encoded[0].content)) as image:
        assert image.size == (12, 8)
    with pytest.raises(ValueError, match="Expected image"):
        encode_media(Reference("asset_x", "audio"), "image")


def test_audio_native_roundtrip(tmp_path):
    waveform = torch.tensor(np.sin(np.arange(2400) / 20), dtype=torch.float32).reshape(1, 1, -1)
    encoded = encode_media({"waveform": waveform, "sample_rate": 24000}, "audio")[0]
    target = tmp_path / "a.wav"
    target.write_bytes(encoded.content)
    decoded = decode_audio(target)
    assert decoded["sample_rate"] == 24000
    assert decoded["waveform"].shape == waveform.shape
    assert torch.max(torch.abs(decoded["waveform"] - waveform)) < 0.0001


@pytest.mark.skipif(not os.environ.get("COMFYUI_PATH"), reason="Native ComfyUI video type")
def test_video_native_roundtrip(tmp_path):
    from fractions import Fraction

    from comfy_api.latest import InputImpl, Types

    video = InputImpl.VideoFromComponents(
        Types.VideoComponents(images=torch.zeros((3, 16, 16, 3)), frame_rate=Fraction(3))
    )
    encoded = encode_media(video, "video")[0]
    target = tmp_path / "a.mp4"
    target.write_bytes(encoded.content)
    decoded = InputImpl.VideoFromFile(str(target))
    assert abs(decoded.get_duration() - 1) < 0.01
    assert decoded.get_components().images.shape == (3, 16, 16, 3)


@pytest.mark.skipif(not os.environ.get("COMFYUI_PATH"), reason="Native outputs")
async def test_all_results_heterogeneous_images_svg_mesh_text(config, journal):
    from protoface_comfyui.media import materialize

    contents = {}
    outputs = []
    for index, (size, mode, color) in enumerate([((8, 8), "RGB", "red"), ((12, 16), "RGBA", (0, 0, 0, 0))]):
        image = io.BytesIO()
        Image.new(mode, size, color).save(image, "PNG")
        contents[f"/image{index}"] = image.getvalue()
        outputs.append(
            {
                "type": "asset",
                "modality": "image",
                "asset_id": f"asset_{index}",
                "file": {"url": f"https://media.example/image{index}", "content_type": "image/png"},
            }
        )
    contents["/vector"] = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    contents["/mesh"] = b"glTF"
    contents["/text"] = b"transcript"
    for path, modality, content_type in [
        ("vector", "image", "image/svg+xml"),
        ("mesh", "model.3d", "model/gltf-binary"),
        ("text", "text", "text/plain"),
    ]:
        outputs.append(
            {
                "type": "asset",
                "modality": modality,
                "file": {"url": f"https://media.example/{path}", "content_type": content_type},
            }
        )
    client = Client(
        config,
        journal,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=contents[r.url.path])),
    )
    result = await materialize(client, {"id": "run_x", "outputs": outputs})
    assert [image.shape for image in result["image"]] == [(1, 8, 8, 3), (1, 16, 12, 3)]
    assert len(result["files"]) == 5
    assert result["vector"][0].data[0].getvalue() == contents["/vector"]
    assert len(result["model.3d"]) == 1
    assert result["text"] == ["transcript"]


def test_result_suffixes_do_not_depend_on_platform_mime_database():
    from protoface_comfyui.media import result_extension

    assert result_extension({"content_type": "model/gltf-binary"}, "model.3d") == ".glb"
    assert (
        result_extension(
            {"content_type": "application/octet-stream", "file_name": "../model.obj"}, "model.3d"
        )
        == ".obj"
    )
    assert (
        result_extension(
            {"content_type": "application/octet-stream", "file_name": "../program.exe"}, "model.3d"
        )
        == ".bin"
    )
