"""Check release metadata and build the full ComfyUI source ZIP without publishing."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from packaging.requirements import Requirement
from pathspec import GitIgnoreSpec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_public_content import inspect, source_files

ROOT = Path(__file__).resolve().parents[1]
CLI_VERSION = "1.22.0"
REPOSITORY = "https://github.com/protoface-ai/protoface-comfyui"
REQUIRED = {
    "__init__.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "web/protoface.js",
    "protoface_comfyui/__init__.py",
    "protoface_comfyui/extension.py",
    "protoface_comfyui/data/catalog.json",
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE).decode().strip()


def notes_for(root: Path, version: str) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}(?: \([^\n]*\))?\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match[1].strip():
        raise ValueError("Add a non-empty CHANGELOG entry for the package version")
    return match[1].strip()


def check(root: Path, tag: str | None = None, publisher_id: str | None = None) -> dict:
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project, comfy = config["project"], config["tool"]["comfy"]
    version = project["version"]
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Use a three-part semantic package version")
    module = ast.parse((root / "protoface_comfyui/__init__.py").read_text(encoding="utf-8"))
    runtime_version = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
    )
    if runtime_version != version:
        raise ValueError("pyproject.toml and adapter __version__ must match")
    requirements = {
        str(Requirement(line.strip()))
        for line in (root / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if requirements != {str(Requirement(item)) for item in project["dependencies"]}:
        raise ValueError("requirements.txt and project dependencies must match")
    if project["name"] != "protoface-nodes" or project["urls"]["Repository"] != REPOSITORY:
        raise ValueError("Confirm the permanent Registry package ID and repository URL")
    if comfy.get("includes"):
        raise ValueError("Release packaging accepts tracked files only; remove force-includes")
    if publisher_id is not None and (not publisher_id or comfy.get("PublisherId") != publisher_id):
        raise ValueError("Set the registered PublisherId in pyproject.toml and the repository variable")
    notes_for(root, version)
    if tag is not None:
        if tag != f"v{version}":
            raise ValueError("Release tag must be v followed by the exact package version")
        if git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}") != git(root, "rev-parse", "HEAD"):
            raise ValueError("The checked-out commit must be the release tag")
        subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"], cwd=root, check=True)
        if git(root, "diff", "HEAD", "--name-only"):
            raise ValueError("Release source must have no tracked changes")
    return config


def build(root: Path, output: Path, config: dict, verify_cli: bool = False) -> Path:
    if git(root, "diff", "HEAD", "--name-only"):
        raise ValueError("Commit tracked changes before building a release candidate")
    issues = [issue for name, content in source_files(root) for issue in inspect(name, content)]
    if issues:
        raise ValueError("Public-content check failed (values withheld):\n" + "\n".join(issues))
    tracked = [name for name in git(root, "ls-files", "-z").split("\0") if name]
    ignore = GitIgnoreSpec.from_lines((root / ".comfyignore").read_text().splitlines())
    names = sorted(name for name in tracked if not ignore.match_file(name))
    if not REQUIRED.issubset(names) or not any(
        name.startswith("example_workflows/") and name.endswith(".json") for name in names
    ):
        raise ValueError(
            "The source ZIP must include entrypoint, frontend, catalog, dependencies and templates"
        )
    if any((root / name).is_symlink() or not (root / name).is_file() for name in names):
        raise ValueError("Release files must be ordinary tracked files, without symlinks")
    version = config["project"]["version"]
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"protoface-nodes-{version}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / name).read_bytes())
    with zipfile.ZipFile(archive_path) as archive:
        issues = [issue for name in archive.namelist() for issue in inspect(name, archive.read(name))]
        if issues:
            raise ValueError("Release archive content check failed (values withheld):\n" + "\n".join(issues))
        if verify_cli:
            if importlib.metadata.version("comfy-cli") != CLI_VERSION:
                raise ValueError(f"Registry packaging verification requires comfy-cli=={CLI_VERSION}")
            from comfy_cli.file_utils import zip_files
            from comfy_cli.registry.config_parser import extract_node_configuration

            registry_config = extract_node_configuration(str(root / "pyproject.toml"))
            if registry_config is None or (
                registry_config.project.name != config["project"]["name"]
                or registry_config.project.version != version
                or registry_config.tool_comfy.publisher_id != config["tool"]["comfy"].get("PublisherId", "")
                or registry_config.project.supported_comfyui_version
                != config["tool"]["comfy"]["requires-comfyui"]
                or set(registry_config.project.dependencies) != set(config["project"]["dependencies"])
            ):
                raise ValueError(
                    "Registry CLI must read the same package identity, compatibility and dependencies"
                )

            with tempfile.TemporaryDirectory() as temporary:
                official = Path(temporary) / "registry.zip"
                # The pinned CLI packages the current directory. No Registry API is called.
                previous = Path.cwd()
                import os

                try:
                    os.chdir(root)
                    zip_files(str(official))
                finally:
                    os.chdir(previous)
                with zipfile.ZipFile(official) as registry_archive:
                    if set(registry_archive.namelist()) != set(names) or any(
                        registry_archive.read(name) != archive.read(name) for name in names
                    ):
                        raise ValueError("Source ZIP differs from the pinned Registry CLI package contents")
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{checksum}  {archive_path.name}\n", encoding="utf-8")
    notes = f"# Protoface for ComfyUI {version}\n\nSource commit: `{git(root, 'rev-parse', 'HEAD')}`\n\n"
    notes += notes_for(root, version) + "\n\n"
    notes += "Install the ZIP contents in `ComfyUI/custom_nodes/protoface`, install `requirements.txt` with ComfyUI's Python, then restart. This is the full custom-node source package, not the shared-adapter wheel.\n"
    (output / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "build"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tag")
    parser.add_argument("--publisher-id")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--verify-cli", action="store_true")
    args = parser.parse_args()
    try:
        config = check(args.root, args.tag, args.publisher_id)
        if args.command == "build":
            path = build(args.root, args.output_dir, config, args.verify_cli)
            print(f"Built full ComfyUI source package: {path.name}")
        else:
            print(f"Release metadata valid: {config['project']['name']} {config['project']['version']}")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
