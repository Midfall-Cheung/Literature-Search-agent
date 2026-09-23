from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.downloads import router as downloads_router
from app.api.query_plans import router as query_plans_router
from app.api.retrieval import router as retrieval_router
from app.api.routes import router
from app.api.terms import router as terms_router
from app.config import Settings
from app.graph.workflow import ClarificationGraph
from app.providers.http import JsonHttpClient, RetryingHttpClient
from app.providers.retrievers import default_retrievers
from app.repositories.downloads import DownloadRepository
from app.repositories.projects import ProjectRepository, create_engine_and_schema
from app.repositories.query_plans import QueryPlanRepository
from app.repositories.retrieval import RetrievalRepository
from app.repositories.terms import TermRepository
from app.resolvers.fulltext import FullTextResolver, default_resolvers
from app.services.clarifier import QuestionAnalyzer
from app.services.downloads import DownloadService
from app.services.pdf_downloader import PdfHttpClient, SafePdfHttpClient
from app.services.projects import ClarificationProjectService
from app.services.query_plans import QueryPlanService
from app.services.retrieval import RetrievalService
from app.services.term_builder import TermBuilder, TermExpander
from app.services.terms import TermTableService


def create_app(
    settings: Settings | None = None,
    analyzer: QuestionAnalyzer | None = None,
    term_expander: TermExpander | None = None,
    retrieval_http_client: JsonHttpClient | None = None,
    download_resolvers: list[FullTextResolver] | None = None,
    download_http_client: PdfHttpClient | None = None,
) -> FastAPI:
    app_settings = settings or Settings.from_env()
    app_settings.ensure_directories()
    engine, session_factory = create_engine_and_schema(app_settings.database_url)
    checkpoint_connection = sqlite3.connect(
        app_settings.checkpoint_database_path, check_same_thread=False
    )
    checkpointer = SqliteSaver(checkpoint_connection)
    checkpointer.setup()
    graph = ClarificationGraph(checkpointer=checkpointer, analyzer=analyzer)
    repository = ProjectRepository(session_factory)
    term_repository = TermRepository(session_factory)
    query_plan_repository = QueryPlanRepository(session_factory)
    retrieval_repository = RetrievalRepository(session_factory)
    download_repository = DownloadRepository(session_factory)
    http_client = retrieval_http_client or RetryingHttpClient(
        user_agent=app_settings.http_user_agent,
        timeout_seconds=app_settings.http_timeout_seconds,
    )
    pdf_client = download_http_client or SafePdfHttpClient(
        user_agent=app_settings.http_user_agent,
        connect_timeout_seconds=app_settings.download_connect_timeout_seconds,
        total_timeout_seconds=app_settings.download_total_timeout_seconds,
        max_redirects=app_settings.download_max_redirects,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        checkpoint_connection.close()
        engine.dispose()

    app = FastAPI(
        title="Literature Search Agent - Phases A-E",
        version="0.5.0",
        description="研究问题澄清、检索词表、查询计划、首轮检索与合法全文下载。",
        lifespan=lifespan,
    )
    app.state.project_service = ClarificationProjectService(repository, graph)
    app.state.term_service = TermTableService(
        repository,
        term_repository,
        builder=TermBuilder(term_expander),
    )
    app.state.query_plan_service = QueryPlanService(
        repository,
        term_repository,
        query_plan_repository,
    )
    app.state.retrieval_service = RetrievalService(
        repository,
        term_repository,
        query_plan_repository,
        retrieval_repository,
        default_retrievers(
            http_client,
            openalex_api_key=app_settings.openalex_api_key,
            semantic_scholar_api_key=app_settings.semantic_scholar_api_key,
            crossref_mailto=app_settings.crossref_mailto,
        ),
    )
    app.state.download_service = DownloadService(
        repository,
        retrieval_repository,
        download_repository,
        (
            download_resolvers
            if download_resolvers is not None
            else default_resolvers(
                http_client,
                unpaywall_email=app_settings.unpaywall_email,
                core_api_key=app_settings.core_api_key,
            )
        ),
        pdf_client,
        projects_root=app_settings.projects_root,
        max_file_bytes=app_settings.download_max_file_bytes,
        concurrency=app_settings.download_concurrency,
    )
    app.include_router(router)
    app.include_router(terms_router)
    app.include_router(query_plans_router)
    app.include_router(retrieval_router)
    app.include_router(downloads_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "phases": "A,B,C,D,E"}

    return app


app = create_app()
