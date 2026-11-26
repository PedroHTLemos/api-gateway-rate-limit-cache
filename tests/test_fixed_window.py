"""Fixed Window: contador por IP e janela de tempo."""

import pytest

pytestmark = pytest.mark.usefixtures("fixed_window_limits")

LIMIT = 10


async def test_ate_o_limite_responde_200_com_headers_corretos(make_client, fake_redis, fake_clock):
    client = make_client(algorithm="fixed_window")

    for i in range(1, LIMIT + 1):
        response = await client.get("/")
        assert response.status_code == 200, f"requisição {i} deveria passar"
        assert response.headers["X-RateLimit-Limit"] == str(LIMIT)
        assert response.headers["X-RateLimit-Remaining"] == str(LIMIT - i)


async def test_requisicao_acima_do_limite_recebe_429_com_retry_after(
    make_client, fake_redis, fake_clock
):
    client = make_client(algorithm="fixed_window")
    for _ in range(LIMIT):
        await client.get("/")

    response = await client.get("/")

    assert response.status_code == 429
    assert response.json() == {"detail": "Too Many Requests"}
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 60


async def test_health_nao_e_limitado(make_client, fake_redis, fake_clock):
    client = make_client(algorithm="fixed_window")
    for _ in range(LIMIT):
        await client.get("/")
    assert (await client.get("/")).status_code == 429

    for _ in range(LIMIT * 2):
        response = await client.get("/health")
        assert response.status_code == 200
        assert "X-RateLimit-Limit" not in response.headers


async def test_ips_diferentes_tem_contadores_separados(make_client, fake_redis, fake_clock):
    client_a = make_client(algorithm="fixed_window", ip="10.0.0.1")
    client_b = make_client(algorithm="fixed_window", ip="10.0.0.2")
    for _ in range(LIMIT):
        await client_a.get("/")
    assert (await client_a.get("/")).status_code == 429

    response = await client_b.get("/")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == str(LIMIT - 1)


async def test_contador_recomeca_quando_a_janela_vira(make_client, fake_redis, fake_clock):
    client = make_client(algorithm="fixed_window")
    for _ in range(LIMIT):
        await client.get("/")
    assert (await client.get("/")).status_code == 429

    fake_clock.advance(60)  # entra na próxima janela de 60 s

    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == str(LIMIT - 1)


async def test_chave_nasce_com_ttl_junto_com_o_primeiro_incremento(
    make_client, fake_redis, fake_clock
):
    client = make_client(algorithm="fixed_window")

    await client.get("/")

    key = f"ratelimit:fixed:127.0.0.1:{int(fake_clock.now // 60)}"
    assert await fake_redis.get(key) == "1"
    assert 0 < await fake_redis.ttl(key) <= 60
