"""Proteção contra stampede: requisições concorrentes para a mesma chave."""

import asyncio

import app.routes.github as github_module
from tests.fakes import GITHUB_USERS, FakeGitHub

CONCURRENT_REQUESTS = 10


async def test_requisicoes_concorrentes_geram_uma_unica_chamada_a_origem(
    make_client, fake_redis, monkeypatch
):
    slow_origin = FakeGitHub(GITHUB_USERS, delay=0.3)
    monkeypatch.setattr(github_module, "fetch_github_user", slow_origin)
    # Limite alto para o teste de concorrência não ser barrado pelo rate limit
    client = make_client(algorithm="fixed_window")
    monkeypatch.setattr(
        "app.middlewares.rate_limiter.RATE_LIMIT_MAX_REQUESTS", 1000
    )

    responses = await asyncio.gather(
        *[client.get("/github/users/octocat") for _ in range(CONCURRENT_REQUESTS)]
    )

    assert all(r.status_code == 200 for r in responses)
    assert slow_origin.calls == ["octocat"]

    bodies = [r.json() for r in responses]
    origin_responses = [b for b in bodies if b["source"] == "origin"]
    cache_responses = [b for b in bodies if b["source"] == "cache"]
    assert len(origin_responses) == 1
    assert len(cache_responses) == CONCURRENT_REQUESTS - 1
    assert all(b["waited_for_lock"] is True for b in cache_responses)
    assert all(b["data"]["login"] == "octocat" for b in bodies)


async def test_requisicoes_que_esperam_o_lock_recebem_x_cache_hit(
    make_client, fake_redis, monkeypatch
):
    slow_origin = FakeGitHub(GITHUB_USERS, delay=0.3)
    monkeypatch.setattr(github_module, "fetch_github_user", slow_origin)
    monkeypatch.setattr(
        "app.middlewares.rate_limiter.RATE_LIMIT_MAX_REQUESTS", 1000
    )
    client = make_client(algorithm="fixed_window")

    responses = await asyncio.gather(
        *[client.get("/github/users/octocat") for _ in range(CONCURRENT_REQUESTS)]
    )

    statuses = sorted(r.headers["X-Cache"] for r in responses)
    assert statuses == ["HIT"] * (CONCURRENT_REQUESTS - 1) + ["MISS"]


async def test_lock_e_liberado_apos_a_rajada(make_client, fake_redis, monkeypatch):
    slow_origin = FakeGitHub(GITHUB_USERS, delay=0.3)
    monkeypatch.setattr(github_module, "fetch_github_user", slow_origin)
    monkeypatch.setattr(
        "app.middlewares.rate_limiter.RATE_LIMIT_MAX_REQUESTS", 1000
    )
    client = make_client(algorithm="fixed_window")

    await asyncio.gather(*[client.get("/github/users/octocat") for _ in range(CONCURRENT_REQUESTS)])

    assert await fake_redis.exists("lock:github:user:octocat") == 0
