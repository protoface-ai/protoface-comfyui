import shutil
import subprocess
import zipfile

import pytest

from scripts.release import ROOT, build, check


@pytest.fixture
def release_source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for name in names:
        if name and (ROOT / name).is_file():
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
    for args in [
        ["init", "-b", "master"],
        ["config", "user.name", "release-test"],
        ["config", "user.email", "release-test@example.com"],
        ["add", "."],
        ["commit", "-m", "Test release source"],
    ]:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def test_release_metadata_requires_matching_versions_and_dependencies(release_source):
    check(release_source)
    path = release_source / "protoface_comfyui/__init__.py"
    path.write_text('__version__ = "9.9.9"\n')
    with pytest.raises(ValueError, match="__version__ must match"):
        check(release_source)
    shutil.copyfile(ROOT / "protoface_comfyui/__init__.py", path)
    (release_source / "requirements.txt").write_text("httpx>=0.27,<1\n")
    with pytest.raises(ValueError, match="dependencies must match"):
        check(release_source)


def test_registry_requires_explicit_matching_publisher(release_source):
    with pytest.raises(ValueError, match="PublisherId"):
        check(release_source, publisher_id="")
    with pytest.raises(ValueError, match="PublisherId"):
        check(release_source, publisher_id="unregistered-example")


def test_release_rejects_mismatched_or_unreviewed_tag(release_source):
    with pytest.raises(ValueError, match="Release tag"):
        check(release_source, tag="v9.9.9")
    version = check(release_source)["project"]["version"]
    tag = f"v{version}"
    subprocess.run(["git", "tag", tag], cwd=release_source, check=True)
    with pytest.raises(subprocess.CalledProcessError):
        check(release_source, tag=tag)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/master", "HEAD"], cwd=release_source, check=True
    )
    check(release_source, tag=tag)


def test_source_package_is_complete_reproducible_and_excludes_development_files(release_source, tmp_path):
    config = check(release_source)
    first = build(release_source, tmp_path / "one", config)
    second = build(release_source, tmp_path / "two", config)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert "__init__.py" in names and "web/protoface.js" in names
        assert (
            len([name for name in names if name.startswith("example_workflows/") and name.endswith(".json")])
            == 4
        )
        assert any(name.startswith("protoface_comfyui/data/history/") for name in names)
        assert not any(name.startswith((".github/", "tests/", "scripts/")) for name in names)


def test_release_rejects_dirty_tree_and_secret_shaped_content(release_source, tmp_path):
    config = check(release_source)
    (release_source / "README.md").write_text("Changed contents\n")
    with pytest.raises(ValueError, match="Commit tracked changes"):
        build(release_source, tmp_path / "dist", config)
    subprocess.run(["git", "restore", "README.md"], cwd=release_source, check=True)
    (release_source / "unexpected.txt").write_text("sk_live_" + "x" * 32)
    with pytest.raises(ValueError, match="values withheld") as error:
        build(release_source, tmp_path / "dist", config)
    assert "x" * 32 not in str(error.value)


def test_release_rejects_missing_templates(release_source, tmp_path):
    config = check(release_source)
    subprocess.run(
        ["git", "rm", "-r", "example_workflows"], cwd=release_source, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Remove templates"], cwd=release_source, check=True, capture_output=True
    )
    with pytest.raises(ValueError, match="templates"):
        build(release_source, tmp_path / "dist", config)
