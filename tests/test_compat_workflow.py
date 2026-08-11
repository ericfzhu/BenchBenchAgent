"""Unit tests for ADK 2.0 dual-mode compatibility layer."""

import asyncio
import unittest
from compat import (
    DEFAULT_ROUTE,
    START,
    Context,
    FallbackAgent,
    FallbackContext,
    FallbackRunner,
    FallbackWorkflow,
    Workflow,
)


class TestCompatWorkflow(unittest.IsolatedAsyncioTestCase):
    """Tests for workflow compilation, routing, dynamic execution, and context state."""

    async def test_context_state_operations(self):
        ctx = FallbackContext(session_id="test_sess", state={"initial": 123})
        self.assertEqual(ctx.get_state("initial"), 123)
        self.assertIsNone(ctx.get_state("non_existent"))
        self.assertEqual(ctx.get_state("non_existent", "default_val"), "default_val")

        ctx.set_state("new_key", "value_456")
        self.assertEqual(ctx.get_state("new_key"), "value_456")

        ctx.update_state({"k1": 1, "k2": 2})
        self.assertEqual(ctx.get_state("k1"), 1)
        self.assertEqual(ctx.get_state("k2"), 2)

    async def test_context_dynamic_run_node(self):
        ctx = FallbackContext()

        # 1. Sync function
        def sync_fn(c):
            c.set_state("sync_ran", True)
            return "sync_result"

        res1 = await ctx.run_node(sync_fn)
        self.assertEqual(res1, "sync_result")
        self.assertTrue(ctx.get_state("sync_ran"))

        # 2. Async function
        async def async_fn(c):
            c.set_state("async_ran", True)
            return "async_result"

        res2 = await ctx.run_node(async_fn)
        self.assertEqual(res2, "async_result")
        self.assertTrue(ctx.get_state("async_ran"))

        # 3. Callable object with run method
        class RunnerObj:
            async def run(self, c, **kwargs):
                c.set_state("runner_ran", True)
                return "runner_result"

        res3 = await ctx.run_node(RunnerObj())
        self.assertEqual(res3, "runner_result")
        self.assertTrue(ctx.get_state("runner_ran"))

    async def test_linear_workflow_progression(self):
        async def step_a(ctx):
            ctx.set_state("a", 1)

        async def step_b(ctx):
            ctx.set_state("b", 2)

        async def step_c(ctx):
            ctx.set_state("c", 3)

        wf = FallbackWorkflow(name="linear_test")
        wf.add_edge(START, step_a)
        wf.add_edge(step_a, step_b)
        wf.add_edge(step_b, step_c)

        ctx = await wf.run(session_id="linear_run")
        self.assertEqual(ctx.get_state("a"), 1)
        self.assertEqual(ctx.get_state("b"), 2)
        self.assertEqual(ctx.get_state("c"), 3)

    async def test_conditional_dictionary_routing(self):
        async def router_node(ctx):
            val = ctx.get_state("decision")
            if val == "GO_LEFT":
                ctx.route = "LEFT"
            else:
                ctx.route = "RIGHT"

        async def left_node(ctx):
            ctx.set_state("path", "left_path")

        async def right_node(ctx):
            ctx.set_state("path", "right_path")

        wf = FallbackWorkflow(name="branching_test")
        wf.add_edge(START, router_node)
        wf.add_edge(router_node, {
            "LEFT": left_node,
            "RIGHT": right_node,
        })

        # Test Branch 1 (Left)
        ctx1 = await wf.run(initial_state={"decision": "GO_LEFT"})
        self.assertEqual(ctx1.get_state("path"), "left_path")

        # Test Branch 2 (Right)
        ctx2 = await wf.run(initial_state={"decision": "GO_RIGHT"})
        self.assertEqual(ctx2.get_state("path"), "right_path")

    async def test_callable_routing_selector(self):
        async def init_node(ctx):
            ctx.set_state("counter", ctx.get_state("counter", 0) + 1)

        async def even_node(ctx):
            ctx.set_state("parity", "even")

        async def odd_node(ctx):
            ctx.set_state("parity", "odd")

        def route_selector(ctx):
            return even_node if ctx.get_state("counter") % 2 == 0 else odd_node

        wf = FallbackWorkflow(name="callable_routing_test")
        wf.add_edge(START, init_node)
        wf.add_edge(init_node, route_selector)

        ctx_even = await wf.run(initial_state={"counter": 1})  # 1 + 1 = 2 (even)
        self.assertEqual(ctx_even.get_state("parity"), "even")

        ctx_odd = await wf.run(initial_state={"counter": 0})   # 0 + 1 = 1 (odd)
        self.assertEqual(ctx_odd.get_state("parity"), "odd")

    async def test_runner_execution(self):
        async def single_node(ctx):
            ctx.set_state("executed", True)
            return "SUCCESS"

        wf = FallbackWorkflow(name="runner_test")
        wf.add_edge(START, single_node)

        runner = FallbackRunner(wf)
        ctx = await runner.run(session_id="runner_sess")
        self.assertTrue(ctx.get_state("executed"))


if __name__ == "__main__":
    unittest.main()
