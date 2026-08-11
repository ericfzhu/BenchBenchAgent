"""Dual-mode ADK 2.0 compatibility layer.

Attempts to import from google.adk (Workflow, START, Agent, Context, Runner, DEFAULT_ROUTE).
If google.adk is not installed, provides a full-featured pure-Python fallback execution
engine supporting directed graph compilation, conditional and callable routing, dynamic
sub-node execution (await ctx.run_node()), and session state management.
"""

import asyncio
import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("bba.compat")

_NATIVE_ADK_AVAILABLE = False

try:
    from google.adk import (  # type: ignore
        Agent as _NativeAgent,
        Context as _NativeContext,
        DEFAULT_ROUTE as _NativeDEFAULT_ROUTE,
        Runner as _NativeRunner,
        START as _NativeSTART,
        Workflow as _NativeWorkflow,
    )
    _NATIVE_ADK_AVAILABLE = True
except ImportError:
    _NativeAgent = None
    _NativeContext = None
    _NativeDEFAULT_ROUTE = None
    _NativeRunner = None
    _NativeSTART = None
    _NativeWorkflow = None


def is_native_adk() -> bool:
    """Returns True if native google.adk is available and loaded."""
    return _NATIVE_ADK_AVAILABLE


# Sentinel definitions
START = _NativeSTART if _NATIVE_ADK_AVAILABLE else "__START__"
DEFAULT_ROUTE = _NativeDEFAULT_ROUTE if _NATIVE_ADK_AVAILABLE else "__DEFAULT__"


class FallbackContext:
    """Context object for workflow execution and agent interaction."""

    def __init__(
        self,
        session_id: str = "default_session",
        state: Optional[Dict[str, Any]] = None,
        route: Optional[str] = None,
        output: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.session_id: str = session_id
        self.state: Dict[str, Any] = state if state is not None else {}
        self.route: Optional[str] = route
        self.output: Any = output
        self.metadata: Dict[str, Any] = metadata if metadata is not None else {}
        self.history: List[Dict[str, Any]] = []

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.state[key] = value

    def update_state(self, updates: Dict[str, Any]) -> None:
        self.state.update(updates)

    async def run_node(self, node: Any, node_input: Any = None, **kwargs) -> Any:
        """Dynamically executes a node (callable, Agent, coroutine, or Workflow) within this context."""
        if node is None:
            return None

        # If node is a string identifier, dereference from state or fallback
        if isinstance(node, str):
            if hasattr(self, "_workflow") and node in getattr(self._workflow, "nodes", {}):
                node = self._workflow.nodes[node]
            else:
                return node

        # If node is an already created coroutine
        if inspect.iscoroutine(node):
            result = await node
        # If node is a sub-Workflow
        elif isinstance(node, FallbackWorkflow) or hasattr(node, "nodes"):
            sub_ctx = await node.run(context=self, input_data=node_input, **kwargs)
            result = sub_ctx.output
        # If node is an Agent or object with async run method
        elif hasattr(node, "run") and inspect.iscoroutinefunction(node.run):
            result = await node.run(self, node_input=node_input, **kwargs)
        elif hasattr(node, "run") and callable(node.run):
            result = node.run(self, node_input=node_input, **kwargs)
        # If node is async callable
        elif inspect.iscoroutinefunction(node):
            sig = inspect.signature(node)
            if len(sig.parameters) == 1:
                result = await node(self)
            elif len(sig.parameters) >= 2:
                result = await node(self, node_input)
            else:
                result = await node()
        # If node is standard sync callable
        elif callable(node):
            sig = inspect.signature(node)
            if len(sig.parameters) == 1:
                result = node(self)
            elif len(sig.parameters) >= 2:
                result = node(self, node_input)
            else:
                result = node()
            if inspect.iscoroutine(result):
                result = await result
        else:
            raise TypeError(f"Cannot execute node of type {type(node)}: {node}")

        if result is not None and not isinstance(result, FallbackContext):
            self.output = result
        return result


class FallbackAgent:
    """Pure-Python Agent implementation matching ADK 2.0 task agent interface."""

    def __init__(
        self,
        name: str,
        model: Optional[str] = None,
        instruction: str = "",
        system_prompt: str = "",
        tools: Optional[List[Any]] = None,
        mode: str = "task",
        description: str = "",
        runner_fn: Optional[Callable] = None,
        **kwargs,
    ):
        self.name: str = name
        self.model: Optional[str] = model
        self.instruction: str = instruction or system_prompt
        self.system_prompt: str = self.instruction
        self.tools: List[Any] = tools or []
        self.mode: str = mode
        self.description: str = description
        self.runner_fn: Optional[Callable] = runner_fn
        self.extra_config: Dict[str, Any] = kwargs

    async def run(self, context: FallbackContext, **kwargs) -> Any:
        """Runs the agent using custom runner_fn or attached model dispatcher."""
        if self.runner_fn is not None:
            if inspect.iscoroutinefunction(self.runner_fn):
                return await self.runner_fn(self, context, **kwargs)
            res = self.runner_fn(self, context, **kwargs)
            if inspect.iscoroutine(res):
                return await res
            return res

        dispatcher = context.get_state("_dispatcher")
        if dispatcher is not None:
            prompt = context.get_state("prompt", self.instruction)
            response = await dispatcher.generate(
                prompt=prompt,
                system_instruction=self.instruction,
                model=self.model or "",
                role=self.name,
            )
            context.output = response
            return response

        return f"Agent {self.name} executed without active dispatcher."

    async def __call__(self, context: FallbackContext, **kwargs) -> Any:
        return await self.run(context, **kwargs)

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, mode={self.mode!r}, model={self.model!r})"


class FallbackWorkflow:
    """Pure-Python directed graph Workflow engine matching ADK 2.0."""

    def __init__(
        self,
        name: str,
        nodes: Optional[List[Any]] = None,
        edges: Optional[List[Tuple[Any, Any]]] = None,
        initial_state: Optional[Dict[str, Any]] = None,
        max_steps: int = 100,
    ):
        self.name: str = name
        self.nodes: Dict[str, Any] = {}
        self.edges: Dict[Any, Any] = {}
        self.initial_state: Dict[str, Any] = initial_state or {}
        self.max_steps: int = max_steps

        if nodes:
            for n in nodes:
                self.add_node(n)
        if edges:
            for from_node, to_node in edges:
                self.add_edge(from_node, to_node)

    def _node_key(self, node: Any) -> Any:
        if node is START or node == "__START__":
            return START
        if isinstance(node, str):
            return node
        if hasattr(node, "name") and isinstance(node.name, str):
            return node.name
        if hasattr(node, "__name__") and isinstance(node.__name__, str):
            return node.__name__
        return id(node)

    def add_node(self, node: Any, name: Optional[str] = None) -> None:
        key = name or self._node_key(node)
        self.nodes[key] = node

    def add_edge(self, from_node: Any, to_node_or_routing: Any) -> None:
        from_key = self._node_key(from_node)
        self.edges[from_key] = to_node_or_routing
        # Ensure from_node is tracked
        if from_node is not START and from_key not in self.nodes:
            self.add_node(from_node)

        if isinstance(to_node_or_routing, dict):
            for route_target in to_node_or_routing.values():
                if route_target is not None:
                    target_key = self._node_key(route_target)
                    if target_key not in self.nodes:
                        self.add_node(route_target)
        elif to_node_or_routing is not None:
            is_routing_fn = (
                callable(to_node_or_routing)
                and not inspect.iscoroutinefunction(to_node_or_routing)
                and not hasattr(to_node_or_routing, "run")
                and not isinstance(to_node_or_routing, (FallbackAgent, FallbackWorkflow))
                and not (hasattr(to_node_or_routing, "name") and isinstance(to_node_or_routing.name, str))
            )
            if not is_routing_fn:
                target_key = self._node_key(to_node_or_routing)
                if target_key not in self.nodes:
                    self.add_node(to_node_or_routing)

    def _resolve_next_node(self, current_key: Any, ctx: FallbackContext) -> Optional[Any]:
        routing = self.edges.get(current_key)
        if routing is None:
            return None

        # 1. Dictionary routing
        if isinstance(routing, dict):
            target = None
            if ctx.route is not None and ctx.route in routing:
                target = routing[ctx.route]
            elif isinstance(ctx.output, str) and ctx.output in routing:
                target = routing[ctx.output]
            elif DEFAULT_ROUTE in routing:
                target = routing[DEFAULT_ROUTE]
            
            if target is None:
                return None
            if isinstance(target, str) and target in self.nodes:
                return self.nodes[target]
            return target

        # 2. Dynamic selector function
        if (
            callable(routing)
            and not inspect.iscoroutinefunction(routing)
            and not hasattr(routing, "run")
            and not isinstance(routing, (FallbackAgent, FallbackWorkflow))
        ):
            try:
                sig = inspect.signature(routing)
                if len(sig.parameters) >= 1:
                    chosen = routing(ctx)
                else:
                    chosen = routing()
                if chosen in self.nodes:
                    return self.nodes[chosen]
                return chosen
            except Exception as e:
                logger.error(f"Error evaluating routing function from {current_key}: {e}")
                return None

        # 3. Direct node
        if isinstance(routing, str) and routing in self.nodes:
            return self.nodes[routing]

        return routing

    async def run(
        self,
        input_data: Any = None,
        context: Optional[FallbackContext] = None,
        initial_state: Optional[Dict[str, Any]] = None,
        session_id: str = "default_session",
        **kwargs,
    ) -> FallbackContext:
        """Executes the graph from START until terminal condition."""
        if isinstance(input_data, FallbackContext) and context is None:
            context = input_data
            input_data = None

        merged_state = dict(self.initial_state)
        if initial_state:
            merged_state.update(initial_state)

        if context is None:
            ctx = FallbackContext(session_id=session_id, state=merged_state)
        else:
            ctx = context
            ctx.update_state(merged_state)

        if input_data is not None:
            ctx.state["input_data"] = input_data
            ctx.output = input_data

        # Determine first node from START edge
        current_node = self._resolve_next_node(START, ctx)
        steps = 0

        while current_node is not None and steps < self.max_steps:
            steps += 1
            current_key = self._node_key(current_node)
            logger.debug(f"Workflow {self.name} step {steps}: running node {current_key}")

            # Reset route before running node
            ctx.route = None

            # Execute node
            try:
                await ctx.run_node(current_node)
            except Exception as e:
                logger.error(f"Error executing node {current_key}: {e}")
                ctx.set_state("last_error", str(e))
                ctx.set_state("last_error_node", str(current_key))
                raise e

            # Resolve next node
            current_node = self._resolve_next_node(current_key, ctx)

        return ctx

    async def __call__(self, context: FallbackContext, **kwargs) -> Any:
        result_ctx = await self.run(context=context, **kwargs)
        return result_ctx.output


class FallbackRunner:
    """Runner for executing ADK Workflows."""

    def __init__(self, workflow: FallbackWorkflow):
        self.workflow: FallbackWorkflow = workflow

    async def run(
        self,
        session_id: str = "default_session",
        initial_state: Optional[Dict[str, Any]] = None,
        input_data: Any = None,
        **kwargs,
    ) -> FallbackContext:
        return await self.workflow.run(
            input_data=input_data,
            session_id=session_id,
            initial_state=initial_state,
            **kwargs,
        )

    def run_sync(
        self,
        session_id: str = "default_session",
        initial_state: Optional[Dict[str, Any]] = None,
        input_data: Any = None,
        **kwargs,
    ) -> FallbackContext:
        return asyncio.run(
            self.run(
                session_id=session_id,
                initial_state=initial_state,
                input_data=input_data,
                **kwargs,
            )
        )


# Exported interfaces
Context = _NativeContext if _NATIVE_ADK_AVAILABLE else FallbackContext
Agent = _NativeAgent if _NATIVE_ADK_AVAILABLE else FallbackAgent
Workflow = _NativeWorkflow if _NATIVE_ADK_AVAILABLE else FallbackWorkflow
Runner = _NativeRunner if _NATIVE_ADK_AVAILABLE else FallbackRunner

__all__ = [
    "START",
    "DEFAULT_ROUTE",
    "Context",
    "Agent",
    "Workflow",
    "Runner",
    "FallbackContext",
    "FallbackAgent",
    "FallbackWorkflow",
    "FallbackRunner",
    "is_native_adk",
]
