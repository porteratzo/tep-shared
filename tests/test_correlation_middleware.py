"""add_correlation_id_middleware: the X-Request-ID that links one request's log lines across services."""

import uuid
from typing import Any

import pytest
from flask import Flask, g

from shared.core.http_utils import correlation_headers
from shared.core.log_setup import add_correlation_id_middleware, request_id_var

pytestmark = pytest.mark.unit


@pytest.fixture()
def client() -> Any:
    app = Flask(__name__)
    add_correlation_id_middleware(app)

    @app.route("/echo")
    def echo() -> Any:
        return {"g": g.request_id, "var": request_id_var.get("-"), "outgoing": correlation_headers()}

    @app.route("/boom")
    def boom() -> Any:
        raise RuntimeError("handler failed")

    return app.test_client()


def test_incoming_request_id_is_adopted_and_echoed(client: Any) -> None:
    resp = client.get("/echo", headers={"X-Request-ID": "req-abc-123"})
    body = resp.get_json()
    assert resp.headers["X-Request-ID"] == "req-abc-123"
    assert body["g"] == "req-abc-123" and body["var"] == "req-abc-123"


def test_missing_request_id_is_generated_as_a_uuid_and_echoed(client: Any) -> None:
    resp = client.get("/echo")
    rid = resp.headers["X-Request-ID"]
    assert str(uuid.UUID(rid)) == rid
    assert resp.get_json()["g"] == rid


def test_each_request_without_a_header_gets_its_own_id(client: Any) -> None:
    assert client.get("/echo").headers["X-Request-ID"] != client.get("/echo").headers["X-Request-ID"]


def test_empty_header_is_treated_as_absent(client: Any) -> None:
    rid = client.get("/echo", headers={"X-Request-ID": ""}).headers["X-Request-ID"]
    assert str(uuid.UUID(rid)) == rid


def test_the_id_is_forwarded_on_outgoing_calls_made_during_the_request(client: Any) -> None:
    body = client.get("/echo", headers={"X-Request-ID": "trace-9"}).get_json()
    assert body["outgoing"]["X-Request-ID"] == "trace-9"


def test_error_responses_still_carry_the_id(client: Any) -> None:
    resp = client.get("/boom", headers={"X-Request-ID": "trace-err"})
    assert resp.status_code == 500 and resp.headers["X-Request-ID"] == "trace-err"


def test_unknown_route_404_still_carries_the_id(client: Any) -> None:
    resp = client.get("/nope", headers={"X-Request-ID": "trace-404"})
    assert resp.status_code == 404 and resp.headers["X-Request-ID"] == "trace-404"
