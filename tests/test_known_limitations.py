"""Limitações conhecidas documentadas por testes marcados como xfail."""

import asyncio

import pytest

import app.middlewares.sliding_window_rate_limiter as sliding_window_module
from app.core.api_keys import TIER_LIMITS

CONCURRENT_REQUESTS = 20
# Simula a ida e volta de rede de um Redis real. O fakeredis responde sem ceder
# o event loop, então sem isso as requisições concorrentes não se intercalam.
SIMULATED_RTT_SECONDS = 0.005
NETWORK_COMMANDS = {"zremrangebyscore", "zcard", "zadd", "zrange", "expire"}


class LatencyRedis:
    """Envolve o FakeRedis e adiciona latência de rede aos comandos do limitador."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in NETWORK_COMMANDS:
            return attr

        async def delayed(*args, **kwargs):
            await asyncio.sleep(SIMULATED_RTT_SECONDS)
            return await attr(*args, **kwargs)

        return delayed


@pytest.mark.xfail(
    strict=True,
    reason="check-then-act não atômico; corrigir com script Lua (remover este marcador após a correção)",
)
async def test_sliding_window_nao_ultrapassa_limite_sob_concorrencia(
    make_client, fake_redis, fake_clock, monkeypatch
):
    """
    O Sliding Window faz ZREMRANGEBYSCORE, ZCARD e ZADD em comandos separados.
    Com latência de rede entre eles, requisições concorrentes leem o mesmo ZCARD
    antes de qualquer ZADD e ultrapassam o limite.
    """
    monkeypatch.setattr(sliding_window_module, "redis_client", LatencyRedis(fake_redis))
    limit = TIER_LIMITS["anonymous"]["max_requests"]
    client = make_client(algorithm="sliding_window_log")

    responses = await asyncio.gather(
        *[client.get("/") for _ in range(CONCURRENT_REQUESTS)]
    )

    accepted = sum(1 for r in responses if r.status_code == 200)
    assert accepted <= limit, f"{accepted} aceitas com limite {limit}"
