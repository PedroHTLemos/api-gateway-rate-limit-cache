"""Cache-aside da rota /github/users/{username} e cabeçalho X-Cache."""


async def test_primeira_chamada_e_miss_da_origem(make_client, fake_redis, fake_github):
    client = make_client()

    response = await client.get("/github/users/octocat")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    body = response.json()
    assert body["source"] == "origin"
    assert body["data"]["login"] == "octocat"


async def test_segunda_chamada_e_hit_do_cache(make_client, fake_redis, fake_github):
    client = make_client()
    await client.get("/github/users/octocat")

    response = await client.get("/github/users/octocat")

    assert response.status_code == 200
    assert response.headers["X-Cache"] == "HIT"
    body = response.json()
    assert body["source"] == "cache"
    assert body["data"]["login"] == "octocat"


async def test_origem_e_chamada_uma_unica_vez_para_duas_requisicoes(
    make_client, fake_redis, fake_github
):
    client = make_client()

    await client.get("/github/users/octocat")
    await client.get("/github/users/octocat")

    assert fake_github.calls == ["octocat"]


async def test_x_cache_e_na_para_raiz_e_health(make_client, fake_redis, fake_github):
    client = make_client()

    assert (await client.get("/")).headers["X-Cache"] == "N/A"
    assert (await client.get("/health")).headers["X-Cache"] == "N/A"


async def test_usuario_inexistente_da_404_e_nao_e_cacheado(
    make_client, fake_redis, fake_github
):
    client = make_client()

    first = await client.get("/github/users/ghost-user-does-not-exist")
    second = await client.get("/github/users/ghost-user-does-not-exist")

    assert first.status_code == 404
    assert second.status_code == 404
    assert await fake_redis.exists("cache:github:user:ghost-user-does-not-exist") == 0
    # Sem cache, cada chamada chega à origem
    assert fake_github.calls == ["ghost-user-does-not-exist", "ghost-user-does-not-exist"]


async def test_delete_remove_entrada_do_cache(make_client, fake_redis, fake_github):
    client = make_client()
    await client.get("/github/users/octocat")
    assert await fake_redis.exists("cache:github:user:octocat") == 1

    response = await client.delete("/github/users/octocat/cache")

    assert response.status_code == 200
    assert response.json() == {"message": "Cache purged for user 'octocat'", "purged": True}
    assert await fake_redis.exists("cache:github:user:octocat") == 0

    # Próxima leitura volta a ser MISS
    after = await client.get("/github/users/octocat")
    assert after.headers["X-Cache"] == "MISS"
    assert fake_github.calls == ["octocat", "octocat"]


async def test_delete_de_entrada_inexistente_da_404(make_client, fake_redis, fake_github):
    client = make_client()

    response = await client.delete("/github/users/octocat/cache")

    assert response.status_code == 404


async def test_lock_e_liberado_apos_a_requisicao(make_client, fake_redis, fake_github):
    client = make_client()

    await client.get("/github/users/octocat")

    assert await fake_redis.exists("lock:github:user:octocat") == 0


async def test_lock_e_liberado_mesmo_quando_usuario_nao_existe(
    make_client, fake_redis, fake_github
):
    client = make_client()

    await client.get("/github/users/ghost-user-does-not-exist")

    assert await fake_redis.exists("lock:github:user:ghost-user-does-not-exist") == 0
