"""Dependency graph and generation order.

A child table depends on every table its foreign keys point to. We condense
strongly connected components (cycles) into single units so the planner can
still order the rest of the schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import Catalog


@dataclass
class GraphInfo:
    edges: dict[str, set[str]]
    order: list[str]
    components: list[list[str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)

    @property
    def has_cycles(self) -> bool:
        return bool(self.cycles)


def build_edges(catalog: Catalog) -> dict[str, set[str]]:
    """Map child table -> set of parent tables."""
    known = set(catalog.table_keys)
    edges: dict[str, set[str]] = {key: set() for key in known}
    for table in catalog.tables:
        for fk in table.foreign_keys:
            if (
                fk.parent_key in known
                and fk.parent_key != table.key
                and fk.parent_key not in edges[table.key]
            ):
                edges[table.key].add(fk.parent_key)
    return edges


def tarjan_scc(nodes: list[str], edges: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan SCC.

    The recursive version hits Python's recursion limit on deep foreign-key
    chains (a 1,200-table reverse-ordered chain is enough). The iterative
    version keeps an explicit work stack, so depth is bounded by memory.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    for root in nodes:
        if root in index:
            continue

        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True
        work: list[tuple[str, object, str | None]] = [
            (root, iter(edges.get(root, ())), None)
        ]

        while work:
            node, neighbours, parent = work[-1]
            descended = False

            for neighbour in neighbours:  # type: ignore[union-attr]
                if neighbour not in index:
                    index[neighbour] = low[neighbour] = counter
                    counter += 1
                    stack.append(neighbour)
                    on_stack[neighbour] = True
                    work.append((neighbour, iter(edges.get(neighbour, ())), node))
                    descended = True
                    break
                if on_stack.get(neighbour):
                    low[node] = min(low[node], index[neighbour])

            if descended:
                continue

            work.pop()
            if parent is not None:
                low[parent] = min(low[parent], low[node])

            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)

    return result


def _condense(
    nodes: list[str], edges: dict[str, set[str]], sccs: list[list[str]]
) -> tuple[list[list[str]], dict[str, int], list[list[str]]]:
    """Return (components in topological order, node -> component index, cycles)."""
    node_to_component: dict[str, int] = {}
    for idx, component in enumerate(sccs):
        for node in component:
            node_to_component[node] = idx

    component_edges: dict[int, set[int]] = {idx: set() for idx in range(len(sccs))}
    indegree = {idx: 0 for idx in range(len(sccs))}
    for node in nodes:
        dst = node_to_component[node]
        for parent in edges.get(node, ()):
            src = node_to_component[parent]
            if src != dst and dst not in component_edges[src]:
                component_edges[src].add(dst)
                indegree[dst] += 1

    ready = sorted([idx for idx, degree in indegree.items() if degree == 0])
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for neighbour in sorted(component_edges[current]):
            indegree[neighbour] -= 1
            if indegree[neighbour] == 0:
                ready.append(neighbour)
        ready.sort()

    cycles = [sorted(component) for component in sccs if len(component) > 1]
    components = [sorted(sccs[idx]) for idx in ordered]
    return components, node_to_component, cycles


def generation_order(catalog: Catalog) -> GraphInfo:
    nodes = catalog.table_keys
    edges = build_edges(catalog)
    sccs = tarjan_scc(nodes, edges)
    components, _, cycles = _condense(nodes, edges, sccs)
    order = [node for component in components for node in component]
    return GraphInfo(edges=edges, order=order, components=components, cycles=cycles)


def parents_of(catalog: Catalog, table_key: str) -> list[str]:
    table = catalog.table(table_key)
    parents: list[str] = []
    for fk in table.foreign_keys:
        if fk.parent_key != table_key and fk.parent_key not in parents:
            parents.append(fk.parent_key)
    return parents
