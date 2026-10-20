"""Check source and release archives without printing potentially sensitive contents."""

from __future__ import annotations

import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

SKIP = {".git", ".venv", ".pytest_cache", ".ruff_cache", "__pycache__", "dist", ".local", ".comfy-test"}
PATTERNS = {
    "credential": re.compile(
        rb"(?:sk_(?:live|test)_[A-Za-z0-9]{16,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
        rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
    "machine path": re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "cloud resource identifier": re.compile(rb"arn:aws:[a-z0-9-]+:[a-z0-9-]*:[0-9]{12}:[^\s\"']+"),
    "non-public deployment URL": re.compile(
        rb"https?://(?:[a-z0-9-]+\.)*(?:dev|staging|internal)\.[a-z0-9.-]+", re.I
    ),
}


def inspect(name: str, content: bytes) -> list[str]:
    path = Path(name)
    issues = []
    if path.name.startswith(".env") or path.suffix in {".key", ".sqlite", ".sqlite3", ".db", ".pem"}:
        issues.append(f"{name}: private state/credential file")
    if ".git" in path.parts or ".venv" in path.parts:
        issues.append(f"{name}: development environment/history")
    issues.extend(f"{name}: {label}" for label, pattern in PATTERNS.items() if pattern.search(content))
    return issues


def source_files(root: Path):
    if (root / ".git").exists():
        names = (
            subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
            )
            .decode()
            .split("\0")
        )
        paths = [root / name for name in names if name]
    else:
        paths = root.rglob("*")
    for path in paths:
        if path.is_file() and not any(part in SKIP for part in path.relative_to(root).parts):
            yield str(path.relative_to(root)), path.read_bytes()


def archive_files(path: Path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    yield name, archive.read(name)
    else:
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    yield member.name, archive.extractfile(member).read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path, action="append", default=[])
    args = parser.parse_args()
    issues = []
    count = 0
    collections = [source_files(args.root)] + [archive_files(path) for path in args.archive]
    for entries in collections:
        for name, content in entries:
            count += 1
            issues.extend(inspect(name, content))
    if issues:
        raise SystemExit("Public-content check failed (values withheld):\n" + "\n".join(issues))
    print(f"Public-content check passed for {count} files. Images and Git history need separate review.")


if __name__ == "__main__":
    main()
