"""Fetch customer-safe model metadata using operator credentials; never submit a generation."""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protoface_comfyui.catalog import atomic_json, parse_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://api.protoface.com/v1/models")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise SystemExit("Public catalog URL must use HTTPS or a localhost fixture")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("Catalog URL must not contain credentials, query strings or fragments")
    key = os.environ.get("PROTOFACE_API_KEY", "").strip()
    if not key and os.environ.get("PROTOFACE_API_KEY_FILE"):
        key = Path(os.environ["PROTOFACE_API_KEY_FILE"]).read_text().strip()
    if not key:
        raise SystemExit("Set PROTOFACE_API_KEY or PROTOFACE_API_KEY_FILE for live catalog discovery")
    with httpx.Client(
        timeout=30, follow_redirects=False, headers={"Authorization": f"Bearer {key}"}
    ) as client:
        response = client.get(args.url)
    if not response.is_success:
        raise SystemExit(f"Catalog request failed (HTTP {response.status_code})")
    value = response.json()
    operations = parse_catalog(value)
    atomic_json(args.output, value)
    print(f"Fetched and validated {len(operations)} public model tasks")


if __name__ == "__main__":
    main()
