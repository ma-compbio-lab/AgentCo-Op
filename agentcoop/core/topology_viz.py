"""Topology visualisation.

Render a `compiled_workflow_graph.json` as both `topology.png` (a
matplotlib + networkx hierarchical layout, no external Graphviz needed)
and `topology.dot` (Graphviz source for users who prefer to render
themselves with `dot -Tpng topology.dot -o topology.png`).

The two artifacts make the multi-agent topology auditable at a glance,
which is what `docs/experiments/case_study_1.md` §12.2 asks for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx


_NODE_COLORS = {
    "agentcoop_internal": "#cfd8dc",        # gray-blue
    "sandboxed_external_agent": "#90caf9",  # blue
    "evaluator_gate": "#ffcc80",            # amber
    "llm_backed_agent_node": "#a5d6a7",     # green
    "agentcoop_reporter": "#f48fb1",        # pink
    "default": "#e0e0e0",                   # gray
}


def render_topology(
    graph_dict: dict[str, Any],
    *,
    out_dir: str | Path,
    title: str | None = None,
) -> dict[str, str]:
    """Write `topology.png` + `topology.dot` under `out_dir`.

    Returns a dict mapping artifact name → path string.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes = list(graph_dict.get("nodes", []))
    edges = list(graph_dict.get("edges", []))
    case_id = graph_dict.get("case_id", "external_collab")
    topology_type = graph_dict.get("topology_type", "")

    G = nx.DiGraph()
    for n in nodes:
        nid = n.get("id") or n.get("node_id")
        if not nid:
            continue
        G.add_node(nid, **{k: v for k, v in n.items() if k != "id"})
    for e in edges:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            G.add_edge(str(e[0]), str(e[1]))
        elif isinstance(e, dict) and "source" in e and "target" in e:
            G.add_edge(str(e["source"]), str(e["target"]))

    png_path = out_dir / "topology.png"
    dot_path = out_dir / "topology.dot"

    _render_png(G, png_path, title=title or f"{case_id} — workflow topology", topology_type=topology_type)
    _render_dot(G, dot_path, case_id=case_id, topology_type=topology_type)

    return {
        "topology_png": str(png_path),
        "topology_dot": str(dot_path),
    }


# ---------------------------------------------------------------------------
# PNG (matplotlib + networkx)
# ---------------------------------------------------------------------------


def _hierarchical_layout(G: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Topological layered layout: each node is placed at (x, -depth)
    where depth = longest path from any source. Falls back to spring
    layout when the graph has cycles."""
    try:
        layers = list(nx.topological_generations(G))
    except (nx.NetworkXUnfeasible, nx.NetworkXError):
        return nx.spring_layout(G, seed=42)
    pos: dict[str, tuple[float, float]] = {}
    for depth, layer in enumerate(layers):
        layer = list(layer)
        n = len(layer)
        for i, node in enumerate(layer):
            # Spread nodes horizontally; centre the layer.
            x = (i - (n - 1) / 2.0) * 1.6
            y = -depth * 1.2
            pos[node] = (x, y)
    return pos


def _render_png(
    G: nx.DiGraph, path: Path, *, title: str, topology_type: str
) -> None:
    pos = _hierarchical_layout(G)
    if not pos:
        return
    n_nodes = len(G.nodes)
    fig_w = max(8.5, 1.2 * n_nodes)
    fig_h = max(5.5, 1.0 * len({y for _, y in pos.values()}))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    colors = []
    for node in G.nodes:
        kind = G.nodes[node].get("kind", "default")
        colors.append(_NODE_COLORS.get(kind, _NODE_COLORS["default"]))

    nx.draw_networkx_edges(
        G, pos, ax=ax,
        arrows=True, arrowsize=18, arrowstyle="-|>",
        edge_color="#455a64", width=1.4,
        connectionstyle="arc3,rad=0.05",
        node_size=2400,
    )
    nx.draw_networkx_nodes(
        G, pos, ax=ax,
        node_color=colors, node_size=2400,
        edgecolors="#263238", linewidths=1.2,
    )

    labels = {}
    for node in G.nodes:
        attrs = G.nodes[node]
        agent = attrs.get("agent")
        kind = attrs.get("kind", "")
        label = node
        if agent:
            label = f"{node}\n[{agent}]"
        labels[node] = label
    nx.draw_networkx_labels(
        G, pos, labels=labels, ax=ax,
        font_size=9, font_color="#102027",
    )

    # Role annotations under each node.
    for node, (x, y) in pos.items():
        role = G.nodes[node].get("role")
        if role:
            ax.text(
                x, y - 0.45, role,
                ha="center", va="top",
                fontsize=7.5, color="#37474f",
                wrap=True,
                bbox=dict(facecolor="white", edgecolor="none", pad=1.0, alpha=0.7),
            )

    ax.set_title(f"{title}\n({topology_type})" if topology_type else title, fontsize=11)
    ax.set_axis_off()

    # Legend.
    handles = [
        mpatches.Patch(color=col, label=kind)
        for kind, col in _NODE_COLORS.items() if kind != "default"
    ]
    ax.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=8)

    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# DOT (Graphviz) — text-only; users can render with `dot -Tpng`
# ---------------------------------------------------------------------------


def _render_dot(
    G: nx.DiGraph, path: Path, *, case_id: str, topology_type: str
) -> None:
    lines: list[str] = [
        f"// AgentCo-Op compiled topology for case_id={case_id}",
        f"// topology_type={topology_type}",
        "digraph G {",
        '  rankdir=TB;',
        '  graph [splines=ortho, nodesep=0.45, ranksep=0.55, fontname="Helvetica"];',
        '  node  [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '  edge  [arrowhead=normal, fontname="Helvetica", fontsize=9];',
    ]
    for node in G.nodes:
        attrs = G.nodes[node]
        kind = attrs.get("kind", "default")
        color = _NODE_COLORS.get(kind, _NODE_COLORS["default"])
        agent = attrs.get("agent")
        role = (attrs.get("role") or "").replace('"', "'")
        label = node
        if agent:
            label += f"\\n[{agent}]"
        if role:
            label += f"\\n{role[:64]}"
        lines.append(f'  "{node}" [label="{label}", fillcolor="{color}"];')
    for u, v in G.edges:
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["render_topology"]
