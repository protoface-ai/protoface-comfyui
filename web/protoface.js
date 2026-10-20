import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

function ensureRequestScope(node) {
    if (!node.comfyClass?.startsWith("Protoface_")) return;
    const widget = node.widgets?.find(w => w.name === "request_scope");
    if (!widget) return;
    if (!widget.value) {
        // randomUUID requires a secure context; LAN-hosted ComfyUI may use HTTP.
        widget.value = typeof crypto.randomUUID === "function"
            ? crypto.randomUUID()
            : "protoface-" + Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
    }
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
}

// All model controls use native Comfy widgets. This adds only operator setup and request identity.
app.registerExtension({
    name: "Protoface.OperatorSettings",
    async setup() {
        const button = document.createElement("button");
        button.textContent = "Protoface";
        button.title = "Set API key or refresh the Protoface catalog";
        button.addEventListener("click", async () => {
            const response = await api.fetchApi("/protoface/settings");
            const settings = await response.json();
            const dialog = document.createElement("dialog");
            const title = document.createElement("h3");
            title.textContent = `Protoface ${settings.version} · ${settings.active_nodes} model tasks`;
            const note = document.createElement("p");
            note.textContent = settings.configured ? "API key configured. Generations use your Protoface credits." : "Enter your Protoface API key. It stays on this ComfyUI server and is excluded from workflows.";
            const endpoint = document.createElement("p");
            endpoint.textContent = `API: ${settings.api_base_url}`;
            const key = document.createElement("input");
            key.type = "password";
            key.autocomplete = "off";
            key.placeholder = "Protoface API key";
            key.style.width = "100%";
            const status = document.createElement("p");
            status.textContent = settings.cache_error || "Interactive keys are memory-only. Use PROTOFACE_API_KEY or PROTOFACE_API_KEY_FILE for persistence.";
            const save = document.createElement("button");
            save.textContent = "Set key";
            save.onclick = async () => {
                const result = await api.fetchApi("/protoface/credentials", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ api_key: key.value }) });
                key.value = "";
                status.textContent = result.ok ? "Key configured for this server session." : await result.text();
            };
            const refresh = document.createElement("button");
            refresh.textContent = "Refresh catalog";
            refresh.onclick = async () => {
                refresh.disabled = true;
                try {
                    const result = await api.fetchApi("/protoface/catalog/refresh", { method: "POST", headers: {"Content-Type": "application/json"}, body: "{}" });
                    if (!result.ok) throw new Error(await result.text());
                    const change = await result.json();
                    status.textContent = `${change.added_or_changed} added/changed tasks. Restart ComfyUI and reload this page to load the refreshed nodes.`;
                } catch (error) { status.textContent = error.message; }
                finally { refresh.disabled = false; }
            };
            const close = document.createElement("button");
            close.textContent = "Close";
            close.onclick = () => dialog.close();
            dialog.append(title, endpoint, note, key, status, save, refresh, close);
            dialog.addEventListener("close", () => dialog.remove());
            document.body.append(dialog);
            dialog.showModal();
        });
        // Floating button remains usable in both current and classic menu layouts.
        Object.assign(button.style, { position: "fixed", right: "16px", bottom: "16px", zIndex: "1000", padding: "8px 12px" });
        document.body.append(button);
    },
    nodeCreated(node) {
        // Preserve serialized value. It is an identity, not a credential.
        ensureRequestScope(node);
    },
    loadedGraphNode(node) {
        // Blank official templates get a fresh identity after saved widgets are restored.
        ensureRequestScope(node);
    }
});
