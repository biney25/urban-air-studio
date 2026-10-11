"""Unit tests for GeminiModel cloud assistant inference and tool execution."""
import io
import json
import urllib.request
import pytest

from api.assistant import GeminiModel, LocalModelUnavailable
from api.assistant_tools import supported_answer


class MockResponse:
    def __init__(self, data):
        self.raw = json.dumps(data).encode("utf-8")
    def read(self):
        return self.raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class DummyTools:
    def __init__(self):
        self.calls = []
        self.results = []
        self.proposal = None
        self.evidence = {}
    def invoke(self, name, args):
        self.calls.append({"name": name, "status": "complete", "evidence_ids": ["dummy_evidence"]})
        self.evidence[("dummy", "dummy_evidence")] = {"id": "dummy_evidence", "kind": "project", "label": "Dummy"}
        res = {"status": "ok", "name": name}
        self.results.append(res)
        return res


def test_gemini_model_status():
    model_without_key = GeminiModel(api_key="")
    status = model_without_key.status()
    assert status["available"] is False
    assert status["state"] == "unavailable"

    model_with_key = GeminiModel(api_key="test-api-key")
    status = model_with_key.status()
    assert status["available"] is True
    assert status["state"] == "ready"
    assert status["model"]["provider"] == "gemini"


def test_gemini_model_infer_single_tool_and_answer(monkeypatch):
    tools = DummyTools()
    model = GeminiModel(api_key="test-api-key")

    call_count = 0
    def mock_urlopen(req, timeout=30):
        nonlocal call_count
        call_count += 1
        body = json.loads(req.data.decode("utf-8"))
        if call_count == 1:
            # First turn: Gemini returns a tool call
            return MockResponse({
                "candidates": [{
                    "content": {
                        "role": "model",
                        "parts": [{
                            "functionCall": {
                                "name": "inspect_study",
                                "args": {}
                            }
                        }]
                    }
                }],
                "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 10}
            })
        else:
            # Second turn: Gemini returns the text answer without digits
            return MockResponse({
                "candidates": [{
                    "content": {
                        "role": "model",
                        "parts": [{
                            "text": "The selected study has been inspected. Review its saved design status and blockers in the evidence cards below."
                        }]
                    }
                }],
                "usageMetadata": {"promptTokenCount": 80, "candidatesTokenCount": 25}
            })

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    res = model.infer("What are the study blockers?", [], tools)
    assert res["type"] == "answer"
    assert len(tools.calls) == 1
    assert tools.calls[0]["name"] == "inspect_study"
    assert supported_answer(res["answer"], tools)
    assert res["stop_reason"] == "end_turn"


def test_gemini_model_infer_fallback_when_digits_present(monkeypatch):
    tools = DummyTools()
    # Simulate a proposal
    tools.proposal = {"validation": {"valid": True}}
    tools.results = [{"user_summary": "The proposal passes the declared geometry and programme constraints and awaits your review. No edit was applied. Physical benefit is not established."}]
    tools.calls = [{"name": "propose_change", "status": "complete", "evidence_ids": ["dummy_evidence"]}]
    tools.evidence[("dummy", "dummy_evidence")] = {"id": "dummy_evidence", "kind": "project", "label": "Dummy"}

    model = GeminiModel(api_key="test-api-key")

    def mock_urlopen(req, timeout=30):
        # Model incorrectly generates raw digits
        return MockResponse({
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "I set the height to 25 meters."}]
                }
            }]
        })

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    res = model.infer("Change height to 25", [], tools)
    assert res["type"] == "answer"
    # Digits must be replaced by authoritative summary
    assert "25" not in res["answer"]
    assert supported_answer(res["answer"], tools)


def test_gemini_model_network_failure(monkeypatch):
    tools = DummyTools()
    model = GeminiModel(api_key="test-api-key")

    def mock_urlopen(req, timeout=30):
        raise OSError("Connection timed out")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    with pytest.raises(LocalModelUnavailable, match="GEMINI_CALL_FAILED"):
        model.infer("Hello", [], tools)
