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
    assert changes == {
        "added_or_changed": 1,
        "retired_or_changed": 1,
        "restart_required": True,
        "unsupported_operations": [],
    }
    new = Catalog(tmp_path)
    assert len(new.operations) == len(old.operations) + 1
    assert sum(o.retired for o in new.operations) == sum(o.retired for o in old.operations) + 1
    assert {o.node_id for o in old.operations} <= {o.node_id for o in new.operations}


def test_unsupported_operation_is_skipped_without_losing_its_supported_sibling(tmp_path):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    model = editable_model(fresh)
    assert "image.generate" in model["operations"]
    model["request_schemas"]["image.edit"]["properties"]["novel"] = {"type": "object"}
    changes = catalog.save_refresh(fresh)
    assert len(changes["unsupported_operations"]) == 1
    assert changes["unsupported_operations"][0]["model_id"] == model["id"]
    assert changes["unsupported_operations"][0]["operation"] == "image.edit"
    assert "reviewed adapter" in changes["unsupported_operations"][0]["reason"]
    new = Catalog(tmp_path)
    assert new.cache_error is None
    assert new.unsupported_operations == changes["unsupported_operations"]
    siblings = {o.name: o for o in new.operations if o.model["id"] == model["id"]}
    assert not siblings["image.generate"].retired
    assert siblings["image.edit"].retired
    assert {o.node_id for o in catalog.operations} == {o.node_id for o in new.operations}
    # Preserve the unsupported source contract, not a silently reduced parameter set.
    assert json.loads((tmp_path / "catalog.json").read_text()) == fresh
    with pytest.raises(ValueError, match="reviewed adapter"):
        parse_catalog(fresh)


@pytest.mark.parametrize(
    "unsupported",
    [
        {"type": "array", "maxItems": 1000, "items": {"type": "integer"}},
        {"type": "array", "items": {"type": "string"}},
        {
            "type": "array",
            "maxItems": 2,
            "items": {"type": "array", "maxItems": 2, "items": {"type": "integer"}},
        },
        {"$ref": "#/$defs/future"},
        {"type": "string", "x-protoface-media": {"modalities": ["model.3d"]}},
    ],
)
def test_valid_future_controls_do_not_block_other_operations(tmp_path, unsupported):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    model = editable_model(fresh)
    schema = model["request_schemas"]["image.edit"]
    schema["$defs"] = {"future": {"type": "string"}}
    schema["properties"]["future"] = unsupported
    changes = catalog.save_refresh(fresh)
    assert len(changes["unsupported_operations"]) == 1
    new = Catalog(tmp_path)
    assert len([o for o in new.operations if not o.retired]) == len(catalog.operations) - 1
    assert new.unsupported_operations == changes["unsupported_operations"]


def test_future_output_is_skipped_and_complete_source_survives_restart(tmp_path):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    model = editable_model(fresh)
    model["operations"]["image.edit"]["outputs"][0]["modality"] = "embedding"
    changes = catalog.save_refresh(fresh)
    assert len(changes["unsupported_operations"]) == 1
    assert "output" in changes["unsupported_operations"][0]["reason"]
    assert Catalog(tmp_path).unsupported_operations == changes["unsupported_operations"]
    assert json.loads((tmp_path / "catalog.json").read_text()) == fresh


def test_refresh_with_no_supported_operations_keeps_cache_and_history(tmp_path):
    catalog = Catalog(tmp_path)
    original = json.loads(BUNDLE.read_text())
    catalog.save_refresh(original)
    saved = (tmp_path / "catalog.json").read_bytes()
    histories = {p.name: p.read_bytes() for p in (tmp_path / "catalog-history").glob("*.json")}
    model = copy.deepcopy(editable_model(original))
    for schema in model["request_schemas"].values():
        schema["properties"]["future"] = {"type": "object"}
    with pytest.raises(ValueError, match="no supported model tasks"):
        catalog.save_refresh({"models": [model]})
    assert (tmp_path / "catalog.json").read_bytes() == saved
    assert {p.name: p.read_bytes() for p in (tmp_path / "catalog-history").glob("*.json")} == histories


def test_malformed_schema_is_not_hidden_by_a_prior_unsupported_operation(tmp_path):
    catalog = Catalog(tmp_path)
    original = json.loads(BUNDLE.read_text())
    catalog.save_refresh(original)
    saved = (tmp_path / "catalog.json").read_bytes()
    fresh = copy.deepcopy(original)
    model = editable_model(fresh)
    model["request_schemas"]["image.edit"]["properties"]["future"] = {"type": "object"}
    model["request_schemas"]["image.generate"]["type"] = "not-a-json-schema-type"
    with pytest.raises(ValueError, match="invalid public request schema"):
        catalog.save_refresh(fresh)
    assert (tmp_path / "catalog.json").read_bytes() == saved


@pytest.mark.parametrize("broken", ["fingerprint", "outputs", "constraints"])
def test_incomplete_contract_is_rejected_even_if_controls_are_unsupported(tmp_path, broken):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    model = editable_model(fresh)
    schema = model["request_schemas"]["image.edit"]
    schema["properties"]["future"] = {"type": "object"}
    if broken == "fingerprint":
        del model["schema_fingerprints"]["image.edit"]
    elif broken == "outputs":
        model["operations"]["image.edit"]["outputs"] = []
    else:
        schema["x-protoface-constraints"] = "not-a-list"
    with pytest.raises(ValueError):
        catalog.save_refresh(fresh)
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


@pytest.mark.parametrize("field", ["id", "display_name", "family", "owner"])
def test_refresh_rejects_missing_renderer_metadata_without_replacing_cache(tmp_path, field):
    catalog = Catalog(tmp_path)
    original = json.loads(BUNDLE.read_text())
    catalog.save_refresh(original)
    current = tmp_path / "catalog.json"
    saved = current.read_bytes()
    fresh = copy.deepcopy(original)
    del fresh["models"][0][field]
    with pytest.raises(ValueError, match=field):
        catalog.save_refresh(fresh)
    assert current.read_bytes() == saved
    assert Catalog(tmp_path).cache_error is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", " "),
        ("family", []),
        ("owner", None),
        ("description", None),
        ("aliases", "alias"),
        ("tags", [None]),
        ("available", "yes"),
    ],
)
def test_refresh_rejects_malformed_renderer_metadata(tmp_path, field, value):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    fresh["models"][0][field] = value
    with pytest.raises(ValueError):
        catalog.save_refresh(fresh)
    assert not (tmp_path / "catalog.json").exists()


@pytest.mark.parametrize("broken", ["metadata", "schema", "root"])
def test_invalid_cached_contract_falls_back_before_node_generation(tmp_path, broken):
    value = json.loads(BUNDLE.read_text())
    if broken == "metadata":
        del value["models"][0]["display_name"]
    elif broken == "schema":
        schema = next(iter(value["models"][0]["request_schemas"].values()))
        schema["type"] = "invalid-schema-type"
    else:
        value = []
    (tmp_path / "catalog.json").write_text(json.dumps(value))
    catalog = Catalog(tmp_path)
    assert catalog.cache_error
    assert {op.model["id"] for op in catalog.operations if not op.retired} == {
        model["id"] for model in json.loads(BUNDLE.read_text())["models"]
    }


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


def test_unknown_relationship_rules_skip_only_the_affected_operation(tmp_path):
    catalog = Catalog(tmp_path)
    fresh = json.loads(BUNDLE.read_text())
    editable_model(fresh)["request_schemas"]["image.edit"]["x-protoface-constraints"] = [
        {"kind": "novel_rule"}
    ]
    changes = catalog.save_refresh(fresh)
    assert len(changes["unsupported_operations"]) == 1
    assert "unsupported constraint" in changes["unsupported_operations"][0]["reason"]
    assert Catalog(tmp_path).unsupported_operations == changes["unsupported_operations"]
    with pytest.raises(ValueError, match="unsupported constraint"):
        parse_catalog(fresh)


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
