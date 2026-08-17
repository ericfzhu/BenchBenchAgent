"""Numeric project-resource resolution tests for quota discovery."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bba.quota_project import (
    QuotaDiscoveryError,
    VertexQuotaDiscovery,
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.responses.pop(0)


class TestProjectResourceResolution(unittest.TestCase):
    def test_project_id_is_resolved_before_service_usage_request(self):
        session = FakeSession([
            FakeResponse(
                200,
                {
                    "name": "projects/123456789012",
                    "projectId": "example-project",
                },
            ),
            FakeResponse(200, {"metrics": []}),
        ])
        discovery = VertexQuotaDiscovery(
            "example-project",
            credentials_loader=lambda **_kwargs: (object(), "example-project"),
            session_factory=lambda _credentials: session,
        )

        self.assertEqual(discovery._metrics(), [])
        self.assertEqual(
            session.calls[0][0],
            "https://cloudresourcemanager.googleapis.com/v3/projects/"
            "example-project",
        )
        self.assertEqual(
            session.calls[1][0],
            "https://serviceusage.googleapis.com/v1beta1/projects/"
            "123456789012/services/aiplatform.googleapis.com/"
            "consumerQuotaMetrics",
        )

    def test_configured_project_number_skips_resource_manager_lookup(self):
        session = FakeSession([FakeResponse(200, {"metrics": []})])
        with patch.dict(
            os.environ,
            {"BBA_GCP_PROJECT_NUMBER": "123456789012"},
            clear=False,
        ):
            discovery = VertexQuotaDiscovery(
                "example-project",
                credentials_loader=lambda **_kwargs: (
                    object(),
                    "example-project",
                ),
                session_factory=lambda _credentials: session,
            )
            self.assertEqual(discovery._metrics(), [])

        self.assertEqual(len(session.calls), 1)
        self.assertIn("projects/123456789012/services/", session.calls[0][0])

    def test_resolution_falls_back_to_project_id_on_resource_manager_failure(self):
        session = FakeSession([
            FakeResponse(403, text="permission denied"),
            FakeResponse(200, {"metrics": []}),
        ])
        discovery = VertexQuotaDiscovery(
            "example-project",
            credentials_loader=lambda **_kwargs: (object(), "example-project"),
            session_factory=lambda _credentials: session,
        )
        self.assertEqual(discovery._metrics(), [])
        self.assertEqual(
            session.calls[1][0],
            "https://serviceusage.googleapis.com/v1beta1/projects/"
            "example-project/services/aiplatform.googleapis.com/"
            "consumerQuotaMetrics",
        )



if __name__ == "__main__":
    unittest.main()
