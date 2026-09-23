from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import random
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.schemas.downloads import PdfFetchResult


class PdfDownloadError(RuntimeError):
    pass


class InvalidPdfError(PdfDownloadError):
    pass


class PdfPermissionError(PdfDownloadError):
    pass


class UnsafeDownloadUrlError(PdfDownloadError):
    pass


class RetryableDownloadError(PdfDownloadError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PdfHttpClient(Protocol):
    async def fetch_pdf(
        self, url: str, destination: Path, *, max_bytes: int
    ) -> PdfFetchResult: ...


class SafePdfHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        connect_timeout_seconds: float = 10,
        total_timeout_seconds: float = 120,
        max_redirects: int = 5,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.user_agent = user_agent
        self.connect_timeout_seconds = connect_timeout_seconds
        self.total_timeout_seconds = total_timeout_seconds
        self.max_redirects = max_redirects
        self.max_attempts = max_attempts
        self.transport = transport
        self.sleep = sleep

    async def fetch_pdf(
        self, url: str, destination: Path, *, max_bytes: int
    ) -> PdfFetchResult:
        validate_download_url(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self._fetch_once(url, destination, max_bytes=max_bytes)
            except (RetryableDownloadError, httpx.RequestError) as exc:
                destination.unlink(missing_ok=True)
                if attempt == self.max_attempts:
                    raise PdfDownloadError(type(exc).__name__) from exc
                retry_after = (
                    exc.retry_after if isinstance(exc, RetryableDownloadError) else None
                )
                delay = (
                    min(retry_after, 30.0)
                    if retry_after is not None and retry_after >= 0
                    else min(2 ** (attempt - 1) + random.random(), 10.0)
                )
                await self.sleep(delay)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        raise PdfDownloadError("下载重试状态异常")

    async def _fetch_once(
        self, url: str, destination: Path, *, max_bytes: int
    ) -> PdfFetchResult:
        timeout = httpx.Timeout(
            self.total_timeout_seconds,
            connect=self.connect_timeout_seconds,
        )
        current_url = url
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            for redirect_count in range(self.max_redirects + 1):
                validate_download_url(current_url)
                async with client.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": self.user_agent, "Accept": "application/pdf"},
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise PdfDownloadError("重定向响应缺少 Location")
                        if redirect_count >= self.max_redirects:
                            raise PdfDownloadError("重定向次数超过限制")
                        current_url = urljoin(str(response.url), location)
                        continue
                    if response.status_code in {401, 403, 451}:
                        raise PdfPermissionError(f"HTTP {response.status_code}")
                    if response.status_code == 429 or response.status_code >= 500:
                        raise RetryableDownloadError(
                            f"HTTP {response.status_code}",
                            _retry_after(response.headers.get("Retry-After")),
                        )
                    if response.status_code != 200:
                        raise PdfDownloadError(f"HTTP {response.status_code}")

                    content_type = response.headers.get("Content-Type", "").split(";")[0]
                    if content_type.casefold() not in {
                        "application/pdf",
                        "application/octet-stream",
                    }:
                        raise InvalidPdfError(
                            f"响应 Content-Type 不是 PDF: {content_type or 'missing'}"
                        )
                    content_length = _integer(response.headers.get("Content-Length"))
                    if content_length is not None and content_length > max_bytes:
                        raise InvalidPdfError("PDF 超过单文件大小限制")

                    digest = hashlib.sha256()
                    size = 0
                    header = bytearray()
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise InvalidPdfError("PDF 超过单文件大小限制")
                            if len(header) < 1024:
                                header.extend(chunk[: 1024 - len(header)])
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    if not bytes(header).lstrip().startswith(b"%PDF-"):
                        raise InvalidPdfError("文件头不是 %PDF-，可能是登录页或错误页")
                    if size == 0:
                        raise InvalidPdfError("PDF 文件为空")
                    return PdfFetchResult(
                        http_status=200,
                        final_url=redact_url(str(response.url)),
                        content_type=content_type,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                    )
        raise PdfDownloadError("下载重定向状态异常")


def validate_download_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise UnsafeDownloadUrlError("只允许带主机名的 HTTPS 下载地址")
    if parsed.username or parsed.password:
        raise UnsafeDownloadUrlError("下载地址不能包含用户凭据")
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise UnsafeDownloadUrlError("不允许访问本地主机")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise UnsafeDownloadUrlError("不允许访问非公网 IP 地址")


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))


def safe_pdf_filename(
    *,
    year: int | None,
    first_author: str | None,
    title: str,
    stable_id: str,
) -> str:
    parts = [
        str(year or "unknown"),
        _filename_part(first_author or "Unknown"),
        _filename_part(title, limit=80),
        _filename_part(stable_id, limit=48),
    ]
    return "_".join(part for part in parts if part)[:220].rstrip(" .") + ".pdf"


def _filename_part(value: str, *, limit: int = 40) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    return cleaned[:limit].rstrip(" ._") or "unknown"


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None

