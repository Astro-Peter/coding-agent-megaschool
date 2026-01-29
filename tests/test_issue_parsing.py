from agents.planner_agent.planner import build_plan


class _StubOpenAIClient:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    class responses:
        @staticmethod
        def create(*, model, input):  # pragma: no cover - delegating stub
            raise NotImplementedError


def _make_client(response_text: str):
    client = _StubOpenAIClient(response_text)

    def _create(*, model, input):
        class _Response:
            output_text = response_text

        return _Response()

    client.responses.create = _create
    return client


def test_build_plan_parses_json():
    client = _make_client('{"summary": "Do X", "steps": ["A", "B"]}')
    plan = build_plan("Title", "Body", openai_client=client, model="gpt-4o-mini")
    assert plan.summary == "Do X"
    assert plan.steps == ["A", "B"]


def test_build_plan_fallback_on_invalid_json():
    client = _make_client("not json")
    plan = build_plan("Title", "Body", openai_client=client, model="gpt-4o-mini")
    assert plan.summary
    assert len(plan.steps) >= 1
