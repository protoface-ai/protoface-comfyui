# Changelog

## 0.1.2 (2026-10-28)

- Refactor configuration loading and local job-journal queries, preserving existing settings, saved jobs, and request recovery.

## 0.1.1 (2026-10-22)

- Refresh compatible model tasks even when another task needs an unsupported control or media adapter.
- Show skipped model tasks and their reasons in Protoface settings, including after restarting ComfyUI.
- Preserve complete cached schemas and archived workflow definitions. Malformed catalogs and refreshes with no supported tasks still keep the previous catalog.

## 0.1.0 (2026-10-19)

- Generate searchable model/task nodes from the public Protoface catalog, with native image, video and audio connections and typed nested controls.
- Include offline catalog snapshots, explicit refreshes and archived node definitions for saved workflows.
- Keep credentials outside workflows and reuse recorded jobs across retries and restarts.
- Include H3, LTX 2.5 Fast, image-to-video asset reuse and job recovery templates.
- Add compatibility checks, public-content checks and manual release tooling.
