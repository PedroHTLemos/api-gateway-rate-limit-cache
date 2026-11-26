"""Dublês usados pela suíte (sem rede)."""

import asyncio

GITHUB_USERS = {
    "octocat": {"login": "octocat", "id": 1, "public_repos": 8},
    "torvalds": {"login": "torvalds", "id": 2, "public_repos": 6},
}


class FakeGitHub:
    """Substitui fetch_github_user: conta chamadas e pode simular latência."""

    def __init__(self, users: dict, delay: float = 0.0):
        self.users = users
        self.delay = delay
        self.calls: list[str] = []

    async def __call__(self, username: str):
        self.calls.append(username)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.users.get(username)
