"""Creator retry inference reservation tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bba.state import LocalStateStore
from tests.test_end_state_protocol import manifest


class TestCreatorReservations(unittest.TestCase):
    def test_each_creator_work_attempt_gets_a_distinct_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = LocalStateStore(Path(temporary) / "state.sqlite3")
            value = manifest("creator-reservation-test")
            state.register_epoch(value)
            work_id = "creator--r0--fixture"
            payload = {"creator": "fixture", "round": 0}
            limits = {
                "calls": 100,
                "input_tokens": 1000,
                "output_tokens": 1000,
            }
            logical_id = f"{work_id}--inference"

            self.assertTrue(
                state.claim(value.epoch_id, work_id, "creator", payload)
            )
            state.reserve_inference(
                value.epoch_id, logical_id, 3, 30, 30, limits
            )
            # Reserving twice inside one controller attempt stays idempotent.
            state.reserve_inference(
                value.epoch_id, logical_id, 3, 30, 30, limits
            )
            state.fail(value.epoch_id, work_id, "provider failure")

            self.assertTrue(
                state.claim(value.epoch_id, work_id, "creator", payload)
            )
            state.reserve_inference(
                value.epoch_id, logical_id, 3, 30, 30, limits
            )
            state.reconcile_inference(
                value.epoch_id, logical_id, 1, 10, 10
            )

            # The failed first attempt remains conservatively reserved, while
            # the successful second attempt is reconciled to actual usage.
            self.assertEqual(
                state.inference_usage(value.epoch_id),
                {"calls": 4, "input_tokens": 40, "output_tokens": 40},
            )


if __name__ == "__main__":
    unittest.main()
