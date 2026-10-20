import copy
import json
from pathlib import Path

import pytest

from protoface_comfyui.catalog import BUNDLE, Catalog, parse_catalog, validate_payload


def editable_model(value):
    return next(
        m
        for m in value["models"]
        if "image.edit" in m["request_schemas"]
        and "prompt" in m["request_schemas"]["image.edit"]["properties"]
    )


def test_complete_bundle_and_no_network_startup(tmp_path):
    catalog = Catalog(tmp_path)
    expected = json.loads(BUNDLE.read_text())["models"]
    active = [o for o in catalog.operations if not o.retired]
    assert len(active) == sum(len(m["operations"]) for m in expected)
    assert {o.model["id"] for o in active} == {m["id"] for m in expected}
    assert len({o.node_id for o in catalog.operations}) == len(catalog.operations)


def test_refresh_retains_saved_schema_ids(tmp_path):
    original = json.loads(BUNDLE.read_text())
    old = Catalog(tmp_path)
    fresh = copy.deepcopy(original)
    model = editable_model(fresh)
    model["schema_fingerprints"]["image.edit"] = "a" * 64
    model["request_schemas"]["image.edit"]["properties"]["prompt"]["maxLength"] = 700
    changes = old.save_refresh(fresh)
    assert changes == {"added_or_changed": 1, "retired_or_changed": 1, "restart_required": True}
    new = Catalog(tmp_path)
    assert len(new.operations) == len(old.operations) + 1
    assert sum(o.retired for o in new.operations) == sum(o.retired for o in old.operations) + 1
    assert {o.node_id for o in old.operations} <= {o.node_id for o in new.operations}


def test_unsupported_refresh_is_atomic(tmp_path):
    catalog = Catalog(tmp_path)
    bad = json.loads(BUNDLE.read_text())
    editable_model(bad)["request_schemas"]["image.edit"]["properties"]["novel"] = {"type": "object"}
    with pytest.raises(ValueError, match="reviewed adapter"):
        catalog.save_refresh(bad)
    assert not (tmp_path / "catalog.json").exists()


def test_corrupt_cache_uses_bundle(tmp_path):
    (tmp_path / "catalog.json").write_text("{bad")
    catalog = Catalog(tmp_path)
    assert len(catalog.operations) >= sum(
        len(m["operations"]) for m in json.loads(BUNDLE.read_text())["models"]
    )
    assert catalog.cache_error
    catalog.save_refresh(json.loads(BUNDLE.read_text()))
    assert Catalog(tmp_path).cache_error is None


def test_availability_changes_preserve_current_node_identity(tmp_path):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    fresh["models"][0]["available"] = False
    assert catalog.save_refresh(fresh)["added_or_changed"] == 0
    new = Catalog(tmp_path)
    assert {o.node_id for o in new.operations} == {o.node_id for o in catalog.operations}
    assert {o.node_id for o in new.operations if o.retired} == {
        o.node_id for o in catalog.operations if o.retired
    }
    assert any(not o.model.get("available", True) for o in new.operations)


def test_unknown_relationship_rules_are_rejected_before_refresh(tmp_path):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    editable_model(fresh)["request_schemas"]["image.edit"]["x-protoface-constraints"] = [
        {"kind": "novel_rule"}
    ]
    with pytest.raises(ValueError, match="unsupported constraint"):
        catalog.save_refresh(fresh)
    assert not (tmp_path / "catalog.json").exists()


def test_offline_schema_constraints(tmp_path):
    catalog = Catalog(tmp_path)
    op = next(
        o
        for o in catalog.operations
        if o.model["id"] == "kuaishou/kling-3.0-turbo" and o.name == "video.generate"
    )
    payload = {"operation": op.name, "multi_prompt": [{"prompt": "Shot", "duration": 5}], "duration": 6}
    with pytest.raises(ValueError, match="must equal 6"):
        validate_payload(op, payload)
    payload["duration"] = 5
    validate_payload(op, payload)
    payload["prompt"] = "Mutually exclusive"
    with pytest.raises(ValueError, match="request: violates"):
        validate_payload(op, payload)


def test_prompt_validation_does_not_echo_content(tmp_path):
    op = next(o for o in Catalog(tmp_path).operations if o.fields.get("prompt", {}).get("maxLength"))
    with pytest.raises(ValueError) as error:
        validate_payload(
            op, {"operation": op.name, "prompt": "SECRET" * (op.fields["prompt"]["maxLength"] + 1)}
        )
    assert "SECRET" not in str(error.value)


def test_missing_schema_is_rejected():
    value = json.loads(BUNDLE.read_text())
    value["models"][0]["request_schemas"] = {}
    with pytest.raises(ValueError, match="Incomplete"):
        parse_catalog(value)


def test_h3_scalar_or_list_media_projects_full_reference_controls(tmp_path):
    op = next(o for o in Catalog(tmp_path).operations if o.model["id"] == "minimax/minimax-h3")
    assert op.fields["reference"]["maxItems"] == 9
    assert op.fields["reference_video"]["maxItems"] == 3
    assert op.fields["audio"]["maxItems"] == 3
    validate_payload(
        op, {"operation": op.name, "prompt": "Use the references", "reference": ["asset_one", "asset_two"]}
    )


def test_output_contract_change_gets_new_identity(tmp_path):
    original = json.loads(BUNDLE.read_text())
    fresh = copy.deepcopy(original)
    editable_model(fresh)["operations"]["image.edit"]["outputs"][0]["kind"] = "vector"
    catalog = Catalog(tmp_path)
    assert catalog.save_refresh(fresh)["added_or_changed"] == 1
    assert len(Catalog(tmp_path).operations) == len(catalog.operations) + 1


def test_native_templates_use_gui_format_and_current_schema_identities(tmp_path):
    root = Path(__file__).resolve().parents[1]
    current = {(o.model["id"], o.name): o for o in Catalog(tmp_path).operations if not o.retired}
    templates = list((root / "example_workflows").glob("*.json"))
    assert templates
    for path in templates:
        graph = json.loads(path.read_text())
        assert isinstance(graph.get("nodes"), list), "Template Browser does not accept API-format graphs"
        assert isinstance(graph.get("links"), list)
        for node in graph["nodes"]:
            props = node.get("properties", {})
            if "protoface_model" not in props:
                continue
            op = current[props["protoface_model"], props["protoface_operation"]]
            assert node["type"] == op.node_id
            assert props["protoface_template_schema"] == op.fingerprint
            assert node["widgets_values_named"]["request_scope"] == ""
