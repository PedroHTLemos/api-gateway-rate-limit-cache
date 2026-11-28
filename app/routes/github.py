import asyncio
import json
import time
import uuid
import httpx
from fastapi import APIRouter, HTTPException, Request

from app.core.redis_client import redis_client
from app.core.github_client import fetch_github_user
from app.core.config import CACHE_TTL_SECONDS

router = APIRouter(prefix="/github", tags=["github"])

LOCK_TTL_SECONDS = 10
LOCK_WAIT_RETRY_DELAY = 0.1
LOCK_WAIT_MAX_ATTEMPTS = 30  # 30 * 0.1s = até 3s de espera

# Libera o lock só se o valor ainda for o token de quem o adquiriu. GET e DEL
# separados deixariam uma janela em que o lock pode expirar e ser re-adquirido.
RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


async def _fetch_from_origin(username: str):
    """Traduz falhas da origem em respostas HTTP: timeout -> 504; erro HTTP, conexão
    ou corpo que não é JSON válido (ValueError) -> 502."""
    try:
        return await fetch_github_user(username)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="GitHub API timeout") from exc
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="GitHub API error or unreachable"
        ) from exc


@router.get("/users/{username}")
async def get_github_user(username: str, request: Request):
    cache_key = f"cache:github:user:{username}"
    lock_key = f"lock:github:user:{username}"

    start = time.perf_counter()
    cached = await redis_client.get(cache_key)

    if cached:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        request.state.cache_status = "HIT"
        return {
            "source": "cache",
            "latency_ms": elapsed_ms,
            "data": json.loads(cached),
        }

    # Cache miss: tenta adquirir o lock distribuído antes de bater na origem
    lock_token = uuid.uuid4().hex
    acquired = await redis_client.set(lock_key, lock_token, nx=True, ex=LOCK_TTL_SECONDS)

    if not acquired:
        # Outra requisição já está buscando na origem; aguarda e tenta o cache de novo
        for _ in range(LOCK_WAIT_MAX_ATTEMPTS):
            await asyncio.sleep(LOCK_WAIT_RETRY_DELAY)
            cached = await redis_client.get(cache_key)
            if cached:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
                request.state.cache_status = "HIT"
                return {
                    "source": "cache",
                    "latency_ms": elapsed_ms,
                    "data": json.loads(cached),
                    "waited_for_lock": True,
                }
        # Não conseguiu nem o lock nem o cache a tempo: busca na origem mesmo assim
        # (fallback, evita deixar o cliente sem resposta)

    try:
        user_data = await _fetch_from_origin(username)
        if user_data is None:
            request.state.cache_status = "MISS"
            raise HTTPException(status_code=404, detail="GitHub user not found")

        await redis_client.set(cache_key, json.dumps(user_data), ex=CACHE_TTL_SECONDS)

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        request.state.cache_status = "MISS"
        return {
            "source": "origin",
            "latency_ms": elapsed_ms,
            "data": user_data,
        }
    finally:
        if acquired:
            # Só libera o lock se foi essa requisição que o adquiriu (verificação e DEL atômicos)
            release_lock = redis_client.register_script(RELEASE_LOCK_LUA)
            await release_lock(keys=[lock_key], args=[lock_token])


@router.delete("/users/{username}/cache")
async def purge_github_user_cache(username: str):
    cache_key = f"cache:github:user:{username}"
    deleted = await redis_client.delete(cache_key)
    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No cached entry found for user '{username}'",
        )
    return {"message": f"Cache purged for user '{username}'", "purged": True}