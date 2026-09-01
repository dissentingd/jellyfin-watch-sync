import httpx
import pytest

from jellyfin_watch_sync.errors import describe_error


def _http_status_error(status: int, url: str = "http://jellyfin.test/Users/x/Items") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_mentions_api_key(status):
    message = describe_error(_http_status_error(status))
    assert "JELLYFIN_API_KEY" in message
    assert "API Keys" in message  # points at the actual Dashboard location


def test_404_mentions_user_id():
    message = describe_error(_http_status_error(404))
    assert "JELLYFIN_USER_ID" in message


def test_5xx_mentions_transient():
    message = describe_error(_http_status_error(503))
    assert "transient" in message.lower()


def test_unrecognized_status_still_gets_a_message():
    message = describe_error(_http_status_error(418))
    assert message is not None
    assert "418" in message


def test_connect_error_mentions_jellyfin_url():
    request = httpx.Request("GET", "http://unreachable.test/")
    message = describe_error(httpx.ConnectError("connection refused", request=request))
    assert "JELLYFIN_URL" in message


def test_timeout_mentions_jellyfin_url():
    message = describe_error(httpx.TimeoutException("timed out"))
    assert "JELLYFIN_URL" in message


def test_psycopg_like_error_mentions_dsn():
    class FakeOperationalError(Exception):
        pass

    FakeOperationalError.__module__ = "psycopg.errors"

    message = describe_error(FakeOperationalError("connection refused"))
    assert message is not None
    assert "yamtrack-dsn" in message.lower() or "YAMTRACK_DSN" in message


def test_unrecognized_exception_returns_none():
    assert describe_error(ValueError("something else entirely")) is None
