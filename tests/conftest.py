"""
Fixtures compartilhadas da suíte.

- Redis: FakeRedis novo por teste (servidor próprio), injetado por monkeypatch
  nos módulos que importam `redis_client` por nome.
- GitHub: `fetch_github_user` substituído por uma função falsa que conta chamadas.
- Tempo: `time.time` substituído no módulo dos limitadores (não no módulo global).
- Rede: qualquer tentativa de conexão ou resolução de DNS falha o teste.
"""

import socket
from types import SimpleNamespace

import fakeredis
import httpx
import pytest
import pytest_asyncio

import app.middlewares.rate_limiter as fixed_window_module
import app.middlewares.sliding_window_rate_limiter as sliding_window_module
import app.routes.github as github_module
from app.main import create_app
from tests.fakes import GITHUB_USERS, FakeGitHub


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("Teste tentou acessar a rede")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest_asyncio.fixture
async def fake_redis(monkeypatch):
    redis = fakeredis.aioredis.FakeRedis(
        server=fakeredis.FakeServer(), decode_responses=True
    )
    monkeypatch.setattr(fixed_window_module, "redis_client", redis)
    monkeypatch.setattr(sliding_window_module, "redis_client", redis)
    monkeypatch.setattr(github_module, "redis_client", redis)
    yield redis
    await redis.aclose()


@pytest.fixture
def fake_github(monkeypatch):
    fake = FakeGitHub(GITHUB_USERS)
    monkeypatch.setattr(github_module, "fetch_github_user", fake)
    return fake


@pytest.fixture
def fake_clock(monkeypatch):
    """Relógio controlável. Começa em um múltiplo exato de 60 (início de janela)."""
    clock = SimpleNamespace(now=1_020_000.0)
    clock.advance = lambda seconds: setattr(clock, "now", clock.now + seconds)
    fake_time = SimpleNamespace(time=lambda: clock.now)
    monkeypatch.setattr(fixed_window_module, "time", fake_time)
    monkeypatch.setattr(sliding_window_module, "time", fake_time)
    return clock


@pytest.fixture
def fixed_window_limits(monkeypatch):
    monkeypatch.setattr(fixed_window_module, "RATE_LIMIT_MAX_REQUESTS", 10)
    monkeypatch.setattr(fixed_window_module, "RATE_LIMIT_WINDOW_SECONDS", 60)


@pytest_asyncio.fixture
async def make_client():
    """Fábrica de clientes: cada um representa um IP de origem."""
    clients: list[httpx.AsyncClient] = []

    def _make(algorithm: str = "fixed_window", ip: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=create_app(algorithm), client=(ip, 50000))
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        clients.append(client)
        return client

    yield _make
    for client in clients:
        await client.aclose()
