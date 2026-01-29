from agents.common.openai_client import create_text


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


def test_create_text_returns_output():
    client = _make_client("Review summary")
    output = create_text(
        client=client,
        model="gpt-4o-mini",
        system_prompt="sys",
        user_prompt="user",
    )
    assert output == "Review summary"
