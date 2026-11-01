"""Regression checks for source grounding and bounded, durable retry history."""

import hashlib
import io
import json
import tarfile
from unittest.mock import Mock

import pytest
from bedrock_model import BedrockJSONModel, make_classifier
from errors import TreatmentDependencyError
from evidence_retrieval import make_evidence_retriever
from queue_worker import run_once
from retry import generate_with_bounded_retries
from source_context import manifest_sources


@pytest.mark.parametrize("status", ["VALIDATED", "ESCALATE"])
def test_queue_acknowledges_only_final_outcomes(status):
    sqs = Mock()
    sqs.receive_message.return_value = {"Messages": [{
        "Body": '{"event_id":"test"}', "ReceiptHandle": "receipt",
    }]}
    assert run_once(sqs, "queue", lambda _: {"status": status})
    sqs.delete_message.assert_called_once_with(QueueUrl="queue", ReceiptHandle="receipt")


@pytest.mark.parametrize("body", ["invalid json", "[]", '{"event_id":"test"}'])
def test_queue_retains_invalid_or_unfinished_messages(body):
    sqs = Mock()
    sqs.receive_message.return_value = {"Messages": [{
        "Body": body, "ReceiptHandle": "receipt",
    }]}
    assert run_once(sqs, "queue", lambda _: {"status": "PROCESSING"})
    sqs.delete_message.assert_not_called()


def test_queue_retains_message_when_durable_write_fails():
    sqs = Mock()
    sqs.receive_message.return_value = {"Messages": [{
        "Body": '{"event_id":"test"}', "ReceiptHandle": "receipt",
    }]}
    process = Mock(side_effect=TreatmentDependencyError("audit unavailable"))
    assert run_once(sqs, "queue", process)
    sqs.delete_message.assert_not_called()


def test_retrieval_deduplicates_citations_and_omits_live_state_from_query():
    client = Mock()
    item = {"content": {"text": "Use Git as the source of truth."},
            "location": {"s3Location": {"uri": "s3://test/policy.md"}}}
    client.retrieve.return_value = {"retrievalResults": [item, item]}
    retrieve = make_evidence_retriever("test", client=client)
    result = retrieve({"resources": [{"kind": "Deployment", "name": "cartservice",
                                       "live_state": "private-value"}]}, {})
    assert len(result) == 1
    query = client.retrieve.call_args.kwargs["retrievalQuery"]["text"]
    assert "cartservice" in query
    assert "private-value" not in query


def _archive(name, body=b"kind: Deployment\n"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        member = tarfile.TarInfo(name)
        member.size = len(body)
        bundle.addfile(member, io.BytesIO(body))
    archive = buffer.getvalue()
    return {"source_archive": archive, "source_sha256": hashlib.sha256(archive).hexdigest()}


def test_sources_preserve_exact_paths_and_content():
    name = "apps/online-boutique/cartservice.yaml"
    assert manifest_sources(_archive(name)) == {name: "kind: Deployment\n"}


def test_source_checksum_is_required():
    golden = _archive("apps/online-boutique/cartservice.yaml")
    golden["source_sha256"] = "0" * 64
    with pytest.raises(TreatmentDependencyError, match="checksum"):
        manifest_sources(golden)


@pytest.mark.parametrize("name", ["../escape.yaml", "/absolute.yaml"])
def test_source_rejects_unsafe_paths(name):
    with pytest.raises(TreatmentDependencyError, match="unsafe"):
        manifest_sources(_archive(name))


def test_retry_stops_after_three_attempts_and_records_each():
    recorded = []
    generate = Mock(return_value={"patch": "bad"})
    result = generate_with_bounded_retries(
        generate, lambda _: ["does not apply"], on_attempt=recorded.append
    )
    assert result["status"] == "ESCALATE"
    assert generate.call_count == 3
    assert len(recorded) == 3
    assert generate.call_args.args[0] == ["does not apply"]


def test_prior_attempt_survives_later_provider_failure():
    recorded = []
    generate = Mock(side_effect=[{}, TreatmentDependencyError("provider unavailable")])
    with pytest.raises(TreatmentDependencyError):
        generate_with_bounded_retries(
            generate, lambda _: ["invalid patch"], on_attempt=recorded.append
        )
    assert len(recorded) == 1
    assert recorded[0]["verdict"] == "REJECT"


def test_bedrock_receives_actual_output_schema():
    client = Mock()
    client.converse.return_value = {"output": {"message": {"content": [{"text": "{}"}]}}}
    model = BedrockJSONModel("test-model", client=client)
    assert model.generate_json("classify_v1.md", {}) == {}
    system = client.converse.call_args.kwargs["system"][0]["text"]
    schema = json.loads(system.split("Required output JSON schema:\n", 1)[1])
    assert "severity" in schema["required"]
    assert schema["additionalProperties"] is False


def test_classification_repair_has_one_retry_and_audits_both():
    model = Mock(model_id="test-model")
    model.generate_json.return_value = {}
    records = []
    classify = make_classifier(model, repair=True, audit=records.append)
    classify({"event_id": "example"}, {}, [])
    assert model.generate_json.call_count == 2
    assert [record["attempt"] for record in records] == [1, 2]
    assert all(record["verdict"] == "REJECT" for record in records)


def test_first_pass_evaluation_does_not_repair():
    model = Mock(model_id="test-model")
    model.generate_json.return_value = {}
    make_classifier(model)({"event_id": "example"}, {}, [])
    assert model.generate_json.call_count == 1
