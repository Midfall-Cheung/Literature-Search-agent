from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.routes import router
from app.api.terms import router as terms_router
from app.config import Settings
from app.graph.workflow import ClarificationGraph
from app.repositories.projects import ProjectRepository, create_engine_and_schema
from app.repositories.terms import TermRepository
from app.services.clarifier import QuestionAnalyzer
from app.services.projects import ClarificationProjectService
from app.services.term_builder import TermBuilder, TermExpander
from app.services.terms import TermTableService


def create_app(
    settings: Settings | None = None,
    analyzer: QuestionAnalyzer | None = None,
    term_expander: TermExpander | None = None,
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        checkpoint_connection.close()
        engine.dispose()

    app = FastAPI(
        title="Literature Search Agent - Phases A+B",
        version="0.2.0",
        description="研究问题澄清、可审计检索词表、查询预览与 CSV 导出。",
        lifespan=lifespan,
    )
    app.state.project_service = ClarificationProjectService(repository, graph)
    app.state.term_service = TermTableService(
        repository,
        term_repository,
        builder=TermBuilder(term_expander),
    )
    app.include_router(router)
    app.include_router(terms_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "phases": "A,B"}

    return app


app = create_app()
