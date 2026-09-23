from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(slots=True)
class JsonResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


class JsonHttpClient(Protocol):
    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> JsonResponse: ...


class RetryingHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> JsonResponse:
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await client.get(
                        url, params=params, headers=request_headers
                    )
                except httpx.RequestError:
                    if attempt == self.max_attempts:
                        raise
                    await asyncio.sleep(
                        min(2 ** (attempt - 1) + random.random(), 10.0)
                    )
                    continue
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("数据源返回的 JSON 顶层不是对象")
                    return JsonResponse(
                        payload=payload,
                        headers=dict(response.headers),
                    )
                if attempt == self.max_attempts:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 30.0) if retry_after else 0.0
                except ValueError:
                    delay = 0.0
                if delay <= 0:
                    delay = min(2 ** (attempt - 1) + random.random(), 10.0)
                await asyncio.sleep(delay)
        raise RuntimeError("HTTP 重试状态异常")
