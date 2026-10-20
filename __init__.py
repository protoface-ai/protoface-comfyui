"""ComfyUI custom-node entrypoint (one package, generated catalog nodes)."""

WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from .protoface_comfyui.extension import Extension

    return Extension()
