from __future__ import annotations

from collections import defaultdict
from typing import Any, Awaitable, Callable

START = "__start__"
END = "__end__"

try:
    from langgraph.graph import END as LANGGRAPH_END
    from langgraph.graph import START as LANGGRAPH_START
    from langgraph.graph import StateGraph as LangGraphStateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    LANGGRAPH_AVAILABLE = False
    LANGGRAPH_START = START
    LANGGRAPH_END = END
    LangGraphStateGraph = None


NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ConditionFn = Callable[[dict[str, Any]], str]


class _FallbackCompiledGraph:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeFn],
        edges: dict[str, list[str]],
        conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]],
        entry_point: str,
    ) -> None:
        self._nodes = nodes
        self._edges = edges
        self._conditional_edges = conditional_edges
        self._entry_point = entry_point

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        current = self._entry_point
        current_state = state
        while current != END:
            handler = self._nodes[current]
            current_state = await handler(current_state)
            if current in self._conditional_edges:
                router, mapping = self._conditional_edges[current]
                branch = router(current_state)
                current = mapping[branch]
                continue
            next_nodes = self._edges.get(current) or []
            current = next_nodes[0] if next_nodes else END
        return current_state


class _FallbackStateGraph:
    def __init__(self, state_type: type[dict[str, Any]] | None = None) -> None:
        _ = state_type
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)
        self._conditional_edges: dict[str, tuple[ConditionFn, dict[str, str]]] = {}
        self._entry_point: str | None = None

    def add_node(self, name: str, handler: NodeFn) -> None:
        self._nodes[name] = handler

    def add_edge(self, source: str, target: str) -> None:
        if source == START:
            self._entry_point = target
            return
        self._edges[source].append(target)

    def add_conditional_edges(self, source: str, router: ConditionFn, mapping: dict[str, str]) -> None:
        self._conditional_edges[source] = (router, mapping)

    def compile(self) -> _FallbackCompiledGraph:
        if not self._entry_point:
            raise ValueError("Workflow entry point is not configured")
        return _FallbackCompiledGraph(
            nodes=self._nodes,
            edges=self._edges,
            conditional_edges=self._conditional_edges,
            entry_point=self._entry_point,
        )


StateGraph = LangGraphStateGraph if LANGGRAPH_AVAILABLE else _FallbackStateGraph
GRAPH_START = LANGGRAPH_START if LANGGRAPH_AVAILABLE else START
GRAPH_END = LANGGRAPH_END if LANGGRAPH_AVAILABLE else END


def workflow_engine_name() -> str:
    return "langgraph" if LANGGRAPH_AVAILABLE else "internal_state_graph"
