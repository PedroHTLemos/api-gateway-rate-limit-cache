"""Dublês usados pela suíte (sem rede)."""

import asyncio

from redis.commands.core import AsyncScript

GITHUB_USERS = {
    "octocat": {"login": "octocat", "id": 1, "public_repos": 8},
    "torvalds": {"login": "torvalds", "id": 2, "public_repos": 6},
}


class FakeGitHub:
    """
    Substitui fetch_github_user: conta chamadas, pode simular latência
    e pode lançar uma exceção (por exemplo, do httpx) em vez de responder.
    """

    def __init__(self, users: dict, delay: float = 0.0, exc: Exception | None = None):
        self.users = users
        self.delay = delay
        self.exc = exc
        self.calls: list[str] = []

    async def __call__(self, username: str):
        self.calls.append(username)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.users.get(username)


# Comandos que, num Redis real, seriam uma ida e volta pela rede.
NETWORK_COMMANDS = {
    "zremrangebyscore",
    "zcard",
    "zadd",
    "zrange",
    "expire",
    "evalsha",
    "eval",
    "script_load",
}
SIMULATED_RTT_SECONDS = 0.005


class LatencyRedis:
    """
    Envolve um FakeRedis e adiciona latência de rede aos comandos.

    O fakeredis responde sem ceder o event loop, então requisições concorrentes
    não se intercalam. Esta espera simula a ida e volta de um Redis real.
    """

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

    def register_script(self, script):
        # Os scripts precisam chamar evalsha/script_load neste wrapper (e não no
        # FakeRedis interno), para que a latência também se aplique a eles.
        return AsyncScript(self, script)
