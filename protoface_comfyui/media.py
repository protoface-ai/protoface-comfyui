"""Conversions at the Comfy boundary; REST and schemas stay independent of Torch."""

from __future__ import annotations

import hashlib
import io as bytesio
import mimetypes
import wave
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
import torch
from PIL import Image, ImageOps

from .client import Client

MEDIA_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "model/gltf-binary": ".glb",
    "model/gltf+json": ".gltf",
    "model/obj": ".obj",
    "model/stl": ".stl",
    "text/plain": ".txt",
    "application/json": ".json",
}


def result_extension(file: dict, modality: str) -> str:
    mime = (file.get("content_type") or "").split(";", 1)[0].strip().lower()
    if mime in MEDIA_EXTENSIONS:
        return MEDIA_EXTENSIONS[mime]
    # Use only a recognized suffix, never a provider-supplied path or filename.
    suffix = Path(file.get("file_name") or "").suffix.lower()
    allowed = {
        "model.3d": {".glb", ".gltf", ".obj", ".stl", ".ply", ".fbx"},
        "video": {".mp4", ".mov", ".webm", ".mkv"},
        "audio": {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac"},
        "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".tif", ".tiff"},
        "text": {".txt", ".srt", ".vtt"},
    }
    return suffix if suffix in allowed.get(modality, set()) else mimetypes.guess_extension(mime) or ".bin"


@dataclass(frozen=True)
class Reference:
    value: str
    modality: str


@dataclass(frozen=True)
class Encoded:
    content: bytes
    modality: str
    filename: str
    content_type: str

    @property
    def identity(self) -> str:
        return self.modality + ":" + hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class Artifact:
    path: str
    modality: str
    content_type: str
    asset_id: str | None = None


def encode_media(value, modality: str) -> list[Encoded | Reference]:
    if isinstance(value, Reference):
        if value.modality != modality:
            raise ValueError(f"Expected {modality} reference; got {value.modality}.")
        return [value]
    if modality == "image":
        if not isinstance(value, torch.Tensor) or value.ndim != 4 or value.shape[-1] not in {1, 3, 4}:
            raise ValueError(
                "Image input must be a native ComfyUI IMAGE tensor [batch,height,width,channels]."
            )
        images = []
        for item in value:
            array = np.clip(item.detach().cpu().numpy() * 255, 0, 255).round().astype(np.uint8)
            if array.shape[-1] == 1:
                array = array[..., 0]
            output = bytesio.BytesIO()
            Image.fromarray(array).save(output, format="PNG")
            images.append(Encoded(output.getvalue(), "image", "reference.png", "image/png"))
        return images
    if modality == "audio":
        waveform = value["waveform"]
        rate = int(value["sample_rate"])
        if waveform.ndim != 3 or not 1 <= waveform.shape[1] <= 2 or rate <= 0:
            raise ValueError(
                "Audio must be [batch,channels,samples], mono/stereo, with a positive sample_rate."
            )
        results = []
        for sample in waveform:
            pcm = np.clip(sample.detach().cpu().numpy().T, -1, 1)
            output = bytesio.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(sample.shape[0])
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes((pcm * 32767).round().astype("<i2").tobytes())
            results.append(Encoded(output.getvalue(), "audio", "reference.wav", "audio/wav"))
        return results
    if modality == "video":
        from comfy_api.latest import Types

        output = bytesio.BytesIO()
        value.save_to(output, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
        return [Encoded(output.getvalue(), "video", "reference.mp4", "video/mp4")]
    raise ValueError(f"No native adapter for {modality}.")


def decode_image(path: Path) -> tuple[torch.Tensor, torch.Tensor]:
    with Image.open(path) as source:
        oriented = ImageOps.exif_transpose(source)
        image = oriented.convert("RGB")
        rgba = oriented.convert("RGBA")
        mask = 1.0 - torch.from_numpy(np.array(rgba.getchannel("A")).astype(np.float32) / 255.0)
        return torch.from_numpy(np.array(image).astype(np.float32) / 255.0).unsqueeze(0), mask.unsqueeze(0)


def decode_audio(path: Path) -> dict:
    frames = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        rate = stream.codec_context.sample_rate
        layout = "mono" if stream.codec_context.channels == 1 else "stereo"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                frames.append(converted.to_ndarray())
        for converted in resampler.resample(None):
            frames.append(converted.to_ndarray())
    if not frames:
        raise RuntimeError("Protoface returned an empty audio file.")
    return {"waveform": torch.from_numpy(np.concatenate(frames, axis=1)).unsqueeze(0), "sample_rate": rate}


async def materialize(client: Client, view: dict) -> dict[str, list]:
    import asyncio

    from comfy_api.latest import InputImpl, Types, io

    result = {
        key: [] for key in ("image", "mask", "video", "audio", "text", "data", "model.3d", "vector", "files")
    }
    for index, output in enumerate(view.get("outputs", [])):
        modality = output["modality"]
        if output["type"] == "text":
            result["text"].append(output.get("text") or "")
            continue
        if output["type"] == "data":
            result["data"].append(output.get("data"))
            continue
        file = output.get("file")
        if not file or not file.get("url"):
            raise RuntimeError(f"Run {view['id']} output {index} is missing a downloadable file.")
        content_type = file.get("content_type") or "application/octet-stream"
        extension = result_extension(file, modality)
        # API-controlled names and IDs are never allowed to form paths.
        key = hashlib.sha256(f"{view['id']}:{index}:{output.get('asset_id')}".encode()).hexdigest()
        target = client.config.state_dir / "results" / (key + extension)
        if not target.exists():
            await client.download(file["url"], target)
        artifact = Artifact(str(target), modality, content_type, output.get("asset_id"))
        result["files"].append(artifact)
        if modality == "image" and content_type.split(";")[0] != "image/svg+xml":
            image, mask = await asyncio.to_thread(decode_image, target)
            result["image"].append(image)
            result["mask"].append(mask)
        elif modality == "image":
            result["vector"].append(io.SVG.Type([bytesio.BytesIO(target.read_bytes())]))
        elif modality == "model.3d":
            result["model.3d"].append(Types.File3D(str(target)))
        elif modality == "video":
            result["video"].append(InputImpl.VideoFromFile(str(target)))
        elif modality == "audio":
            result["audio"].append(await asyncio.to_thread(decode_audio, target))
        elif modality == "text":
            result["text"].append(await asyncio.to_thread(target.read_text, encoding="utf-8"))
        elif modality == "data":
            result["data"].append(artifact)
    return result


async def preview_result(result: dict, run_id: str) -> dict:
    import asyncio
    import shutil

    import folder_paths
    from comfy_api.latest import io, ui

    preview = {"protoface_run_id": [run_id]}
    for image in result["image"]:
        saved = await asyncio.to_thread(lambda: ui.PreviewImage(image).as_dict())
        preview.setdefault("images", []).extend(saved["images"])
    for audio in result["audio"]:
        saved = await asyncio.to_thread(lambda: ui.PreviewAudio(audio).as_dict())
        preview.setdefault("audio", []).extend(saved["audio"])
    videos = []
    for artifact in result["files"]:
        if artifact.modality != "video":
            continue
        source = Path(artifact.path)
        target = Path(folder_paths.get_temp_directory()) / ("protoface_" + source.name)
        if not target.exists():
            await asyncio.to_thread(shutil.copyfile, source, target)
        videos.append(ui.SavedResult(target.name, "", io.FolderType.temp))
    if videos:
        preview.setdefault("images", []).extend(videos)
        preview["animated"] = (True,)
    return preview
