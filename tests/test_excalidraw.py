"""Tests for the deterministic Excalidraw + Mermaid diagram builder."""

from __future__ import annotations

import json

from blog_writer.tools.excalidraw import (
    DiagramEdge,
    DiagramGroup,
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


def _nested_spec() -> DiagramSpec:
    return DiagramSpec(
        title="Nested architecture",
        groups=[
            DiagramGroup(id="spoke", label="AI Spoke VNet", color="blue"),
            DiagramGroup(id="pe", label="Private Endpoints", color="green", parent="spoke"),
        ],
        nodes=[
            DiagramNode(id="user", label="Internal User", shape="stadium"),
            DiagramNode(id="app", label="Chat UI", group="spoke"),
            DiagramNode(id="search", label="Azure AI Search", group="pe", shape="cylinder"),
        ],
        edges=[
            DiagramEdge(source="user", target="app", label="HTTPS"),
            DiagramEdge(source="app", target="search"),
        ],
    )


def test_mermaid_emits_nested_subgraphs_and_shapes() -> None:
    mermaid = build_mermaid(_nested_spec())
    lines = mermaid.splitlines()
    # One subgraph per group; the inner one nested deeper than the outer.
    assert mermaid.count("subgraph ") == 2
    assert any(line.strip() == "end" for line in lines)
    outer = next(ln for ln in lines if "AI Spoke VNet" in ln)
    inner = next(ln for ln in lines if "Private Endpoints" in ln)
    indent = lambda ln: len(ln) - len(ln.lstrip())  # noqa: E731
    assert indent(inner) > indent(outer)
    # Shapes survive into the Mermaid node declarations.
    assert '(["Internal User"])' in mermaid  # stadium
    assert '[("Azure AI Search")]' in mermaid  # cylinder
    # Edges and colour classes are still emitted after the subgraphs.
    assert "-->|HTTPS|" in mermaid
    assert "classDef" in mermaid


def test_excalidraw_draws_group_boxes_and_shapes() -> None:
    scene = json.loads(build_excalidraw(_nested_spec()))
    assert validate_excalidraw(scene) == []
    ids = [el["id"] for el in scene["elements"]]
    assert len(ids) == len(set(ids))
    # One boundary box per group.
    assert "group-spoke" in ids
    assert "group-pe" in ids
    types = {el["id"]: el["type"] for el in scene["elements"]}
    assert types["node-user"] == "ellipse"  # stadium -> ellipse
    assert types["node-app"] == "rectangle"
    # The stadium node still carries its bound text label.
    user = next(el for el in scene["elements"] if el["id"] == "node-user")
    assert any(b["type"] == "text" for b in user["boundElements"])


def test_parse_spec_reads_shape_and_parent() -> None:
    text = (
        '{"title":"T",'
        '"groups":[{"id":"out","label":"Outer"},'
        '{"id":"in","label":"Inner","parent":"out"}],'
        '"nodes":[{"id":"a","label":"A","shape":"stadium"},'
        '{"id":"b","label":"B","group":"in","shape":"bogus"}],'
        '"edges":[{"from":"a","to":"b"}]}'
    )
    spec = parse_diagram_spec(text)
    assert spec is not None
    by_id = {g.id: g for g in spec.groups}
    assert by_id["in"].parent == "out"
    assert by_id["out"].parent is None
    nodes = {n.id: n for n in spec.nodes}
    assert nodes["a"].shape == "stadium"
    assert nodes["b"].shape is None  # unknown shape is dropped


def test_parse_spec_breaks_group_cycles() -> None:
    text = (
        '{"title":"T",'
        '"groups":[{"id":"x","label":"X","parent":"y"},'
        '{"id":"y","label":"Y","parent":"x"}],'
        '"nodes":[{"id":"a","label":"A","group":"x"}]}'
    )
    spec = parse_diagram_spec(text)
    assert spec is not None
    parents = {g.id: g.parent for g in spec.groups}
    # The cycle is broken so the renderers can't recurse forever.
    assert not (parents["x"] == "y" and parents["y"] == "x")
    # And it still renders without error.
    assert validate_excalidraw(json.loads(build_excalidraw(spec))) == []
