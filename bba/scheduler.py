"""Deterministic bounded local scheduling helpers."""

from __future__ import annotations

import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Sequence, Tuple


class BoundedScheduler:
    def __init__(self, workers: int = 4, per_publisher: int = 2):
        if workers < 1 or per_publisher < 1:
            raise ValueError("scheduler limits must be positive")
        self.workers = workers
        self.per_publisher = per_publisher
        self._publisher_limits: Dict[str, BoundedSemaphore] = {}

    @contextmanager
    def _publisher_slot(self, publisher: str) -> Iterator[None]:
        semaphore = self._publisher_limits.setdefault(
            publisher, BoundedSemaphore(self.per_publisher)
        )
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    def map(
        self,
        work: Sequence[Tuple[str, str, Callable[[], Any]]],
    ) -> Mapping[str, Any]:
        ordered = sorted(work, key=lambda item: item[0])
        results: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures: Dict[str, Future] = {}
            for work_id, publisher, callback in ordered:
                def run(selected=callback, selected_publisher=publisher):
                    with self._publisher_slot(selected_publisher):
                        return selected()
                context = contextvars.copy_context()
                futures[work_id] = executor.submit(context.run, run)
            for work_id, _publisher, _callback in ordered:
                results[work_id] = futures[work_id].result()
        return results
