"""Sliding Window Log: limite por tier, identificação por API key e janela deslizante."""

import asyncio

import pytest

import app.middlewares.sliding_window_rate_limiter as sliding_window_module
from app.core.api_keys import TIER_LIMITS
from tests.fakes import LatencyRedis

ANON_KEY = None
FREE_KEY = "free-key-123"
PREMIUM_KEY = "premium-key-456"


async def _send(client, api_key=None, path="/"):
    headers = {"X-API-Key": api_key} if api_key else {}
    return await client.get(path, headers=headers)


async def _exhaust(client, limit, api_key=None):
    for _ in range(limit):
        assert (await _send(client, api_key)).status_code == 200


@pytest.mark.parametrize(
    "api_key, tier",
    [(ANON_KEY, "anonymous"), (FREE_KEY, "free"), (PREMIUM_KEY, "premium")],
)
async def test_limite_por_tier(make_client, fake_redis, fake_clock, api_key, tier):
    limit = TIER_LIMITS[tier]["max_requests"]
    client = make_client(algorithm="sliding_window_log")

    for i in range(1, limit + 1):
        response = await _send(client, api_key)
        assert response.status_code == 200, f"{tier}: requisição {i} deveria passar"
        assert response.headers["X-RateLimit-Limit"] == str(limit)
        assert response.headers["X-RateLimit-Remaining"] == str(limit - i)
        assert response.headers["X-RateLimit-Tier"] == tier

    rejected = await _send(client, api_key)
    assert rejected.status_code == 429


async def test_chave_invalida_cai_em_anonymous(make_client, fake_redis, fake_clock):
    client = make_client(algorithm="sliding_window_log")
    limit = TIER_LIMITS["anonymous"]["max_requests"]

    for _ in range(limit):
        response = await _send(client, api_key="chave-que-nao-existe")
        assert response.headers["X-RateLimit-Tier"] == "anonymous"

    rejected = await _send(client, api_key="chave-que-nao-existe")
    assert rejected.status_code == 429
    assert rejected.json()["tier"] == "anonymous"


async def test_anonimos_de_ips_diferentes_nao_compartilham_limite(
    make_client, fake_redis, fake_clock
):
    limit = TIER_LIMITS["anonymous"]["max_requests"]
    client_a = make_client(algorithm="sliding_window_log", ip="10.0.0.1")
    client_b = make_client(algorithm="sliding_window_log", ip="10.0.0.2")
    await _exhaust(client_a, limit)
    assert (await _send(client_a)).status_code == 429

    response = await _send(client_b)

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == str(limit - 1)


async def test_429_traz_tier_e_retry_after(make_client, fake_redis, fake_clock):
    client = make_client(algorithm="sliding_window_log")
    await _exhaust(client, TIER_LIMITS["free"]["max_requests"], api_key=FREE_KEY)

    rejected = await _send(client, api_key=FREE_KEY)

    assert rejected.status_code == 429
    body = rejected.json()
    assert body == {"detail": "Too Many Requests", "tier": "free"}
    retry_after = int(rejected.headers["Retry-After"])
    assert 1 <= retry_after <= TIER_LIMITS["free"]["window_seconds"]


async def test_x_ratelimit_tier_presente_nas_respostas_aceitas(
    make_client, fake_redis, fake_clock
):
    client = make_client(algorithm="sliding_window_log")

    response = await _send(client, api_key=PREMIUM_KEY)

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Tier"] == "premium"


async def test_requisicoes_antigas_saem_da_janela_e_novas_sao_aceitas(
    make_client, fake_redis, fake_clock
):
    client = make_client(algorithm="sliding_window_log")
    limit = TIER_LIMITS["anonymous"]["max_requests"]
    await _exhaust(client, limit)
    assert (await _send(client)).status_code == 429

    fake_clock.advance(30)
    # Metade da janela passou: as requisições antigas ainda contam
    assert (await _send(client)).status_code == 429

    fake_clock.advance(31)  # total de 61 s: todas saíram da janela de 60 s
    response = await _send(client)
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == str(limit - 1)


CONCURRENT_REQUESTS = 20


async def test_concorrencia_nao_ultrapassa_limite(make_client, fake_redis, fake_clock, monkeypatch):
    """
    Com latência de rede entre os comandos, 20 requisições concorrentes não podem
    passar do limite anônimo. Antes do script Lua, ZCARD e ZADD em comandos
    separados deixavam todas passarem.
    """
    monkeypatch.setattr(sliding_window_module, "redis_client", LatencyRedis(fake_redis))
    limit = TIER_LIMITS["anonymous"]["max_requests"]
    client = make_client(algorithm="sliding_window_log")

    responses = await asyncio.gather(*[_send(client) for _ in range(CONCURRENT_REQUESTS)])

    accepted = sum(1 for r in responses if r.status_code == 200)
    rejected = sum(1 for r in responses if r.status_code == 429)
    assert accepted == limit
    assert rejected == CONCURRENT_REQUESTS - limit
