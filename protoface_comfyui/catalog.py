"""Offline catalog, explicit refresh, and immutable workflow schema identities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError
from referencing import Registry

BUNDLE = Path(__file__).parent / "data" / "catalog.json"
ENVELOPE = {"operation", "metadata", "external_id"}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temp, path)


def ui_schema(schema: dict) -> dict:
    """Project a scalar/list media union into native list sockets without changing wire validation."""
    if schema.get("x-protoface-media"):
        branches = schema.get("oneOf", schema.get("anyOf", []))
        array = next((branch for branch in branches if branch.get("type") == "array"), None)
        if array:
            return {**schema, **{key: value for key, value in array.items() if key != "type"}}
    return schema


def field_type(schema: dict) -> str:
    schema = ui_schema(schema)
    kind = schema.get("type")
    if isinstance(kind, list):
        kinds = [k for k in kind if k != "null"]
        if len(kinds) == 1:
            return kinds[0]
    if isinstance(kind, str):
        return kind
    if "enum" in schema:
        item = next((x for x in schema["enum"] if x is not None), "")
        return (
            "boolean"
            if isinstance(item, bool)
            else "integer"
            if isinstance(item, int)
            else "number"
            if isinstance(item, float)
            else "string"
        )
    if schema.get("x-protoface-media"):
        return "array" if "items" in schema else "string"
    raise ValueError("Schema needs one representable non-null type")


def nullable(schema: dict) -> bool:
    return (
        "null" in (schema.get("type") or [])
        or None in schema.get("enum", [])
        or any(branch.get("type") == "null" for branch in schema.get("oneOf", schema.get("anyOf", [])))
    )


def check_field(schema: dict, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path}: a representable input schema is required")
    schema = ui_schema(schema)
    if "$ref" in schema:
        raise ValueError(f"{path}: unresolved reference; publish a resolved catalog schema")
    if "const" in schema:
        return
    kind = field_type(schema)
    if kind not in {"string", "integer", "number", "boolean", "array", "object"}:
        raise ValueError(f"{path}: unsupported type {kind}")
    if schema.get("x-protoface-media"):
        modalities = schema["x-protoface-media"].get("modalities", [])
        if not modalities or any(m not in {"image", "video", "audio"} for m in modalities):
            raise ValueError(f"{path}: media adapter missing for {modalities}")
    if kind == "array":
        if "maxItems" not in schema or not 1 <= schema["maxItems"] <= 100:
            raise ValueError(f"{path}: bounded array required by native ComfyUI Autogrow (1–100)")
        check_field(schema.get("items", {}), f"{path}[]")
    if kind == "object":
        if not schema.get("properties"):
            raise ValueError(f"{path}: open-ended objects require a reviewed adapter")
        for name, child in schema["properties"].items():
            check_field(child, f"{path}.{name}")


@dataclass(frozen=True)
class Operation:
    model: dict
    name: str
    schema: dict
    fingerprint: str
    retired: bool = False

    @property
    def node_id(self) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", self.model["id"] + "_" + self.name).strip("_")
        identity = digest(
            {
                "model": self.model["id"],
                "operation": self.name,
                "schema": self.fingerprint,
                "outputs": self.outputs,
            }
        )
        return f"Protoface_{slug}_{identity[:16]}"

    @property
    def fields(self) -> dict:
        return {k: ui_schema(v) for k, v in self.schema["properties"].items() if k not in ENVELOPE}

    @property
    def outputs(self) -> list[dict]:
        return self.model["operations"][self.name]["outputs"]


def parse_catalog(value: dict, *, retired: bool = False) -> list[Operation]:
    if not isinstance(value, dict):
        raise ValueError("Catalog must be a JSON object")
    models = value.get("models", value.get("data"))
    if not isinstance(models, list) or not models:
        raise ValueError("Catalog must contain models and their public request_schemas")
    operations = []
    ids = set()
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Catalog model must be a JSON object")
        for field in ("id", "display_name", "family", "owner"):
            if not isinstance(model.get(field), str) or not model[field].strip():
                raise ValueError(f"Catalog model requires a non-empty {field} string")
        if "description" in model and not isinstance(model["description"], str):
            raise ValueError("Catalog model description must be a string")
        for field in ("aliases", "tags"):
            if field in model and (
                not isinstance(model[field], list) or any(not isinstance(item, str) for item in model[field])
            ):
                raise ValueError(f"Catalog model {field} must be a list of strings")
        if "available" in model and not isinstance(model["available"], bool):
            raise ValueError("Catalog model availability must be a boolean")
        if model["id"] in ids:
            raise ValueError(f"Duplicate model {model['id']}")
        ids.add(model["id"])
        schemas = model.get("request_schemas", {})
        if not isinstance(schemas, dict) or not isinstance(model.get("operations"), dict):
            raise ValueError(f"Catalog model {model['id']} requires schema and operation mappings")
        if set(schemas) != set(model["operations"]):
            raise ValueError(f"Incomplete schemas for {model['id']}")
        for name, schema in schemas.items():
            if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
                raise ValueError(f"{model['id']}/{name}: a request schema with properties is required")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError:
                raise ValueError(f"{model['id']}/{name}: invalid public request schema") from None
            for rule in schema.get("x-protoface-constraints", []):
                if rule.get("kind") not in {"length_equals", "sum_equals", "sum_maximum"}:
                    raise ValueError(f"{model['id']}/{name}: unsupported constraint; update the extension")
            for field, prop in schema["properties"].items():
                if field not in ENVELOPE:
                    check_field(prop, f"{model['id']}/{name}/{field}")
            outputs = model["operations"][name].get("outputs", [])
            if not outputs or any(
                o.get("modality") not in {"image", "video", "audio", "text", "data", "model.3d"}
                for o in outputs
            ):
                raise ValueError(f"Unrepresentable output for {model['id']}/{name}")
            fingerprint = model.get("schema_fingerprints", {}).get(name)
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise ValueError(f"Missing public schema fingerprint for {model['id']}/{name}")
            operations.append(Operation(model, name, schema, fingerprint, retired))
    return operations


class Catalog:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        bundled = json.loads(BUNDLE.read_text())
        snapshots = [bundled]
        current = state_dir / "catalog.json"
        if current.exists():
            try:
                cached = json.loads(current.read_text())
                parse_catalog(cached)
                snapshots.append(cached)
            except (ValueError, KeyError, OSError):
                # A broken cache must not prevent offline startup; status exposes this error.
                self.cache_error = "Cached catalog invalid. Using bundled catalog; refresh to repair."
        self.cache_error = getattr(self, "cache_error", None)
        active = parse_catalog(snapshots[-1])
        active_ids = {op.node_id for op in active}
        archived = {}
        for value in snapshots[:-1]:
            for op in parse_catalog(value, retired=True):
                if op.node_id not in active_ids:
                    archived[op.node_id] = op
        histories = [BUNDLE.parent / "history", state_dir / "catalog-history"]
        for path in sorted(path for directory in histories for path in directory.glob("*.json")):
            try:
                for op in parse_catalog(json.loads(path.read_text()), retired=True):
                    if op.node_id not in active_ids:
                        archived[op.node_id] = op
            except (ValueError, KeyError, OSError):
                self.cache_error = (
                    "Some catalog history is invalid; repair it before using affected workflows."
                )
        self.operations = active + list(archived.values())
        self.revision = snapshots[-1].get("revision", digest(snapshots[-1]))

    def save_refresh(self, value: dict) -> dict:
        fresh = parse_catalog(value)  # Validate entire refresh before any mutation.
        current = self.state_dir / "catalog.json"
        try:
            previous = json.loads(current.read_text())
            parse_catalog(previous)
        except (OSError, ValueError, KeyError):
            previous = json.loads(BUNDLE.read_text())
        atomic_json(self.state_dir / "catalog-history" / f"{digest(previous)}.json", previous)
        atomic_json(current, value)
        old = {o.node_id for o in self.operations if not o.retired}
        new = {o.node_id for o in fresh}
        return {
            "added_or_changed": len(new - old),
            "retired_or_changed": len(old - new),
            "restart_required": True,
        }


def validate_payload(op: Operation, payload: dict) -> None:
    digest(payload)  # Reject non-finite numbers and non-JSON values before uploading.
    validator = Draft202012Validator(op.schema, registry=Registry())
    error = next(iter(validator.iter_errors(payload)), None)
    if error:
        path = ".".join(map(str, error.absolute_path)) or "request"
        hint = "Check the node controls and model constraints."
        if error.validator == "enum":
            hint = "Choose one of: " + ", ".join(map(str, error.validator_value)) + "."
        elif error.validator in {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}:
            hint = f"The {error.validator} limit is {error.validator_value}."
        elif error.validator == "not" and "required" in error.validator_value:
            hint = "Use only one of: " + ", ".join(error.validator_value["required"]) + "."
        # Do not echo prompts, asset URLs, keys, or arbitrary user data in errors.
        raise ValueError(f"{path}: violates {error.validator}. {hint}")
    for rule in op.schema.get("x-protoface-constraints", []):
        values = payload.get(rule.get("field"))
        if values is None:
            continue
        kind = rule["kind"]
        if kind == "length_equals":
            expected = len(payload.get(rule["other_field"], [])) + rule.get("offset", 0)
            if len(values) != expected:
                raise ValueError(f"{rule['field']}: requires {expected} items.")
        elif kind in {"sum_equals", "sum_maximum"}:
            total = sum(item.get(rule["item_field"], rule.get("item_default", 0)) for item in values)
            bound = payload.get(rule.get("other_field")) if kind == "sum_equals" else rule["maximum"]
            if bound is not None and (total != bound if kind == "sum_equals" else total > bound):
                raise ValueError(
                    f"{rule['field']}: total {rule['item_field']} must {'equal' if kind == 'sum_equals' else 'not exceed'} {bound}."
                )
        else:
            raise ValueError(f"Unsupported constraint {kind}; update the Protoface extension.")
