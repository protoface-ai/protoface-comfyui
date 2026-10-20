"""Validate a public API catalog artifact and update the offline bundle (no secrets/network)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protoface_comfyui.catalog import BUNDLE, atomic_json, digest, parse_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Public /v1/models response or model-catalog snapshot")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    value = json.loads(args.source.read_text())
    models = value.get("models", value.get("data"))
    snapshot = {
        "schema": "protoface.model-catalog.v1",
        "revision": value.get("revision", digest(models)),
        "models": models,
    }
    operations = parse_catalog(snapshot)
    if args.check:
        if digest(models) != digest(json.loads(BUNDLE.read_text())["models"]):
            raise SystemExit("Bundled model catalog drift detected")
    else:
        old = json.loads(BUNDLE.read_text())
        if digest(old["models"]) != digest(models):
            atomic_json(BUNDLE.parent / "history" / (digest(old) + ".json"), old)
        atomic_json(BUNDLE, snapshot)
    print(f"{len(models)} models / {len(operations)} operations validated")


if __name__ == "__main__":
    main()
