"""Unit tests for shared.core.api_response — success_response and error_response helpers."""

from typing import Any, Generator

import pytest
from flask import Flask as _Flask

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures — minimal Flask app so jsonify works
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> _Flask:
    from flask import Flask

    return Flask(__name__)


@pytest.fixture()
def ctx(app: _Flask) -> Generator[None, None, None]:
    with app.app_context():
        yield


# ---------------------------------------------------------------------------
# success_response
# ---------------------------------------------------------------------------


class TestSuccessResponse:
    def test_default_status_200(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        _, status = success_response({"key": "value"})
        assert status == 200

    def test_custom_status_201(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        _, status = success_response({}, status_code=201)
        assert status == 201

    def test_success_field_is_true(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response("hello")
        body = response.get_json()
        assert body["success"] is True

    def test_error_field_is_none(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response("hello")
        body = response.get_json()
        assert body["error"] is None

    def test_data_field_matches_input(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        payload = {"items": [1, 2, 3]}
        response, _ = success_response(payload)
        assert response.get_json()["data"] == payload

    def test_data_none_when_no_data(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response()
        assert response.get_json()["data"] is None

    def test_data_can_be_list(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response([1, 2, 3])
        assert response.get_json()["data"] == [1, 2, 3]

    def test_data_can_be_string(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response("ok")
        assert response.get_json()["data"] == "ok"

    def test_data_can_be_numeric(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response(42)
        assert response.get_json()["data"] == 42

    def test_content_type_is_json(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        response, _ = success_response({})
        assert "application/json" in response.content_type


# ---------------------------------------------------------------------------
# error_response
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_default_status_500(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        _, status = error_response("Something went wrong")
        assert status == 500

    def test_custom_status_400(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        _, status = error_response("Bad request", status_code=400)
        assert status == 400

    def test_custom_status_404(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        _, status = error_response("Not found", status_code=404)
        assert status == 404

    def test_success_field_is_false(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        response, _ = error_response("oops")
        assert response.get_json()["success"] is False

    def test_data_field_is_none(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        response, _ = error_response("oops")
        assert response.get_json()["data"] is None

    def test_error_field_matches_message(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        msg = "something broke"
        response, _ = error_response(msg)
        assert response.get_json()["error"] == msg

    def test_empty_message(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        response, _ = error_response("")
        assert response.get_json()["error"] == ""

    def test_content_type_is_json(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        response, _ = error_response("err")
        assert "application/json" in response.content_type


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


class TestEnvelopeShape:
    _EXPECTED_KEYS = {"success", "data", "error"}

    def test_success_response_has_exactly_three_keys(self, ctx: Any) -> None:
        from shared.core.api_response import success_response

        body = success_response({})[0].get_json()
        assert set(body.keys()) == self._EXPECTED_KEYS

    def test_error_response_has_exactly_three_keys(self, ctx: Any) -> None:
        from shared.core.api_response import error_response

        body = error_response("err")[0].get_json()
        assert set(body.keys()) == self._EXPECTED_KEYS


class TestUnwrapData:
    def test_unwraps_envelope(self) -> None:
        from shared.core.api_response import unwrap_data

        assert unwrap_data({"success": True, "data": {"a": 1}, "error": None}) == {"a": 1}

    def test_passes_through_bare_payload(self) -> None:
        from shared.core.api_response import unwrap_data

        assert unwrap_data({"a": 1}) == {"a": 1}
