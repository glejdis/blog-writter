"""Excalidraw architecture-diagram builder.

The diagrammer agent emits a *simple* node/edge spec (JSON). This module turns
that spec into two deterministic artifacts:

* a self-contained ``.excalidraw`` scene (valid JSON, editable at
  https://aka.ms/excalidraw), and
* a Mermaid ``flowchart`` string the Writer can embed straight into the post.

Generating the Excalidraw scene ourselves — instead of asking the model to
hand-author Excalidraw JSON — guarantees every text element has explicit
width/height, every arrow binds to real nodes, and ids stay unique. That is the
part LLMs reliably get wrong, so we keep it in code.
"""

from __future__ import annotations

import binascii
import json
import re
from dataclasses import dataclass, field

EXCALIDRAW_URL = "https://aka.ms/excalidraw"

# ---- Layout constants (px) --------------------------------------------------
NODE_W = 190
NODE_H = 72
H_GAP = 90
V_GAP = 44
MARGIN = 50
TITLE_FONT = 28
NODE_FONT = 16
EDGE_FONT = 14

# FluentUI-ish palette: name -> (strokeColor, backgroundColor). Backgrounds are
# the light Excalidraw tints so black text stays readable on top.
PALETTE: dict[str, tuple[str, str]] = {
    "blue": ("#1971c2", "#a5d8ff"),
    "green": ("#2f9e44", "#b2f2bb"),
    "purple": ("#7048e8", "#d0bfff"),
    "teal": ("#0c8599", "#99e9f2"),
    "orange": ("#e8590c", "#ffd8a8"),
    "gray": ("#495057", "#e9ecef"),
}
# Mermaid uses solid fills, so pair each colour with a darker stroke.
MERMAID_PALETTE: dict[str, tuple[str, str]] = {
    "blue": ("#a5d8ff", "#1971c2"),
    "green": ("#b2f2bb", "#2f9e44"),
    "purple": ("#d0bfff", "#7048e8"),
    "teal": ("#99e9f2", "#0c8599"),
    "orange": ("#ffd8a8", "#e8590c"),
    "gray": ("#e9ecef", "#495057"),
}
_DEFAULT_COLOR = "gray"
_PALETTE_CYCLE = ("blue", "green", "purple", "teal", "orange")


# ---- Spec dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class DiagramNode:
    id: str
    label: str
    group: str | None = None


@dataclass(frozen=True)
class DiagramEdge:
    source: str
    target: str
    label: str = ""


@dataclass(frozen=True)
class DiagramGroup:
    id: str
    label: str
    color: str | None = None


@dataclass(frozen=True)
class DiagramSpec:
    title: str
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    groups: list[DiagramGroup] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.nodes)


# ---- Parsing ----------------------------------------------------------------


def parse_diagram_spec(text: str) -> DiagramSpec | None:
    """Parse the diagrammer agent's JSON output into a `DiagramSpec`.

    Tolerates ```json fences and surrounding prose. Returns ``None`` only when
    no usable object with nodes can be recovered.
    """
    raw = _extract_json_object(text)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return _coerce_spec(data)


def _coerce_spec(data: dict) -> DiagramSpec | None:
    nodes: list[DiagramNode] = []
    seen: set[str] = set()
    for n in data.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or n.get("label") or "").strip()
        label = str(n.get("label") or n.get("id") or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        group = n.get("group")
        nodes.append(DiagramNode(id=nid, label=label or nid, group=str(group) if group else None))
    if not nodes:
        return None

    valid_ids = {n.id for n in nodes}
    edges: list[DiagramEdge] = []
    edge_seen: set[tuple[str, str]] = set()
    for e in data.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src = str(e.get("from") or e.get("source") or "").strip()
        dst = str(e.get("to") or e.get("target") or "").strip()
        if src not in valid_ids or dst not in valid_ids or src == dst:
            continue
        if (src, dst) in edge_seen:
            continue
        edge_seen.add((src, dst))
        edges.append(DiagramEdge(source=src, target=dst, label=str(e.get("label") or "").strip()))

    groups: list[DiagramGroup] = []
    for g in data.get("groups") or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or g.get("label") or "").strip()
        if not gid:
            continue
        color = g.get("color")
        color = str(color).lower() if color else None
        if color not in PALETTE:
            color = None
        groups.append(DiagramGroup(id=gid, label=str(g.get("label") or gid), color=color))

    title = str(data.get("title") or "Architecture").strip() or "Architecture"
    return DiagramSpec(title=title, nodes=nodes, edges=edges, groups=groups)


def spec_from_sections(title: str, sections: list[str]) -> DiagramSpec:
    """Fallback spec: one node per outline section, wired in sequence.

    Used when the diagrammer agent's output can't be parsed so the pipeline
    still produces a diagram.
    """
    nodes = [DiagramNode(id=f"s{i}", label=s) for i, s in enumerate(sections) if s.strip()]
    if not nodes:
        nodes = [DiagramNode(id="s0", label=title)]
    edges = [DiagramEdge(source=nodes[i].id, target=nodes[i + 1].id) for i in range(len(nodes) - 1)]
    return DiagramSpec(title=title or "Architecture", nodes=nodes, edges=edges)


# ---- Colour resolution ------------------------------------------------------


def _resolve_colors(spec: DiagramSpec) -> dict[str, str]:
    """Map every node id to a palette colour name."""
    group_color: dict[str, str] = {g.id: g.color for g in spec.groups if g.color}
    # Auto-assign colours to groups that didn't pick one.
    auto = (c for c in _PALETTE_CYCLE)
    used = set(group_color.values())
    for g in spec.groups:
        if g.id in group_color:
            continue
        for c in _PALETTE_CYCLE:
            if c not in used:
                group_color[g.id] = c
                used.add(c)
                break
        else:
            group_color[g.id] = _DEFAULT_COLOR
    node_color: dict[str, str] = {}
    for n in spec.nodes:
        if n.group and n.group in group_color:
            node_color[n.id] = group_color[n.group]
        elif n.group:
            # group referenced but not declared — give it a stable colour
            idx = binascii.crc32(n.group.encode()) % len(_PALETTE_CYCLE)
            node_color[n.id] = _PALETTE_CYCLE[idx]
        else:
            node_color[n.id] = _DEFAULT_COLOR
    return node_color


# ---- Layout -----------------------------------------------------------------


def _layer_nodes(spec: DiagramSpec) -> dict[str, int]:
    """Longest-path layering: x-position bucket for each node."""
    layer = {n.id: 0 for n in spec.nodes}
    # Iterate enough times to propagate the longest path; cap protects cycles.
    for _ in range(len(spec.nodes)):
        changed = False
        for e in spec.edges:
            if layer[e.target] < layer[e.source] + 1:
                layer[e.target] = layer[e.source] + 1
                changed = True
        if not changed:
            break
    return layer


def _positions(spec: DiagramSpec) -> dict[str, tuple[int, int]]:
    layers = _layer_nodes(spec)
    by_layer: dict[int, list[str]] = {}
    for n in spec.nodes:
        by_layer.setdefault(layers[n.id], []).append(n.id)
    pos: dict[str, tuple[int, int]] = {}
    for layer, ids in sorted(by_layer.items()):
        x = MARGIN + layer * (NODE_W + H_GAP)
        for idx, nid in enumerate(ids):
            y = MARGIN + TITLE_FONT + 30 + idx * (NODE_H + V_GAP)
            pos[nid] = (x, y)
    return pos


# ---- Excalidraw element builders -------------------------------------------


def _nonce(key: str) -> int:
    return binascii.crc32(key.encode()) % (2**31)


def _base_element(eid: str, etype: str, x: float, y: float, w: float, h: float) -> dict:
    return {
        "id": eid,
        "type": etype,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": _nonce(eid + "seed"),
        "version": 1,
        "versionNonce": _nonce(eid + "nonce"),
        "isDeleted": False,
        "boundElements": [],
        "updated": 1,
        "link": None,
        "locked": False,
    }


def _rect(eid: str, x: float, y: float, w: float, h: float, color: str) -> dict:
    stroke, bg = PALETTE.get(color, PALETTE[_DEFAULT_COLOR])
    el = _base_element(eid, "rectangle", x, y, w, h)
    el["strokeColor"] = stroke
    el["backgroundColor"] = bg
    el["fillStyle"] = "solid"
    el["roundness"] = {"type": 3}
    return el


def _text(
    eid: str,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    *,
    font_size: int,
    container_id: str | None = None,
    align: str = "center",
) -> dict:
    el = _base_element(eid, "text", x, y, w, h)
    el["strokeColor"] = "#000000"
    el["text"] = text
    el["originalText"] = text
    el["fontSize"] = font_size
    el["fontFamily"] = 1
    el["textAlign"] = align
    el["verticalAlign"] = "middle"
    el["baseline"] = int(font_size * 0.9)
    el["containerId"] = container_id
    el["lineHeight"] = 1.25
    el["autoResize"] = True
    return el


def _arrow(eid: str, start: tuple[float, float], end: tuple[float, float], src_id: str, dst_id: str) -> dict:
    sx, sy = start
    ex, ey = end
    el = _base_element(eid, "arrow", sx, sy, abs(ex - sx), abs(ey - sy))
    el["strokeColor"] = "#495057"
    el["points"] = [[0, 0], [ex - sx, ey - sy]]
    el["lastCommittedPoint"] = None
    el["startBinding"] = {"elementId": src_id, "focus": 0, "gap": 6}
    el["endBinding"] = {"elementId": dst_id, "focus": 0, "gap": 6}
    el["startArrowhead"] = None
    el["endArrowhead"] = "arrow"
    el["roundness"] = {"type": 2}
    return el


def build_excalidraw(spec: DiagramSpec) -> str:
    """Render a `DiagramSpec` to a self-contained ``.excalidraw`` JSON string."""
    colors = _resolve_colors(spec)
    pos = _positions(spec)
    elements: list[dict] = []
    bound: dict[str, list[dict]] = {}

    # Title
    title_w = max(240, len(spec.title) * 15)
    title = _text(
        "title",
        MARGIN,
        MARGIN - 10,
        title_w,
        TITLE_FONT * 2,
        spec.title,
        font_size=TITLE_FONT,
        align="left",
    )
    elements.append(title)

    # Node rectangles + bound labels
    for n in spec.nodes:
        x, y = pos[n.id]
        rect_id = f"node-{n.id}"
        label_id = f"node-{n.id}-label"
        rect = _rect(rect_id, x, y, NODE_W, NODE_H, colors[n.id])
        label = _text(
            label_id,
            x + 8,
            y + NODE_H / 2 - NODE_FONT,
            NODE_W - 16,
            NODE_FONT * 2,
            n.label,
            font_size=NODE_FONT,
            container_id=rect_id,
        )
        rect["boundElements"] = bound.setdefault(rect_id, [])
        rect["boundElements"].append({"id": label_id, "type": "text"})
        elements.append(rect)
        elements.append(label)

    # Arrows + optional bound labels
    for i, e in enumerate(spec.edges):
        sx, sy = pos[e.source]
        dx, dy = pos[e.target]
        forward = dx >= sx
        start = (sx + NODE_W, sy + NODE_H / 2) if forward else (sx, sy + NODE_H / 2)
        end = (dx, dy + NODE_H / 2) if forward else (dx + NODE_W, dy + NODE_H / 2)
        arrow_id = f"edge-{i}"
        arrow = _arrow(arrow_id, start, end, f"node-{e.source}", f"node-{e.target}")
        # Register the arrow on both endpoints so Excalidraw keeps the binding.
        for rid in (f"node-{e.source}", f"node-{e.target}"):
            bound.setdefault(rid, []).append({"id": arrow_id, "type": "arrow"})
        if e.label:
            lbl_id = f"edge-{i}-label"
            midx = (start[0] + end[0]) / 2
            midy = (start[1] + end[1]) / 2
            lbl = _text(
                lbl_id,
                midx - len(e.label) * 4,
                midy - EDGE_FONT,
                max(40, len(e.label) * 8),
                EDGE_FONT * 2,
                e.label,
                font_size=EDGE_FONT,
                container_id=arrow_id,
            )
            arrow["boundElements"] = [{"id": lbl_id, "type": "text"}]
            elements.append(arrow)
            elements.append(lbl)
        else:
            elements.append(arrow)

    # Attach accumulated bindings to their rectangles.
    for el in elements:
        if el["type"] == "rectangle":
            el["boundElements"] = bound.get(el["id"], [])

    scene = {
        "type": "excalidraw",
        "version": 2,
        "source": EXCALIDRAW_URL,
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }
    return json.dumps(scene, indent=2)


# ---- Mermaid ----------------------------------------------------------------


def _mermaid_id(raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    if not safe or not safe[0].isalpha():
        safe = "n_" + safe
    return safe


def _mermaid_label(text: str) -> str:
    return text.replace('"', "'").replace("\n", " ").strip()


def build_mermaid(spec: DiagramSpec) -> str:
    """Render a `DiagramSpec` to a Mermaid ``flowchart`` string."""
    colors = _resolve_colors(spec)
    lines = ["flowchart LR"]
    idmap = {n.id: _mermaid_id(n.id) for n in spec.nodes}
    for n in spec.nodes:
        lines.append(f'    {idmap[n.id]}["{_mermaid_label(n.label)}"]')
    for e in spec.edges:
        if e.label:
            lines.append(f'    {idmap[e.source]} -->|{_mermaid_label(e.label)}| {idmap[e.target]}')
        else:
            lines.append(f"    {idmap[e.source]} --> {idmap[e.target]}")
    # Colour classes
    used_colors = sorted({colors[n.id] for n in spec.nodes})
    for c in used_colors:
        fill, stroke = MERMAID_PALETTE.get(c, MERMAID_PALETTE[_DEFAULT_COLOR])
        lines.append(f"    classDef {c} fill:{fill},stroke:{stroke},color:#000000")
    for c in used_colors:
        members = ",".join(idmap[n.id] for n in spec.nodes if colors[n.id] == c)
        if members:
            lines.append(f"    class {members} {c}")
    return "\n".join(lines)


# ---- Validation (used by tests + a sanity guard) ----------------------------


def validate_excalidraw(scene: dict) -> list[str]:
    """Return a list of structural problems; empty list means valid."""
    problems: list[str] = []
    if scene.get("type") != "excalidraw":
        problems.append("type must be 'excalidraw'")
    if scene.get("version") != 2:
        problems.append("version must be 2")
    elements = scene.get("elements")
    if not isinstance(elements, list) or not elements:
        problems.append("elements must be a non-empty list")
        return problems
    ids = [el.get("id") for el in elements]
    if len(ids) != len(set(ids)):
        problems.append("element ids must be unique")
    id_set = set(ids)
    for el in elements:
        if el.get("type") == "text":
            if not el.get("text"):
                problems.append(f"text element {el.get('id')} has empty text")
            if not (el.get("width", 0) > 0 and el.get("height", 0) > 0):
                problems.append(f"text element {el.get('id')} missing width/height")
        if el.get("type") == "arrow":
            for key in ("startBinding", "endBinding"):
                binding = el.get(key)
                if binding and binding.get("elementId") not in id_set:
                    problems.append(f"arrow {el.get('id')} {key} points to missing element")
    return problems


# ---- One-shot convenience ---------------------------------------------------


@dataclass(frozen=True)
class DiagramArtifacts:
    title: str
    excalidraw: str
    mermaid: str
    node_count: int
    edge_count: int


def render_diagram(spec: DiagramSpec) -> DiagramArtifacts:
    """Build both artifacts from a spec in one call."""
    return DiagramArtifacts(
        title=spec.title,
        excalidraw=build_excalidraw(spec),
        mermaid=build_mermaid(spec),
        node_count=len(spec.nodes),
        edge_count=len(spec.edges),
    )


def _extract_json_object(text: str) -> str | None:
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    fenced_any = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced_any:
        return fenced_any.group(1)
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    return brace.group(0) if brace else None


__all__ = [
    "DiagramArtifacts",
    "DiagramEdge",
    "DiagramGroup",
    "DiagramNode",
    "DiagramSpec",
    "EXCALIDRAW_URL",
    "build_excalidraw",
    "build_mermaid",
    "parse_diagram_spec",
    "render_diagram",
    "spec_from_sections",
    "validate_excalidraw",
]
