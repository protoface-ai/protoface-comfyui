# Releases and Comfy Registry publishing

## What is automated

`Checks` runs on pushes and PRs. It checks source contents, lint, catalog contracts, versions and dependencies; executes native mocked workflows on pinned Linux/Windows ComfyUI releases; and builds a full source ZIP. The package job compares every ZIP file's contents with **comfy-cli 1.22.0** packaging. This is an offline check, not a Registry upload.

`Prepare release` is manual. It accepts an existing `vX.Y.Z` tag, requires that exact commit to be on `master`, reruns Checks, and uploads a source ZIP, SHA-256 checksum and release notes as an Actions artifact. Both optional actions start disabled:

- `create_draft=true` creates a **draft** GitHub release. It never publishes the draft or overwrites an existing release.
- `publish_registry=true` publishes the tagged source to Comfy Registry after the package checks and the `comfy-registry` environment gate. This makes a Registry version available; treat it as a public publishing action. It is independent of the GitHub draft.

There is no publication on a normal push, merge, version-file edit or scheduled catalog check. No workflow requests a paid generation. PyPI publication is intentionally absent: artists need the full custom-node package, not the shared-adapter wheel.

## One-time repository setup

1. Merge the reviewed integration into the existing default branch, **master**. Keep Manager as the recommended installation path; manual installation commands should point to an approved release tag.
2. Require Checks before merging to master. Restrict writes and release tag creation to maintainers. Give Actions read permissions by default; the draft-release job alone requests `contents: write`.
3. Create the immutable official publisher ID at [Comfy Registry](https://registry.comfy.org). Confirm the proposed package ID **protoface-nodes** is available. The package ID is distinct from the GitHub repository name and the generated model/task node IDs.
4. The registered publisher ID **protoface** is committed in `[tool.comfy].PublisherId` in `pyproject.toml`. Set repository variable **COMFY_PUBLISHER_ID** to exactly the same value. If the proposed package ID is unavailable, update the package metadata, release guard, artifact names and instructions together before the first publication.
5. Create a GitHub Actions environment named **comfy-registry**. Restrict it to release tags and enable required reviewer approval where the repository's GitHub plan supports it. Store the Registry publisher key as environment secret **REGISTRY_ACCESS_TOKEN**. This is a Registry token, not a Protoface inference key. Do not add the token to source or workflow JSON.
6. Make the repository public only after the source and history audit. Registry publishing refuses a private repository. This release workflow does not change visibility.
7. Finish the listing's short description, public documentation links and icon/banner if desired. Add `Icon`/`Banner` public URLs to `[tool.comfy]` only once real assets exist; no fabricated asset URLs are included.

No inference key is needed for regular CI or release builds. The catalog candidate workflow uses a separate optional **PROTOFACE_CATALOG_API_KEY** repository secret. Enable its daily schedule with **PROTOFACE_CATALOG_REFRESH_ENABLED=true** only after configuring that credential and reviewing its first candidate.

## Prepare a version

Update `pyproject.toml` and `protoface_comfyui/__init__.py` to the same three-part semantic version. Add a non-empty entry in `CHANGELOG.md`, replace its unreleased marker with the release date, and describe changes to artist controls or saved workflow behavior. After Registry approval, update the release version, manual Git tag, and Registry ZIP links in `README.md` and `docs/installation.md`. The release guard also checks that `requirements.txt` matches project dependencies.

Registry publication takes the listing description from `[project].description` in the tagged `pyproject.toml`. Keep that description and the README introduction focused on supported media tasks and recognizable model names. A source edit changes the metadata sent with the next release; it does not edit an already published archive or listing by itself.

For catalog changes, review the candidate or run:

```sh
python scripts/sync_catalog.py /path/to/public-catalog.json
COMFYUI_PATH=/path/to/ComfyUI python scripts/generate_examples.py
```

Run all checks, review the diff and merge to master. Keep archived bundled schema definitions. Re-export GUI templates when controls change; do not repair incompatible widgets by merely replacing node hashes. Ordinary compatible new models can also reach installed users through runtime refresh without a code release.

Before the first supported release, complete the remaining Windows portable/Desktop smoke tests and check at least one fresh install against the production API. Live generations require an explicit budget and keep all private evidence outside this repository. Mock catalog coverage does not establish every model's live behavior.

## Tag and build

After the version commit is reviewed and merged to master, create and push an annotated tag for that exact commit. GitHub operations by the official publishing team should use the team account:

```sh
git tag -a v0.1.0 -m 'Protoface for ComfyUI 0.1.0'
GH_TOKEN=$(gh auth token --user protoface-team) \
git -c credential.helper= -c credential.helper='!gh auth git-credential' push origin v0.1.0

GH_TOKEN=$(gh auth token --user protoface-team) \
gh workflow run release.yml --repo protoface-ai/protoface-comfyui --ref master \
  --field tag=v0.1.0 --field create_draft=false --field publish_registry=false
```

Use the version being released, not always `0.1.0`. Wait for the full workflow to succeed, download the **protoface-release** artifact, inspect its notes and file list, verify `SHA256SUMS`, and install its ZIP into a fresh ComfyUI instance. The archive has root-level `__init__.py`, `web/`, schemas, requirements and templates. Extract those contents into `custom_nodes/protoface`; do not install the adapter wheel instead.

The same packaging check can run locally on a clean committed tree:

```sh
python -m pip install -e '.[dev]' 'comfy-cli==1.22.0'
python scripts/release.py check
python scripts/release.py build --verify-cli
```

## Publish deliberately

To prepare a GitHub draft, rerun `Prepare release` for the same reviewed tag with `create_draft=true` and `publish_registry=false`. Review its notes/assets, then explicitly publish it in GitHub when ready. The automation leaves it a draft.

To publish the Registry package, rerun for the same tag with `publish_registry=true`, and approve the configured environment gate. The job verifies public visibility, publisher identity, tag/version/master ancestry and the same package checksum before calling the pinned CLI. It uploads exactly the tracked source selected by `.comfyignore`. The CLI ZIP container's timestamps can differ from the deterministic release ZIP, but file names and every file's bytes must match.

After publication:

1. Verify the Registry page shows the expected publisher, package ID, version, repository and compatibility metadata. Upload acceptance does not prove successful indexing.
2. On a clean instance, find **Protoface** in Manager/Registry search, install it, restart and check Add Node search for **MiniMax H3** and **LTX 2.5 Fast**. Open installed templates and confirm the credential dialog and a workflow export without keys.
3. Check a production workflow within an approved budget, then unchanged-request reuse and Resume Run. Record only sanitized pass/fail evidence in the repository.
4. Link the verified Registry listing and install instructions from the README and announce only the capabilities actually tested.

If the Registry call times out or upload fails, inspect the existing version before retrying. Do not automatically replay a publish with an ambiguous result, replace a release tag or overwrite a published version. Correct mistakes with a new version and clear release notes. Installed runtime catalog state is separate from code version rollback; preserve the state directory and recover paid jobs with Resume Run.

## Public-content review

The source and archive guard catches common credential formats, private state files, machine paths and private deployment URLs without printing matched values. It complements manual review. Before public visibility or publication, review Git history, screenshots, example prompts, model metadata and release notes as well. Never copy validation logs, keys, run IDs, signed media URLs or private backend details into the repository.

## Sources and tool versions

Checked on 2026-10-19:

- [Registry publishing and `.comfyignore`](https://docs.comfy.org/registry/publishing)
- [Registry metadata specifications](https://docs.comfy.org/registry/specifications)
- [comfy-cli 1.22.0](https://pypi.org/project/comfy-cli/1.22.0/) — pinned release packaging/publishing tool; runtime users do not install it.

When updating the CLI pin, update it in `scripts/release.py`, both workflows and this guide together, and rerun the package-content equivalence check. Pinned ComfyUI compatibility versions live in `checks.yml` and the [development guide](development.md).
