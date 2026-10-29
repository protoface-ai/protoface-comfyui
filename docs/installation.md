# Installation and first generation

## Choose an installation

Requires ComfyUI **0.41.0 or newer** and Python **3.10 or newer**. The extension performs inference through Protoface; you do not download model weights or need a local inference GPU. ComfyUI still supplies its native media types and tensor libraries. PyAV and Pillow handle native media connections and previews.

The latest approved release is **0.1.2**, available at [Protoface on Comfy Registry](https://registry.comfy.org/nodes/protoface-nodes), under publisher **protoface** and package ID **protoface-nodes**.

### ComfyUI Manager (recommended)

Open **Extensions** (or **Manager**), search **Protoface**, install **protoface-nodes**, then restart ComfyUI and reload the browser. Manager installs the latest approved Registry version. If the package does not appear, refresh Manager's catalog.

For a manual installation, use the tagged Git instructions below or unpack the [0.1.2 Registry source ZIP](https://cdn.comfy.org/protoface/protoface-nodes/0.1.2/node.zip).

There is one installable extension; its model/task nodes become searchable after installation. A wheel named `protoface_nodes-*.whl` contains only the shared Python adapter and is not an installable ComfyUI custom-node package.

### Local Linux or macOS

From the ComfyUI directory, with its virtual environment activated:

```sh
git clone --branch v0.1.2 --depth 1 https://github.com/protoface-ai/protoface-comfyui.git custom_nodes/protoface
python -m pip install -r custom_nodes/protoface/requirements.txt
```

For a source ZIP, create `custom_nodes/protoface` and unpack directly into it. Confirm `custom_nodes/protoface/__init__.py` exists; an extra nested archive folder prevents discovery. Restart ComfyUI and reload its browser page.

### Windows portable

Extract the full source ZIP into `ComfyUI_windows_portable/ComfyUI/custom_nodes/protoface`. From the portable distribution's root, use its bundled interpreter:

```powershell
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\protoface\requirements.txt
```

Restart ComfyUI. Installing with a separate system Python does not install dependencies into the portable distribution. CI includes Windows native execution checks; the portable distribution still needs a manual installation smoke test before it is advertised as validated.

### Desktop and hosted services

Use the Desktop installation's managed Python environment and custom-node directory rather than a separate system interpreter. The exact Desktop installation flow still needs a manual smoke test. Prefer Manager installation where the package is listed and this path is available.

A hosted service must explicitly permit independent custom nodes and operator credentials. Comfy Cloud does not permit arbitrary custom-node installation. This extension charges your Protoface account directly and does not use Comfy credits.

## Configure your account

Click **Protoface** in the lower-right corner and enter a key from your Protoface account. The dialog clears the input after saving. The key stays in the ComfyUI server's memory; you will need to enter it again after restarting the server. It is not saved in a node or exported workflow.

For a persistent or headless server, set `PROTOFACE_API_KEY_FILE` to a private file outside the extension directory before launching ComfyUI. The alternative is `PROTOFACE_API_KEY` in the operator environment. Do not put keys in workflow JSON, screenshots, committed shell scripts or issue reports.

Anyone who can run workflows on a shared server can spend through that server's key. Use appropriate server authentication and separate instances for separate Protoface accounts. Confirm the settings dialog shows `https://api.protoface.com` for production use.

## Run a workflow

1. Open **Templates → the Protoface extension** and select an example, or search **Protoface**, **MiniMax H3**, **LTX 2.5 Fast** or a task in Add Node.
2. Set the prompt and model controls. For LTX's image-to-video example, put `reference.png` in ComfyUI's input directory or choose another image in Load Image.
3. Queue the graph. Generations are paid through Protoface. The four templates cover direct video generation, native image upload, image-to-video asset reuse and recovery of an existing run.
4. Keep `generation_id` fixed to resume/reuse a request. Increment it to request another paid generation. Changing prompts or inputs also creates a new request.

The blue image/video blocks in developer screenshots are synthetic fixture media, not a preview of model quality. See [the workflows guide](../example_workflows/README.md) for media chaining and headless request scopes.

## Update models or extension code

**New models or compatible controls:** click **Protoface → Refresh catalog**, then restart ComfyUI and reload the browser. The current snapshot loads offline; refresh failure leaves it working. Supported catalog changes can arrive without reinstalling extension code.

**A new extension release:** update with Manager after Registry publication, or replace the source folder with the reviewed release ZIP and reinstall `requirements.txt` with ComfyUI's interpreter. Git installations can fetch and check out a reviewed tag. Restart ComfyUI and reload the browser. Do not install a second copy under another name.

Preserve `PROTOFACE_STATE_DIR` (default `~/.cache/protoface-comfyui`) across updates. That directory holds private catalog/job/media state and belongs outside the repository. Changing/deleting it can lose recovery and deduplication information. Old schema definitions remain identifiable, but archived models refuse new submissions; choose a current model node or **Protoface Resume Run** to retrieve existing work.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No Protoface nodes | Confirm the entrypoint location, supported ComfyUI version, successful imports in the startup log and that there is only one copy installed. Restart and reload the browser. |
| Cannot find a newly added model | Refresh the catalog, restart and reload. Unsupported schema additions require a code update. Registry search finds the package; Add Node finds installed nodes. |
| Refresh reports unsupported tasks | Expand the unsupported-task list in Protoface settings for model names and reasons. Compatible tasks still load after restarting. Skipped tasks require an adapter update; their full schemas remain cached. A malformed response or one with no supported tasks keeps the previous catalog. |
| Missing dependency | Install `requirements.txt` using ComfyUI's own Python interpreter. |
| Authentication or credit error | Re-enter the account key after a restart; check that the account has enough credits. |
| Job times out or the browser disconnects | Preserve the workflow scope and generation ID and requeue, or use Resume Run with the run ID. Do not increment the generation ID just to recover. |
| Cancelled job still has a charge | Cancellation is best effort and does not promise a refund. Check the run status in your Protoface account. |
| An archived node refuses a new request | Use the current model/task definition. Reuse the previous run ID to recover its result. |

For support, include extension version, ComfyUI version, OS, model/task and a sanitized error. Remove credentials, private prompts/media, signed result URLs and account/job identifiers from public issues. Share private diagnostic details through an appropriate support channel instead.
