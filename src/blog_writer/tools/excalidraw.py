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
# Boundary boxes drawn behind grouped nodes (Excalidraw companion).
GROUP_PAD = 16
GROUP_LABEL_H = 24
GROUP_FONT = 15

# Fluent UI palette: name -> (strokeColor, backgroundColor). Backgrounds are the
# light Fluent tints so black text stays readable on top. Kept in sync with the
# Mermaid palette below so the .excalidraw companion and the embedded flowchart
# look identical.
PALETTE: dict[str, tuple[str, str]] = {
    "blue": ("#0078D4", "#CFE4FA"),
    "green": ("#107C10", "#DFF6DD"),
    "purple": ("#5C2D91", "#E8DAEF"),
    "teal": ("#038387", "#CCEFF1"),
    "orange": ("#F7630C", "#FFF4CE"),
    "gray": ("#495057", "#F3F2F1"),
}
# Mermaid pairs the same light fill with the matching darker stroke.
MERMAID_PALETTE: dict[str, tuple[str, str]] = {
    "blue": ("#CFE4FA", "#0078D4"),
    "green": ("#DFF6DD", "#107C10"),
    "purple": ("#E8DAEF", "#5C2D91"),
    "teal": ("#CCEFF1", "#038387"),
    "orange": ("#FFF4CE", "#F7630C"),
    "gray": ("#F3F2F1", "#495057"),
}
_DEFAULT_COLOR = "gray"
_PALETTE_CYCLE = ("blue", "green", "purple", "teal", "orange")

# Node shapes the spec may request. Default is a rounded rectangle.
_SHAPES = frozenset({"rectangle", "rounded", "stadium", "cylinder", "hexagon"})
_DEFAULT_SHAPE = "rectangle"


# ---- Spec dataclasses -------------------------------------------------------


@dataclass(frozen=True)
class DiagramNode:
    id: str
    label: str
    group: str | None = None
    shape: str | None = None  # one of _SHAPES; None -> rounded rectangle


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
    parent: str | None = None  # id of an enclosing group, for nested boundaries


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
        shape = str(n.get("shape") or "").strip().lower() or None
        if shape not in _SHAPES:
            shape = None
        nodes.append(
            DiagramNode(
                id=nid,
                label=label or nid,
                group=str(group) if group else None,
                shape=shape,
            )
        )
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
    group_ids: set[str] = set()
    raw_parents: dict[str, str | None] = {}
    for g in data.get("groups") or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or g.get("label") or "").strip()
        if not gid or gid in group_ids:
            continue
        group_ids.add(gid)
        color = g.get("color")
        color = str(color).lower() if color else None
        if color not in PALETTE:
            color = None
        raw_parents[gid] = str(g.get("parent") or "").strip() or None
        groups.append(DiagramGroup(id=gid, label=str(g.get("label") or gid), color=color))

    # Resolve parents now that every group id is known: drop dangling or
    # self-referential parents, then break any cycles.
    groups = [
        DiagramGroup(
            id=g.id,
            label=g.label,
            color=g.color,
            parent=(raw_parents[g.id] if raw_parents[g.id] in group_ids and raw_parents[g.id] != g.id else None),
        )
        for g in groups
    ]
    groups = _break_group_cycles(groups)

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


# ---- Group tree helpers -----------------------------------------------------


def _break_group_cycles(groups: list[DiagramGroup]) -> list[DiagramGroup]:
    """Null out any ``parent`` that would create a cycle in the group tree."""
    by_id = {g.id: g for g in groups}
    out: list[DiagramGroup] = []
    for g in groups:
        seen = {g.id}
        cur = g.parent
        cyclic = False
        while cur:
            if cur in seen or cur not in by_id:
                cyclic = cur in seen
                break
            seen.add(cur)
            cur = by_id[cur].parent
        if cyclic and g.parent:
            out.append(DiagramGroup(id=g.id, label=g.label, color=g.color, parent=None))
        else:
            out.append(g)
    return out


def _group_relations(
    spec: DiagramSpec,
) -> tuple[
    dict[str, DiagramGroup],
    dict[str, list[str]],
    list[str],
    dict[str, list[DiagramNode]],
    list[DiagramNode],
]:
    """Return (groups_by_id, children, roots, nodes_in_group, ungrouped_nodes)."""
    by_id = {g.id: g for g in spec.groups}
    children: dict[str, list[str]] = {g.id: [] for g in spec.groups}
    roots: list[str] = []
    for g in spec.groups:
        if g.parent and g.parent in by_id:
            children[g.parent].append(g.id)
        else:
            roots.append(g.id)
    nodes_in: dict[str, list[DiagramNode]] = {g.id: [] for g in spec.groups}
    ungrouped: list[DiagramNode] = []
    for n in spec.nodes:
        if n.group and n.group in nodes_in:
            nodes_in[n.group].append(n)
        else:
            ungrouped.append(n)
    return by_id, children, roots, nodes_in, ungrouped


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
    by_id, _children, _roots, _nodes_in, _ung = _group_relations(spec)
    node_by_id = {n.id: n for n in spec.nodes}
    orig_index = {n.id: i for i, n in enumerate(spec.nodes)}

    # Stable ordering for groups by first appearance so members cluster together.
    group_order: dict[str, int] = {}
    for g in spec.groups:
        group_order.setdefault(g.id, len(group_order))

    def _cluster_key(nid: str) -> tuple[int, ...]:
        n = node_by_id[nid]
        if not n.group or n.group not in by_id:
            return (len(group_order) + 1,)  # ungrouped sinks to the bottom
        chain, seen, cur = [], set(), n.group
        while cur and cur in by_id and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = by_id[cur].parent
        chain.reverse()  # outermost group first so nested members stay adjacent
        return tuple(group_order.get(c, 0) for c in chain)

    # Reserve vertical room above the first row for nested box headers.
    depth: dict[str, int] = {}

    def _set_depth(gid: str, d: int, seen: set[str]) -> None:
        if gid in seen:
            return
        seen.add(gid)
        depth[gid] = d
        for c in _children.get(gid, []):
            _set_depth(c, d + 1, seen)

    for r in _roots:
        _set_depth(r, 0, set())
    max_depth = max(depth.values(), default=-1)
    header_room = (GROUP_PAD + GROUP_LABEL_H) * (max_depth + 1)

    by_layer: dict[int, list[str]] = {}
    for n in spec.nodes:
        by_layer.setdefault(layers[n.id], []).append(n.id)
    pos: dict[str, tuple[int, int]] = {}
    y0 = MARGIN + TITLE_FONT + 30 + header_room
    for layer, ids in sorted(by_layer.items()):
        ids.sort(key=lambda nid: (_cluster_key(nid), orig_index[nid]))
        x = MARGIN + layer * (NODE_W + H_GAP)
        for idx, nid in enumerate(ids):
            y = y0 + idx * (NODE_H + V_GAP)
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


def _node_element(eid: str, x: float, y: float, w: float, h: float, color: str, shape: str) -> dict:
    """A coloured node. Stadium renders as an ellipse; everything else a rounded box."""
    if shape == "stadium":
        stroke, bg = PALETTE.get(color, PALETTE[_DEFAULT_COLOR])
        el = _base_element(eid, "ellipse", x, y, w, h)
        el["strokeColor"] = stroke
        el["backgroundColor"] = bg
        el["fillStyle"] = "solid"
        return el
    return _rect(eid, x, y, w, h, color)


def _group_box_el(eid: str, x: float, y: float, w: float, h: float, color: str | None) -> dict:
    """A transparent, colour-bordered boundary box drawn behind grouped nodes."""
    stroke, _bg = PALETTE.get(color or _DEFAULT_COLOR, PALETTE[_DEFAULT_COLOR])
    el = _base_element(eid, "rectangle", x, y, w, h)
    el["strokeColor"] = stroke
    el["backgroundColor"] = "transparent"
    el["fillStyle"] = "solid"
    el["strokeWidth"] = 2
    el["roundness"] = {"type": 3}
    return el


def _group_boxes(
    spec: DiagramSpec, pos: dict[str, tuple[int, int]]
) -> tuple[dict[str, tuple[float, float, float, float]], dict[str, int], dict[str, DiagramGroup]]:
    """Compute a bounding box per group, nested children included."""
    by_id, children, roots, nodes_in, _ung = _group_relations(spec)
    boxes: dict[str, tuple[float, float, float, float]] = {}
    depth: dict[str, int] = {}

    def compute(gid: str, d: int, seen: set[str]) -> tuple[float, float, float, float] | None:
        if gid in seen:
            return None
        seen = seen | {gid}
        depth[gid] = d
        x1s: list[float] = []
        y1s: list[float] = []
        x2s: list[float] = []
        y2s: list[float] = []
        for n in nodes_in.get(gid, []):
            if n.id not in pos:
                continue
            x, y = pos[n.id]
            x1s.append(x)
            y1s.append(y)
            x2s.append(x + NODE_W)
            y2s.append(y + NODE_H)
        for c in children.get(gid, []):
            cb = compute(c, d + 1, seen)
            if cb:
                bx, by, bw, bh = cb
                x1s.append(bx)
                y1s.append(by)
                x2s.append(bx + bw)
                y2s.append(by + bh)
        if not x1s:
            return None
        minx = min(x1s) - GROUP_PAD
        miny = min(y1s) - GROUP_PAD - GROUP_LABEL_H
        maxx = max(x2s) + GROUP_PAD
        maxy = max(y2s) + GROUP_PAD
        box = (minx, miny, maxx - minx, maxy - miny)
        boxes[gid] = box
        return box

    for r in roots:
        compute(r, 0, set())
    return boxes, depth, by_id


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

    # Group boundary boxes — outermost first so nested boxes paint on top, and
    # before the nodes so each node sits above its box.
    boxes, box_depth, groups_by_id = _group_boxes(spec, pos)
    for gid in sorted(boxes, key=lambda g: box_depth.get(g, 0)):
        bx, by, bw, bh = boxes[gid]
        grp = groups_by_id[gid]
        elements.append(_group_box_el(f"group-{gid}", bx, by, bw, bh, grp.color))
        glabel = grp.label or gid
        elements.append(
            _text(
                f"group-{gid}-label",
                bx + 10,
                by + 6,
                max(60, len(glabel) * 9),
                GROUP_FONT * 1.6,
                glabel,
                font_size=GROUP_FONT,
                align="left",
            )
        )

    # Node shapes + bound labels
    for n in spec.nodes:
        x, y = pos[n.id]
        rect_id = f"node-{n.id}"
        label_id = f"node-{n.id}-label"
        rect = _node_element(rect_id, x, y, NODE_W, NODE_H, colors[n.id], n.shape or _DEFAULT_SHAPE)
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

    # Attach accumulated bindings to their node shapes (rectangles + ellipses).
    for el in elements:
        if el["type"] in ("rectangle", "ellipse") and el["id"].startswith("node-"):
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
    return text.strip().replace('"', "'").replace("\r", "").replace("\n", "<br/>")


# Mermaid wrappers per shape: (prefix, suffix) placed around a quoted label.
_MERMAID_SHAPE_WRAP: dict[str, tuple[str, str]] = {
    "rectangle": ('["', '"]'),
    "rounded": ('("', '")'),
    "stadium": ('(["', '"])'),
    "cylinder": ('[("', '")]'),
    "hexagon": ('{{"', '"}}'),
}


def _mermaid_node_decl(mid: str, node: DiagramNode) -> str:
    """Render a single shaped node declaration, e.g. ``id(["Label"])``."""
    pre, post = _MERMAID_SHAPE_WRAP.get(node.shape or _DEFAULT_SHAPE, _MERMAID_SHAPE_WRAP[_DEFAULT_SHAPE])
    return f"{mid}{pre}{_mermaid_label(node.label)}{post}"


def build_mermaid(spec: DiagramSpec) -> str:
    """Render a `DiagramSpec` to a Mermaid ``flowchart`` string.

    Groups become (optionally nested) ``subgraph`` blocks; nodes carry their
    requested shape; node colours come from the shared Fluent palette.
    """
    colors = _resolve_colors(spec)
    idmap = {n.id: _mermaid_id(n.id) for n in spec.nodes}
    by_id, children, roots, nodes_in, ungrouped = _group_relations(spec)

    lines = ["flowchart LR"]

    # Ungrouped node declarations live at the top level.
    for n in ungrouped:
        lines.append(f"    {_mermaid_node_decl(idmap[n.id], n)}")

    # Nested subgraphs carry their member node declarations.
    def emit_group(gid: str, level: int, seen: set[str]) -> None:
        if gid in seen:
            return
        seen.add(gid)
        pad = "    " * (level + 1)
        grp = by_id[gid]
        lines.append(f'{pad}subgraph {_mermaid_id("g_" + gid)}["{_mermaid_label(grp.label)}"]')
        for n in nodes_in.get(gid, []):
            lines.append(f"{pad}    {_mermaid_node_decl(idmap[n.id], n)}")
        for child in children.get(gid, []):
            emit_group(child, level + 1, seen)
        lines.append(f"{pad}end")

    seen_groups: set[str] = set()
    for r in roots:
        emit_group(r, 0, seen_groups)

    # Edges after declarations so shapes/labels are already established.
    for e in spec.edges:
        if e.label:
            lines.append(f"    {idmap[e.source]} -->|{_mermaid_label(e.label)}| {idmap[e.target]}")
        else:
            lines.append(f"    {idmap[e.source]} --> {idmap[e.target]}")

    # Colour classes for nodes.
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
