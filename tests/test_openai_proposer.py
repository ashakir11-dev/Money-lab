"""OpenAI proposer calls. HTTP goes through httpx.MockTransport; nothing here uses the network."""

from __future__ import annotations

import json

import httpx
import pytest

from moneylab.decision.measure import changed_axes
from moneylab.decision.proposer import (
    FallbackProposer,
    OpenAIProposerError,
    ProposerOverreach,
    Reading,
    StyleBudget,
    ThesisIncomplete,
    open_proposer,
)
from moneylab.engine.config import resolve
from moneylab.settings import Settings

FAKE_KEY = "sk-test-openai"
CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _budget() -> StyleBudget:
    return StyleBudget(
        remaining={"bounce": 10, "breakout": 10, "trend": 10, "rotation": 10},
        universe=("AAA", "BBB"),
        open_family="breakout",
    )


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_absent_key_keeps_the_deterministic_fallback_and_makes_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    hit: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hit.append(request)
        return httpx.Response(500)

    with _client(handler) as client:
        proposer = open_proposer(client=client)
        assert proposer.__class__.__name__ == "FallbackProposer"
        draft = proposer.write_thesis("breakout", _budget())
    assert hit == []
    assert draft.signals == ("highest_high",)
    assert Settings().cost_bps_per_side == 0.0


def test_blank_key_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert open_proposer().__class__.__name__ == "FallbackProposer"


def test_openai_key_calls_chat_completions_for_one_thesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.delenv("MONEYLAB_OPENAI_MODEL", raising=False)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == CHAT_URL
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert FAKE_KEY.encode() not in request.content
        assert FAKE_KEY not in str(request.url)
        assert body["model"] == "gpt-4o-mini"
        assert body["response_format"] == {"type": "json_object"}
        system = body["messages"][0]["content"].lower()
        assert "thesis" in system
        assert "one next change" in system
        assert "kill" in system and "freeze" in system and "paper" in system
        return _completion(
            json.dumps(
                {
                    "family": "breakout",
                    "claim": "Breakouts continue for ten sessions.",
                    "signals": ["highest_high"],
                    "falsifier": "Active excess is not positive.",
                    "cost_bps_per_side": 50,
                }
            )
        )

    with _client(handler) as client:
        proposer = open_proposer(client=client)
        assert proposer.__class__.__name__ == "ModelProposer"
        draft = proposer.write_thesis("breakout", _budget())
    assert len(seen) == 1
    assert draft.claim == "Breakouts continue for ten sessions."
    assert draft.signals == ("highest_high",)
    assert resolve(draft.recipe)["portfolio"]["cost_bps_per_side"] == 0.0


def test_model_name_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MONEYLAB_OPENAI_MODEL", "gpt-4.1-mini")
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return _completion(json.dumps({"family": "trend"}))

    with _client(handler) as client:
        family = open_proposer(client=client).propose_family(_budget())
    assert family == "trend"
    assert seen[0]["model"] == "gpt-4.1-mini"


def test_blank_model_override_uses_the_cheap_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MONEYLAB_OPENAI_MODEL", "  ")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(json.loads(request.content)["model"]))
        return _completion(json.dumps({"family": "bounce"}))

    with _client(handler) as client:
        assert open_proposer(client=client).propose_family(_budget()) == "bounce"
    assert seen == ["gpt-4o-mini"]


def test_model_cannot_kill_freeze_or_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    def chat(payload: dict[str, object]) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
            return _completion(json.dumps(payload))

        return _client(handler)

    with chat({"verdict": "kill", "bucket": "exit"}) as client, pytest.raises(ProposerOverreach):
        open_proposer(client=client).name_bucket(Reading(idle_cash="benchmark"))
    with chat({"bucket": "freeze"}) as client, pytest.raises(ProposerOverreach):
        open_proposer(client=client).name_bucket(Reading(idle_cash="benchmark"))
    with chat({"bucket": "paper"}) as client, pytest.raises(ProposerOverreach):
        open_proposer(client=client).name_bucket(Reading(idle_cash="benchmark"))


def test_model_change_stays_on_one_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    parent = FallbackProposer().write_thesis("breakout", _budget()).recipe

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(
            json.dumps(
                {
                    "bucket": "entry",
                    "reason": "replace the whole recipe",
                    "change": "everything",
                    "cost_bps_per_side": 50,
                }
            )
        )

    with _client(handler) as client:
        child = open_proposer(client=client).propose_change(parent, "exit")
    assert changed_axes(resolve(parent), resolve(child)) == ["exit"]
    assert resolve(child)["portfolio"]["cost_bps_per_side"] == 0.0


def test_model_thesis_cannot_name_a_signal_outside_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        return _completion(
            json.dumps({"family": "breakout", "signals": ["not_in_catalog"], "claim": "no"})
        )

    with (
        _client(handler) as client,
        pytest.raises(ThesisIncomplete, match="signal_not_in_catalog"),
    ):
        open_proposer(client=client).write_thesis("breakout", _budget())


def test_openai_error_does_not_include_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-live-should-not-leak"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(401, json={"error": {"message": f"rejected {secret}"}})

    with _client(handler) as client, pytest.raises(OpenAIProposerError) as caught:
        open_proposer(client=client).propose_family(_budget())
    message = str(caught.value)
    assert secret not in message
    assert "401" in message


def test_injected_completion_does_not_call_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    hit: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hit.append(request)
        return httpx.Response(500)

    def complete(_: str) -> str:
        return json.dumps({"family": "rotation"})

    with _client(handler) as client:
        family = open_proposer(complete, client=client).propose_family(_budget())
    assert family == "rotation"
    assert hit == []
