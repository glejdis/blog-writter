"""Tests for the deterministic Excalidraw + Mermaid diagram builder."""

from __future__ import annotations

import json

from blog_writer.tools.excalidraw import (
    DiagramEdge,
    DiagramNode,
    DiagramSpec,
    build_excalidraw,
    build_mermaid,
    parse_diagram_spec,
    render_diagram,
    spec_from_sections,
    validate_excalidraw,
)


def _sample_spec() -> DiagramSpec:
    return DiagramSpec(
        title="Sample architecture",
        nodes=[
            DiagramNode(id="client", label="Client"),
            DiagramNode(id="api", label="API"),
            DiagramNode(id="db", label="Database"),
        ],
        edges=[
            DiagramEdge(source="client", target="api", label="request"),
            DiagramEdge(source="api", target="db", label="query"),
        ],
    )


def test_build_excalidraw_is_valid_scene() -> None:
    scene_json = build_excalidraw(_sample_spec())
    scene = json.loads(scene_json)

    assert scene["type"] == "excalidraw"
    assert scene["version"] == 2
    assert validate_excalidraw(scene) == []

    ids = [el["id"] for el in scene["elements"]]
    assert len(ids) == len(set(ids)), "element ids must be unique"

    texts = [el for el in scene["elements"] if el["type"] == "text"]
    assert texts, "scene should contain text labels"
    for el in texts:
        assert el["width"] > 0 and el["height"] > 0


def test_excalidraw_arrows_bind_to_existing_nodes() -> None:
    scene = json.loads(build_excalidraw(_sample_spec()))
    ids = {el["id"] for el in scene["elements"]}
    for el in scene["elements"]:
        if el["type"] != "arrow":
            continue
        start = el.get("startBinding") or {}
        end = el.get("endBinding") or {}
        if start.get("elementId"):
            assert start["elementId"] in ids
        if end.get("elementId"):
            assert end["elementId"] in ids


def test_build_mermaid_flowchart() -> None:
    mermaid = build_mermaid(_sample_spec())
    assert "flowchart" in mermaid
    assert "-->" in mermaid


def test_parse_diagram_spec_tolerates_json_fence() -> None:
    text = (
        "Here is the diagram:\n"
        "```json\n"
        '{"title": "T", "nodes": [{"id": "a", "label": "A"}, '
        '{"id": "b", "label": "B"}], '
        '"edges": [{"from": "a", "to": "b", "label": "x"}]}\n'
        "```\n"
    )
    spec = parse_diagram_spec(text)
    assert spec is not None
    assert spec.ok
    assert {n.id for n in spec.nodes} == {"a", "b"}
    assert spec.edges[0].source == "a"
    assert spec.edges[0].target == "b"


def test_parse_diagram_spec_drops_dangling_edges() -> None:
    text = (
        '{"title": "T", "nodes": [{"id": "a", "label": "A"}], '
        '"edges": [{"from": "a", "to": "ghost"}, {"from": "a", "to": "a"}]}'
    )
    spec = parse_diagram_spec(text)
    assert spec is not None
    assert spec.edges == []


def test_spec_from_sections_fallback() -> None:
    spec = spec_from_sections("Title", ["Intro", "Body", "Conclusion"])
    assert spec.ok
    assert len(spec.nodes) == 3
    # linear chain of edges
    assert len(spec.edges) == 2


def test_render_diagram_round_trip() -> None:
    artifacts = render_diagram(_sample_spec())
    assert artifacts.title == "Sample architecture"
    assert artifacts.node_count == 3
    assert artifacts.edge_count == 2
    assert validate_excalidraw(json.loads(artifacts.excalidraw)) == []
    assert "flowchart" in artifacts.mermaid
