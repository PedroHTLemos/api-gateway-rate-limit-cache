"""Falhas da origem (GitHub): timeout -> 504; erro HTTP ou de conexão -> 502."""

import httpx
import pytest

import app.routes.github as github_module
from tests.fakes import GITHUB_USERS, FakeGitHub

REQUEST = httpx.Request("GET", "https://api.github.com/users/octocat")


def _status_error(status: int) -> httpx.HTTPStatusError:
    response = httpx.Response(status, request=REQUEST)
    return httpx.HTTPStatusError(f"{status}", request=REQUEST, response=response)


ORIGIN_FAILURES = [
    pytest.param(httpx.ReadTimeout("timeout", request=REQUEST), 504, "GitHub API timeout", id="read-timeout-504"),
    pytest.param(httpx.ConnectTimeout("timeout", request=REQUEST), 504, "GitHub API timeout", id="connect-timeout-504"),
    pytest.param(_status_error(403), 502, "GitHub API error or unreachable", id="http-403-502"),
    pytest.param(_status_error(429), 502, "GitHub API error or unreachable", id="http-429-502"),
    pytest.param(_status_error(500), 502, "GitHub API error or unreachable", id="http-500-502"),
    pytest.param(httpx.ConnectError("refused", request=REQUEST), 502, "GitHub API error or unreachable", id="connect-error-502"),
]


@pytest.mark.parametrize("exc, status, detail", ORIGIN_FAILURES)
async def test_falha_da_origem_mapeada_para_status_http(
    exc, status, detail, make_client, fake_redis, monkeypatch
):
    failing_origin = FakeGitHub(GITHUB_USERS, exc=exc)
    monkeypatch.setattr(github_module, "fetch_github_user", failing_origin)
    client = make_client()

    response = await client.get("/github/users/octocat")

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert failing_origin.calls == ["octocat"]
    # Nada foi cacheado e o lock foi liberado mesmo com a falha
    assert await fake_redis.exists("cache:github:user:octocat") == 0
    assert await fake_redis.exists("lock:github:user:octocat") == 0


async def test_requisicao_seguinte_funciona_apos_falha_da_origem(
    make_client, fake_redis, monkeypatch
):
    origin = FakeGitHub(GITHUB_USERS, exc=httpx.ReadTimeout("timeout", request=REQUEST))
    monkeypatch.setattr(github_module, "fetch_github_user", origin)
    client = make_client()
    assert (await client.get("/github/users/octocat")).status_code == 504

    # A origem volta a responder: a próxima requisição não fica presa no lock
    origin.exc = None
    response = await client.get("/github/users/octocat")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert origin.calls == ["octocat", "octocat"]


async def test_404_da_origem_continua_404(make_client, fake_redis, fake_github):
    client = make_client()

    response = await client.get("/github/users/ghost-user-does-not-exist")

    assert response.status_code == 404
