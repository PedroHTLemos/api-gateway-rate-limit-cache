import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.redis_client import redis_client
from app.core.api_keys import resolve_identity, TIER_LIMITS

# Tudo em um único script, para que nenhuma requisição concorrente leia a contagem
# antes da inserção de outra. Argumentos:
#   KEYS[1] = chave do log; ARGV = now, window_start, max_requests, member, window_seconds.
# Retorna {aceita (1/0), contagem antes da inserção, score mais antigo ('' se vazio)}.
SLIDING_WINDOW_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[2])
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local oldest_score = ''
    if oldest[2] then
        oldest_score = oldest[2]
    end
    return {0, count, oldest_score}
end
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return {1, count, ''}
"""


class SlidingWindowLogRateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Sliding Window Log Rate Limiter, com limite variável por tier
    (resolvido via header X-API-Key). Sem chave válida, cai no tier
    'anonymous' (mais restritivo).
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/health", "/docs", "/openapi.json"):
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        identity = resolve_identity(api_key)
        tier = identity["tier"]
        limits = TIER_LIMITS[tier]
        max_requests = limits["max_requests"]
        window_seconds = limits["window_seconds"]

        # Anônimos ainda são distinguidos por IP (senão compartilhariam o mesmo balde)
        client_ip = request.client.host if request.client else "unknown"
        identifier = identity["id"] if tier != "anonymous" else f"anonymous:{client_ip}"

        key = f"ratelimit:sliding:{identifier}"
        now = time.time()
        window_start = now - window_seconds
        member = f"{now}:{uuid.uuid4().hex}"

        # Registrado a cada chamada: o script fica ligado ao redis_client atual do módulo.
        sliding_window = redis_client.register_script(SLIDING_WINDOW_LUA)
        accepted, current_count, oldest_score = await sliding_window(
            keys=[key],
            args=[now, window_start, max_requests, member, window_seconds],
        )

        if not accepted:
            if oldest_score:
                oldest_ts = float(oldest_score)
                retry_after = max(1, int(oldest_ts + window_seconds - now))
            else:
                retry_after = window_seconds
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests", "tier": tier},
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max(0, max_requests - current_count - 1))
        response.headers["X-RateLimit-Tier"] = tier
        return response