from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from langgraph.checkpoint.sqlite import SqliteSaver

from app.api.routes import router
from app.config import Settings
from app.graph.workflow import ClarificationGraph
from app.repositories.projects import ProjectRepository, create_engine_and_schema
from app.services.clarifier import QuestionAnalyzer
from app.services.projects import ClarificationProjectService


def create_app(
    settings: Settings | None = None, analyzer: QuestionAnalyzer | None = None
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        checkpoint_connection.close()
        engine.dispose()

    app = FastAPI(
        title="Literature Search Agent - Phase A",
        version="0.1.0",
        description="多轮澄清、结构化摘要、显式确认和审计持久化。",
        lifespan=lifespan,
    )
    app.state.project_service = ClarificationProjectService(repository, graph)
    app.include_router(router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "phase": "A"}

    return app


app = create_app()
