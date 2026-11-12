import json

import pytest

from app.config import Settings
from app.llm_explainer import (
    LLMError,
    LLMExplainer,
    build_user_prompt,
    fallback_diagnosis,
    parse_diagnosis,
)
from app.models import Diagnosis, PodLogs, PodSummary


def test_parse_plain_json():
    diagnosis = parse_diagnosis(
        json.dumps(
            {
                "summary": "Pod is crash looping.",
                "likely_root_cause": "exit 1",
                "suggested_fix": "fix the command",
                "kubectl_commands": ["kubectl logs x -n y"],
                "confidence": 0.7,
            }
        )
    )
    assert diagnosis.summary == "Pod is crash looping."
    assert diagnosis.likely_root_cause == "exit 1"
    assert diagnosis.kubectl_commands == ["kubectl logs x -n y"]
    assert diagnosis.confidence == 0.7


def test_parse_strips_markdown_fences():
    body = {
        "summary": "Image cannot be pulled.",
        "likely_root_cause": "typo in image name",
        "suggested_fix": "correct the tag",
        "kubectl_commands": ["kubectl describe pod a -n b"],
        "confidence": 0.9,
    }
    text = "```json\n" + json.dumps(body) + "\n```"
    diagnosis = parse_diagnosis(text)
    assert diagnosis.likely_root_cause == "typo in image name"
    assert diagnosis.confidence == 0.9


def test_parse_clamps_confidence_and_flattens_commands():
    diagnosis = parse_diagnosis(
        json.dumps(
            {
                "summary": "s",
                "likely_root_cause": "r",
                "suggested_fix": "f",
                "kubectl_commands": "kubectl get po\nkubectl describe po",
                "confidence": 2.5,
            }
        )
    )
    assert diagnosis.confidence == 1.0
    assert len(diagnosis.kubectl_commands) == 2


def test_parse_extracts_json_from_prose():
    diagnosis = parse_diagnosis(
        'Sure, here you go: {"summary": "s", "likely_root_cause": "r", '
        '"suggested_fix": "f", "kubectl_commands": [], "confidence": 0.2} hope that helps'
    )
    assert diagnosis.summary == "s"


def test_fallback_diagnosis_includes_kubectl(sample_pod):
    diagnosis = fallback_diagnosis("not json at all", sample_pod)
    assert diagnosis.confidence == 0.0
    assert any(sample_pod.name in cmd and sample_pod.namespace in cmd for cmd in diagnosis.kubectl_commands)
    assert "not json" in diagnosis.summary


def test_build_user_prompt_includes_evidence(sample_pod, sample_events, sample_logs):
    prompt = build_user_prompt(sample_pod, sample_events, sample_logs)
    assert "copilot-demo/crashloop-app-abc123" in prompt
    assert "CrashLoopBackOff" in prompt
    assert "I am about to crash" in prompt
    assert "Back-off restarting" in prompt


class _ChoiceMessage:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _ChoiceMessage(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _Response(self.content)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def test_explain_uses_injected_client(sample_pod, sample_events, sample_logs):
    payload = {
        "summary": "It keeps crashing.",
        "likely_root_cause": "exit 1 in the container command",
        "suggested_fix": "Change the command so the process stays up.",
        "kubectl_commands": ["kubectl logs crashloop-app-abc123 -n copilot-demo --previous"],
        "confidence": 0.88,
    }
    completions = _FakeCompletions(content=json.dumps(payload))
    explainer = LLMExplainer(
        settings=Settings(llm_api_key="test-key", llm_model="llama-3.3-70b-versatile"),
        client=_FakeOpenAI(completions),
    )
    diagnosis = explainer.explain(sample_pod, sample_events, sample_logs)
    assert diagnosis.likely_root_cause == "exit 1 in the container command"
    assert completions.calls, "expected a chat.completions.create call"
    call = completions.calls[0]
    assert call["model"] == "llama-3.3-70b-versatile"
    assert call["response_format"] == {"type": "json_object"}
    assert call["messages"][0]["role"] == "system"


def test_explain_falls_back_on_bad_json(sample_pod, sample_events, sample_logs):
    completions = _FakeCompletions(content="I cannot produce JSON today")
    explainer = LLMExplainer(
        settings=Settings(llm_api_key="test-key"),
        client=_FakeOpenAI(completions),
    )
    diagnosis = explainer.explain(sample_pod, sample_events, sample_logs)
    assert diagnosis.confidence == 0.0
    assert "cannot produce JSON" in diagnosis.summary


def test_missing_api_key_raises_for_hosted_provider():
    explainer = LLMExplainer(settings=Settings(llm_api_key="", llm_base_url="https://api.groq.com/openai/v1"))
    with pytest.raises(LLMError) as exc:
        explainer._get_client()
    assert "LLM_API_KEY" in str(exc.value)
    assert "console.groq.com" in str(exc.value)


def test_ollama_allows_empty_key():
    explainer = LLMExplainer(settings=Settings(llm_api_key="", llm_base_url="http://localhost:11434/v1"))
    client = explainer._get_client()
    assert client is not None
    assert not explainer._supports_json_response_format()
