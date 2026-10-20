# Protoface for ComfyUI

Use Protoface's generative media catalog inside native ComfyUI workflows. One extension generates searchable model/task nodes from the public API schemas: currently **44 models and 95 tasks**, including MiniMax H3, LTX 2.5, Seedance, Kling, image editing, music and 3D generation.

**Local prerelease.** This source has not been published to the Comfy Registry. Representative image, video, audio and reference workflows have been tested against the production API. Mock coverage includes every bundled task; it does not establish every model's production quality or latency.

## Install locally

Requires **ComfyUI 0.41.0 or newer** and Python 3.10 or newer. ComfyUI Desktop and local/self-managed hosted installations can load this custom-node package. Comfy Cloud does not permit arbitrary independent custom-node installation. This extension bills your Protoface account directly; it is independent of Comfy Partner Nodes and Comfy credits.

1. Copy or symlink this folder into `ComfyUI/custom_nodes/protoface`.
2. With **ComfyUI's Python interpreter**, install `requirements.txt`:
   ```sh
   python -m pip install -r custom_nodes/protoface/requirements.txt
   ```
   Desktop users should use its managed environment; Windows portable users should use `python_embeded/python.exe -m pip` rather than a system Python.
3. Restart ComfyUI and reload the browser.
4. Click **Protoface** in the lower-right corner and enter an API key from your Protoface account. Interactive keys stay in server memory and are excluded from exported workflows.
5. Search **Protoface**, **MiniMax H3**, a model name, or a task such as **Text to Video** in ComfyUI's Add Node search. Choose a model/task node, fill its controls, then queue it.

For a ready-made graph, open **Templates → the Protoface extension** and choose MiniMax H3 Text to Video, LTX 2.5 Fast Image to Video, Qwen Image 3.0 to MiniMax H3 Asset Reuse, or Resume Run.

Headless/persistent setup uses operator environment variables:

```sh
export PROTOFACE_API_KEY='your-api-key'
# Alternatively set PROTOFACE_API_KEY_FILE to a private file containing the key.
```

Never put a key in workflow JSON. Anyone allowed to execute workflows on a shared ComfyUI server can use its configured operator key; use separate server instances or an authenticated deployment with appropriate access controls for separate accounts.

## Work with media and controls

Each visible model/task has its own native controls. There is one shared implementation underneath, rather than separately maintained code per model.

- Prompts, enums, numbers and booleans come from public schemas. **Default** omits an optional parameter; **Set** supplies it; **Null** explicitly sends null where supported. Blank text is distinct from omission.
- Media inputs offer native **Image**, **Video** or **Audio** modes. Connect Load Image, native video, or native audio nodes. List inputs grow as you connect assets; input batches count toward the model's reference limit.
- **References** mode accepts **Protoface Asset Reference** (existing asset ID/public URL) or **Protoface Reuse Result Asset**. Reuse result assets to chain remote jobs without reuploading their media.
- Kling multi-shot and Pika transition controls use generated typed item nodes. Search the model name plus **Shot** or **Transition**, connect one helper per item, and select **Set** on the list control.
- Images, masks, videos, audio, SVG and 3D files use native typed outputs. Multiple results are output lists, including heterogeneous image sizes. ComfyUI maps connected downstream nodes over these lists. A mask uses native inverted-alpha semantics.
- **files** contains every original downloaded asset, including transparent PNGs and non-media artifacts. **Protoface Save File** writes originals to ComfyUI's output directory. A 3D file's actual format remains authoritative; a GLB-only downstream node may need a conversion if the provider returns another format.
- **run id** is available for recovery and support. **Protoface Resume Run** retrieves an existing job without submitting another generation.

## Paid generation, caching and interruption

Keep **generation_id** fixed to reuse or resume the same job. Increment it, or choose ComfyUI's increment-after-generation option, to request a fresh paid generation. This is separate from a model's random seed. Changed prompts, assets or model parameters constitute a new request.

The extension journals a request identity before submission and reuses its API idempotency key on ambiguous failures. Requeueing unchanged inputs after a restart resumes the recorded run. Interrupting execution requests cancellation; API cancellation is best effort and does not imply a refund. A polling timeout leaves a recoverable job rather than submitting another one. An unresolved submission near the API's 24-hour idempotency limit stops and asks you to find the run in your account.

Recorded runs resume without uploading their references again. For a new request, cached uploads are checked against the API; a deleted asset is uploaded again. If an ambiguous submission's reconstructed body changes, the adapter stops for recovery rather than retrying a different paid request under the same key.

Every model node contains a serialized, non-secret **request_scope** assigned by the frontend. Headless workflows must supply their own stable unique workflow/node scope and preserve it when resuming. Deleting the state directory, assigning a new scope, or changing the generation ID loses that deduplication intent and can lead to a new paid job.

## Catalog updates

The bundled catalog loads offline without an API key or startup network request. **Protoface → Refresh catalog** checks the live public schemas and caches a validated snapshot. Restart ComfyUI and reload the browser to load added/changed nodes. Ordinary new models using supported schema/media types need a catalog refresh rather than an extension-code update.

Nodes keep an immutable schema identity. Old definitions remain loadable from bundled/local history and are marked archived after a schema change or retirement. Archived definitions refuse new submissions; select the current model node or use Resume Run for previous jobs. Existing results are never automatically regenerated because a package version changed.

New schema constructs, media modalities, or provider-specific behavior can require an extension update. Unsupported catalog refreshes are rejected before replacing the working snapshot. Publishing a model-list endpoint alone does not prove a future model is usable in ComfyUI.

See [development and release checks](docs/development.md), [architecture](docs/architecture.md), and [example workflows](example_workflows/README.md).

## Operator limits

`PROTOFACE_STATE_DIR` selects the private catalog/job/media cache (default `~/.cache/protoface-comfyui`). Preserve this directory for job recovery; do not share it between accounts or delete it to troubleshoot an unresolved paid submission.

Optional operator settings are `PROTOFACE_CONCURRENCY` (default 2), `PROTOFACE_JOB_TIMEOUT_SECONDS` (7200), `PROTOFACE_POLL_INTERVAL_SECONDS` (2), and `PROTOFACE_MAX_DOWNLOAD_BYTES` (2,000,000,000). `PROTOFACE_BASE_URL` is trusted server configuration for a proxy/test deployment; it is never a workflow input. Changing credentials changes the cache namespace; use Resume Run for an existing job after API-key rotation.
