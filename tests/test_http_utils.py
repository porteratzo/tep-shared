"""Unit tests for shared.core.http_utils — correlation_headers()."""

from typing import Generator

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture()
def reset_request_id() -> Generator[None, None, None]:
    """Ensure request_id_var starts and ends at its default for each test."""
    from shared.core.log_setup import request_id_var

    token = request_id_var.set("-")
    try:
        yield
    finally:
        request_id_var.reset(token)


class TestCorrelationHeaders:
    def test_returns_empty_dict_when_no_request_in_flight(self, reset_request_id: None) -> None:
        from shared.core.http_utils import correlation_headers

        assert correlation_headers() == {}

    def test_returns_x_request_id_header_when_set(self, reset_request_id: None) -> None:
        from shared.core.http_utils import correlation_headers
        from shared.core.log_setup import request_id_var

        request_id_var.set("abc-123")
        assert correlation_headers() == {"X-Request-ID": "abc-123"}

    def test_returned_dict_has_exactly_one_key_when_set(self, reset_request_id: None) -> None:
        from shared.core.http_utils import correlation_headers
        from shared.core.log_setup import request_id_var

        request_id_var.set("some-id")
        headers = correlation_headers()
        assert list(headers.keys()) == ["X-Request-ID"]

    def test_default_sentinel_value_is_treated_as_absent(self, reset_request_id: None) -> None:
        from shared.core.http_utils import correlation_headers
        from shared.core.log_setup import request_id_var

        request_id_var.set("-")
        assert correlation_headers() == {}

    def test_empty_string_request_id_is_propagated(self, reset_request_id: None) -> None:
        from shared.core.http_utils import correlation_headers
        from shared.core.log_setup import request_id_var

        request_id_var.set("")
        assert correlation_headers() == {"X-Request-ID": ""}
