import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.repositories.retrieval import WorkRow, normalize_title
from app.schemas.downloads import (
    FullTextSource,
    PdfFetchResult,
    ResolvedLocation,
    VersionType,
)
from app.services.pdf_downloader import (
    InvalidPdfError,
    PdfDownloadError,
    SafePdfHttpClient,
    UnsafeDownloadUrlError,
    safe_pdf_filename,
    validate_download_url,
)


PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StaticResolver:
    def __init__(
        self,
        source: FullTextSource,
        location: ResolvedLocation | None,
        error: Exception | None = None,
    ):
        self.source = source
        self.location = location
        self.error = error
        self.calls = 0

    async def resolve(self, work):
        del work
        self.calls += 1
        if self.error:
            raise self.error
        return self.location


class FakePdfClient:
    def __init__(self, responses: dict[str, bytes | Exception]):
        self.responses = responses
        self.calls: list[str] = []

    async def fetch_pdf(
        self, url: str, destination: Path, *, max_bytes: int
    ) -> PdfFetchResult:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        if len(response) > max_bytes:
            raise InvalidPdfError("too large")
        destination.write_bytes(response)
        return PdfFetchResult(
            http_status=200,
            final_url=url,
            content_type="application/pdf",
            size_bytes=len(response),
            sha256=hashlib.sha256(response).hexdigest(),
        )


def make_app(
    tmp_path: Path,
    resolvers: list[StaticResolver],
    pdf_client: FakePdfClient,
):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'business.db'}",
        checkpoint_database_path=tmp_path / "checkpoints.db",
        projects_root=tmp_path / "projects",
        download_concurrency=2,
    )
    return create_app(
        settings,
        download_resolvers=resolvers,
        download_http_client=pdf_client,
    )


async def create_project_and_works(
    app, client: AsyncClient, works: list[dict[str, Any]]
) -> tuple[str, list[str]]:
    created = await client.post(
        "/projects", json={"original_question": "测试开放全文下载"}
    )
    assert created.status_code == 201
    project_id = created.json()["project_id"]
    work_ids: list[str] = []
    session_factory = app.state.download_service.repository.session_factory
    with session_factory.begin() as session:
        for index, data in enumerate(works):
            work_id = str(uuid4())
            work_ids.append(work_id)
            title = data.get("title", f"Open paper {index}")
            session.add(
                WorkRow(
                    id=work_id,
                    project_id=project_id,
                    title=title,
                    normalized_title=normalize_title(title),
                    authors=data.get("authors", ["Ada Lovelace"]),
                    year=data.get("year", 2025),
                    doi=data.get("doi", f"10.1234/test{index}"),
                    pmcid=data.get("pmcid"),
                    arxiv_id=data.get("arxiv_id"),
                    is_oa=data.get("is_oa", True),
                    license=data.get("license", "cc-by"),
                    landing_url=data.get(
                        "landing_url", "https://publisher.example/article?token=secret"
                    ),
                    pdf_url=data.get("pdf_url"),
                    relevance_score=data.get("relevance_score", 1 - index / 10),
                    relevance_reason="test",
                    is_retracted=False,
                    user_decision="unreviewed",
                    notes="",
                    tags=[],
                )
            )
    return project_id, work_ids


@pytest.mark.anyio
async def test_selected_download_is_atomic_auditable_and_idempotent(
    tmp_path: Path,
) -> None:
    url = "https://oa.example/paper.pdf?token=secret"
    resolver = StaticResolver(
        FullTextSource.UNPAYWALL,
        ResolvedLocation(
            source="unpaywall",
            url=url,
            license="cc-by",
            version_type=VersionType.PUBLISHED,
            landing_url="https://oa.example/article?token=secret",
        ),
    )
    pdf_client = FakePdfClient({url: PDF_BYTES})
    app = make_app(tmp_path, [resolver], pdf_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, work_ids = await create_project_and_works(
            app, client, [{"title": 'A/B: "safe" PDF'}]
        )
        request = {"mode": "selected", "work_ids": work_ids}
        response = await client.post(f"/projects/{project_id}/downloads", json=request)
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "completed"
        assert run["downloaded_count"] == 1
        assert run["items"][0]["status"] == "downloaded"
        file_info = run["items"][0]["file"]
        local_path = Path(file_info["local_path"])
        assert local_path.is_file()
        assert local_path.read_bytes() == PDF_BYTES
        assert local_path.parent.name == "papers"
        assert not list(local_path.parent.glob("*.part"))
        assert "?" not in file_info["source_url"]
        assert "?" not in file_info["final_url"]
        assert Path(run["manifest_path"]).is_file()
        assert Path(run["failed_report_path"]).is_file()

        manifest = await client.get(
            f"/projects/{project_id}/downloads/{run['run_id']}/manifest.jsonl"
        )
        assert manifest.status_code == 200
        line = json.loads(manifest.text.strip())
        assert line["file"]["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
        assert "secret" not in manifest.text

        repeated = await client.post(
            f"/projects/{project_id}/downloads", json=request
        )
        assert repeated.status_code == 201
        assert repeated.json()["already_exists_count"] == 1
        assert pdf_client.calls == [url]


@pytest.mark.anyio
async def test_invalid_candidate_falls_back_to_next_resolver(tmp_path: Path) -> None:
    bad_url = "https://bad.example/not-a-pdf"
    good_url = "https://arxiv.org/pdf/2601.12345"
    resolvers = [
        StaticResolver(
            FullTextSource.METADATA,
            ResolvedLocation(source="metadata", url=bad_url),
        ),
        StaticResolver(
            FullTextSource.ARXIV,
            ResolvedLocation(
                source="arxiv", url=good_url, version_type="preprint"
            ),
        ),
    ]
    pdf_client = FakePdfClient(
        {bad_url: InvalidPdfError("HTML login page"), good_url: PDF_BYTES}
    )
    app = make_app(tmp_path, resolvers, pdf_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, work_ids = await create_project_and_works(app, client, [{}])
        response = await client.post(
            f"/projects/{project_id}/downloads",
            json={"mode": "selected", "work_ids": work_ids},
        )
        run = response.json()
        assert run["status"] == "completed"
        assert [item["status"] for item in run["attempts"]] == [
            "invalid_pdf",
            "downloaded",
        ]
        assert run["items"][0]["file"]["source"] == "arxiv"


@pytest.mark.anyio
async def test_all_oa_requires_estimate_confirmation_and_deduplicates_hashes(
    tmp_path: Path,
) -> None:
    url = "https://oa.example/shared.pdf"
    resolver = StaticResolver(
        FullTextSource.METADATA,
        ResolvedLocation(source="metadata", url=url, license="cc-by"),
    )
    pdf_client = FakePdfClient({url: PDF_BYTES})
    app = make_app(tmp_path, [resolver], pdf_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, _ = await create_project_and_works(app, client, [{}, {}])
        estimate = await client.post(
            f"/projects/{project_id}/downloads/estimate", json={"mode": "all_oa"}
        )
        assert estimate.status_code == 200
        assert estimate.json()["eligible_count"] == 2
        assert estimate.json()["requires_confirmation"] is True

        rejected = await client.post(
            f"/projects/{project_id}/downloads", json={"mode": "all_oa"}
        )
        assert rejected.status_code == 409

        accepted = await client.post(
            f"/projects/{project_id}/downloads",
            json={"mode": "all_oa", "confirm_all_oa": True},
        )
        assert accepted.status_code == 201, accepted.text
        run = accepted.json()
        assert run["downloaded_count"] == 1
        assert run["already_exists_count"] == 1
        assert len(list(Path(run["papers_directory"]).glob("*.pdf"))) == 1


@pytest.mark.anyio
async def test_failed_run_can_resume(tmp_path: Path) -> None:
    url = "https://oa.example/retry.pdf"
    resolver = StaticResolver(
        FullTextSource.UNPAYWALL,
        ResolvedLocation(source="unpaywall", url=url),
    )
    pdf_client = FakePdfClient({url: PdfDownloadError("temporary")})
    app = make_app(tmp_path, [resolver], pdf_client)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, work_ids = await create_project_and_works(app, client, [{}])
        failed = await client.post(
            f"/projects/{project_id}/downloads",
            json={"mode": "selected", "work_ids": work_ids},
        )
        assert failed.status_code == 201
        assert failed.json()["status"] == "failed"
        run_id = failed.json()["run_id"]

        pdf_client.responses[url] = PDF_BYTES
        resumed = await client.post(
            f"/projects/{project_id}/downloads/{run_id}/resume"
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        assert resumed.json()["downloaded_count"] == 1


@pytest.mark.anyio
async def test_no_legal_location_is_reported_for_manual_access(tmp_path: Path) -> None:
    resolver = StaticResolver(FullTextSource.UNPAYWALL, None)
    app = make_app(tmp_path, [resolver], FakePdfClient({}))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, work_ids = await create_project_and_works(
            app, client, [{"is_oa": False}]
        )
        response = await client.post(
            f"/projects/{project_id}/downloads",
            json={"mode": "selected", "work_ids": work_ids},
        )
        run = response.json()
        assert run["items"][0]["status"] == "manual_access_required"
        report = await client.get(
            f"/projects/{project_id}/downloads/{run['run_id']}/failed.csv"
        )
        assert report.status_code == 200
        assert b"manual_access_required" in report.content


@pytest.mark.anyio
async def test_resolver_errors_redact_urls_emails_and_tokens(tmp_path: Path) -> None:
    resolver = StaticResolver(
        FullTextSource.UNPAYWALL,
        None,
        RuntimeError(
            "failed https://api.example/item?email=secret@example.com&token=abc"
        ),
    )
    app = make_app(tmp_path, [resolver], FakePdfClient({}))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        project_id, work_ids = await create_project_and_works(app, client, [{}])
        response = await client.post(
            f"/projects/{project_id}/downloads",
            json={"mode": "selected", "work_ids": work_ids},
        )
        payload = response.text
        assert "secret@example.com" not in payload
        assert "token=abc" not in payload
        assert "https://api.example/item" in payload


@pytest.mark.anyio
async def test_http_downloader_retries_429_and_validates_pdf(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=PDF_BYTES,
            request=request,
        )

    delays: list[float] = []

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    client = SafePdfHttpClient(
        user_agent="test-agent",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )
    destination = tmp_path / "paper.part"
    result = await client.fetch_pdf(
        "https://oa.example/paper.pdf", destination, max_bytes=1024
    )
    assert calls == 2
    assert delays == [0.0]
    assert result.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert destination.read_bytes() == PDF_BYTES


@pytest.mark.anyio
async def test_http_downloader_rejects_html_disguised_as_pdf(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=b"<html>login</html>",
            request=request,
        )

    client = SafePdfHttpClient(
        user_agent="test-agent", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InvalidPdfError, match="Content-Type"):
        await client.fetch_pdf(
            "https://oa.example/paper.pdf", tmp_path / "bad.part", max_bytes=1024
        )


@pytest.mark.anyio
async def test_http_downloader_rejects_invalid_pdf_header(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"this is not a pdf",
            request=request,
        )

    client = SafePdfHttpClient(
        user_agent="test-agent", transport=httpx.MockTransport(handler)
    )
    destination = tmp_path / "bad.part"
    with pytest.raises(InvalidPdfError, match="文件头"):
        await client.fetch_pdf(
            "https://oa.example/paper.pdf", destination, max_bytes=1024
        )
    assert not destination.exists()


def test_download_url_and_filename_safety() -> None:
    with pytest.raises(UnsafeDownloadUrlError):
        validate_download_url("http://example.org/paper.pdf")
    with pytest.raises(UnsafeDownloadUrlError):
        validate_download_url("https://127.0.0.1/paper.pdf")
    filename = safe_pdf_filename(
        year=2025,
        first_author="Wang/Smith",
        title='A:B <unsafe> "title"',
        stable_id="10.1234/abc",
    )
    assert filename.endswith(".pdf")
    assert not any(character in filename for character in '<>:"/\\|?*')
