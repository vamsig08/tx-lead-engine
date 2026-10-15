import httpx
import pytest
import respx

from integrations.http import request_with_retry

URL = "https://example.test/x"


@respx.mock
def test_retries_server_errors_with_backoff():
    route = respx.get(URL).mock(side_effect=[
        httpx.Response(503), httpx.Response(429), httpx.Response(200, json=[]),
    ])
    sleeps = []
    with httpx.Client() as client:
        response = request_with_retry(client, "GET", URL, sleep=sleeps.append)
    assert response.status_code == 200
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]


@respx.mock
def test_gives_up_after_three_retries():
    route = respx.get(URL).mock(return_value=httpx.Response(500))
    sleeps = []
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "GET", URL, sleep=sleeps.append)
    assert route.call_count == 4
    assert sleeps == [1.0, 2.0, 4.0]


@respx.mock
def test_retries_transport_errors():
    respx.get(URL).mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200)])
    with httpx.Client() as client:
        assert request_with_retry(client, "GET", URL, sleep=lambda s: None).status_code == 200


@respx.mock
def test_does_not_retry_client_errors():
    route = respx.get(URL).mock(return_value=httpx.Response(400))
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "GET", URL, sleep=lambda s: None)
    assert route.call_count == 1
