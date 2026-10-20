import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMFY_ROOT = os.environ.get("COMFYUI_PATH")
if COMFY_ROOT:
    sys.path.insert(0, COMFY_ROOT)
    from comfy.cli_args import args

    args.cpu = True


@pytest.fixture(autouse=True)
def isolated_comfy_directories(tmp_path, monkeypatch):
    if not COMFY_ROOT:
        return
    import folder_paths

    for kind in ("input", "output", "temp"):
        directory = tmp_path / kind
        directory.mkdir()
        monkeypatch.setattr(folder_paths, f"{kind}_directory", str(directory))


@pytest.fixture
def config(tmp_path):
    from protoface_comfyui.config import Config

    return Config(tmp_path, api_key="mock-secret", poll_interval=0.001)


@pytest.fixture
def journal(config):
    from protoface_comfyui.journal import Journal

    return Journal(config.state_dir)
