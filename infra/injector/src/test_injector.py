"""Fast standard-library unit tests for the injector's selection and API contract."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("injector", HERE / "injector.py")
injector = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(injector)


class InjectorTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "id": "SYN-20261022-0006", "synthetic": True, "kind": "Deployment",
            "namespace": "online-boutique", "name": "frontend", "class": "benign",
            "severity": 0, "intent": "intentional-benign", "split": "test",
            "mutation": [{"op": "add", "path": "/metadata/annotations/experiment.drift~1note", "value": "ok"}],
        }

    def test_record_target_must_match_namespace_and_allowlist(self):
        injector._validate_record(self.record, self.record["id"])
        outside = dict(self.record, namespace="kube-system")
        with self.assertRaisesRegex(ValueError, "outside"):
            injector._validate_record(outside, outside["id"])

    def test_record_cannot_patch_managed_fields(self):
        unsafe = dict(self.record, mutation=[{"op": "replace", "path": "/metadata/managedFields", "value": []}])
        with self.assertRaisesRegex(ValueError, "forbidden"):
            injector._validate_record(unsafe, unsafe["id"])

    def test_record_identifier_is_checked(self):
        with self.assertRaisesRegex(ValueError, "not a synthetic"):
            injector._validate_record(self.record, "SYN-20261022-9999")

    def test_patch_uses_named_field_manager_and_json_patch_media_type(self):
        class FakeApi:
            def __init__(self): self.patch_args = None
            def read_namespaced_deployment(self, **kwargs):
                return SimpleNamespace(metadata=SimpleNamespace(annotations={"existing": "kept"}))
            def patch_namespaced_deployment(self, **kwargs):
                self.patch_args = kwargs
                return SimpleNamespace(metadata=SimpleNamespace(resource_version="7"))

        api = FakeApi()
        with patch.object(injector, "_api_for", return_value=(api, "deployment")):
            response = injector._patch_record(object(), self.record)
        self.assertEqual(response.metadata.resource_version, "7")
        self.assertEqual(api.patch_args["field_manager"], "drift-injector")
        self.assertEqual(api.patch_args["_content_type"], "application/json-patch+json")
        self.assertEqual(api.patch_args["body"], self.record["mutation"])

    def test_missing_annotations_are_initialized_without_replacing_existing_values(self):
        class FakeApi:
            def read_namespaced_deployment(self, **kwargs):
                return SimpleNamespace(metadata=SimpleNamespace(annotations=None))
            def patch_namespaced_deployment(self, **kwargs): self.patch_body = kwargs["body"]; return object()

        api = FakeApi()
        with patch.object(injector, "_api_for", return_value=(api, "deployment")):
            injector._patch_record(object(), self.record)
        self.assertEqual(api.patch_body[0], {"op": "add", "path": "/metadata/annotations", "value": {}})
        self.assertEqual(api.patch_body[1], self.record["mutation"][0])

    def test_handler_writes_t_inject_to_ledger_after_successful_patch(self):
        events = []
        fake_client = SimpleNamespace(close=lambda: None)
        with patch.object(injector, "_load_corpus", return_value={self.record["id"]: self.record}), \
             patch.object(injector, "_api_client", return_value=fake_client), \
             patch.object(injector, "_patch_record", side_effect=lambda *_: events.append("patch") or SimpleNamespace(metadata=SimpleNamespace(resource_version="9"))), \
             patch.object(injector, "_write_ledger", side_effect=lambda *args: events.append(("ledger", args[2], args[3]))), \
             patch.dict(os.environ, {"LEDGER_TABLE": "ledger-test"}):
            result = injector.handler({"instance_id": self.record["id"], "trial_id": "unit-1"}, None)
        self.assertEqual(events[0], "patch")
        self.assertEqual(events[1][0], "ledger")
        self.assertTrue(events[1][1].endswith("Z"))
        self.assertIsInstance(events[1][2], int)
        self.assertEqual(result["field_manager"], "drift-injector")
        self.assertEqual(result["resource_version"], "9")


if __name__ == "__main__":
    unittest.main()
