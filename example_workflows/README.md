# Example workflows

The four JSON files here use normal ComfyUI GUI format and appear under the installed extension in **Templates**. Open one and queue it after configuring your key. Templates contain **no API keys** and receive a fresh request identity when opened; saving that workflow preserves the identity for resume/reuse.

The corresponding [headless prompt graphs](../examples/api/) use `.api.json` format for ComfyUI's `/prompt` endpoint. Headless JSON is accepted by **File → Open**, but the Template Browser requires the GUI format.

- **h3-text-to-video:** a simple cinematic shot with native audio. Shows direct API access without loading local model weights.
- **ltx-image-to-video:** Load Image → LTX 2.5. Place your image in ComfyUI's input folder as `reference.png`, then queue. Useful for rapid still-to-motion iteration.
- **image-to-video-reuse:** Qwen Image → Reuse Result Asset → MiniMax H3 first frame. Demonstrates a multi-provider pipeline sharing assets through one Protoface account without reuploading the first result.
- **resume-run:** recover a prior job by replacing the run ID. Never submits a new paid generation.

The headless workflow identity is fixed for repeat/resume behavior. Use a distinct `request_scope` for a new copy of a headless production workflow, and increment `generation_id` for a new paid generation. `scripts/generate_examples.py` regenerates API graphs and checks GUI template identities. If a template's model controls change, re-export and review that GUI workflow before release; do not edit hash suffixes around an incompatible widget change.

H3 reference inputs are ordered lists of media. These examples do not claim that H3 supports numbered prompt-reference syntax. Public model contracts, rather than another model's conventions, determine supported reference controls.

Real price/speed/quality benefits must be measured on the same task and settings before claiming an advantage in a launch tutorial. The fixture API exercises integration behavior but cannot establish those benefits.
