from __future__ import annotations

import os
import re
from email.message import Message
from typing import Any
from urllib.parse import quote

import httpx


class APIError(Exception):
    """A readable API failure without request contents or credentials."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def readable_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict) and isinstance(item.get("msg"), str):
                location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                messages.append(f"{location}：{item['msg']}" if location else item["msg"])
        return "；".join(messages)
    return ""


class APIClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or os.getenv(
            "LITERATURE_API_BASE_URL", "http://127.0.0.1:8000"
        )).rstrip("/") + "/"
        self.timeout = timeout
        self.transport = transport

    def _http_request(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        try:
            with httpx.Client(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport
            ) as client:
                response = client.request(method, path, json=payload)
        except httpx.InvalidURL as exc:
            raise APIError("后端地址格式无效，请检查 LITERATURE_API_BASE_URL。") from exc
        except httpx.TimeoutException as exc:
            raise APIError("请求超时。请刷新项目检查是否已保存，再决定是否重试。") from exc
        except httpx.RequestError as exc:
            raise APIError(
                "无法连接后端。请启动 .venv/bin/literature-clarifier --reload，"
                "并检查 LITERATURE_API_BASE_URL。"
            ) from exc
        if not response.is_success:
            try:
                data = response.json()
            except ValueError:
                data = None
            fallback = {
                400: "请求内容无效，请检查输入。",
                404: "项目不存在，请核对项目 ID。",
                409: "当前状态或输入不允许此操作，请刷新项目并检查回答。",
                422: "输入格式不正确，请检查字段类型与必填内容。",
            }.get(response.status_code, "后端服务暂时不可用，请稍后刷新项目。")
            detail = readable_detail(data.get("detail")) if isinstance(data, dict) else ""
            raise APIError(detail or fallback, response.status_code)
        return response

    def _request(self, method: str, path: str, payload: dict | None = None, *, response_kind: str = "project") -> dict:
        response = self._http_request(method, path, payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise APIError("后端响应格式异常，请刷新项目；已保存的页面状态将保留。") from exc
        if not isinstance(data, dict):
            raise APIError("后端响应格式异常，请刷新项目；已保存的页面状态将保留。")
        if response_kind == "project":
            if (not isinstance(data.get("project_id"), str)
                or data.get("status") not in {"clarifying", "awaiting_confirmation", "confirmed"}
                or not isinstance(data.get("question_spec"), dict)):
                raise APIError("后端项目响应不完整，请刷新项目；已保存的页面状态将保留。")
        elif response_kind == "health":
            if not isinstance(data.get("status"), str):
                raise APIError("后端健康检查响应不完整。")
        else:
            from app.schemas.terms import TermTable, TermHistory, QueryPreview
            schemas = {"terms": TermTable, "history": TermHistory, "preview": QueryPreview}
            try:
                data = schemas[response_kind].model_validate(data).model_dump(mode="json")
            except ValueError as exc:
                raise APIError("后端词表响应格式异常；本地草稿将保留。") from exc
        return data

    @staticmethod
    def _project_path(project_id: str) -> str:
        return f"projects/{quote(project_id.strip(), safe='')}"

    def health(self) -> dict:
        return self._request("GET", "health", response_kind="health")

    def create_project(self, question: str) -> dict:
        return self._request("POST", "projects", {"original_question": question})

    def get_state(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/state")

    def answer(self, project_id: str, content: str, field_updates: dict | None = None) -> dict:
        payload: dict[str, Any] = {"content": content}
        if field_updates is not None:
            payload["field_updates"] = field_updates
        return self._request("POST", self._project_path(project_id) + "/messages", payload)

    def confirm(self, project_id: str) -> dict:
        return self._request("POST", self._project_path(project_id) + "/confirm-question", {"accepted": True})

    def revise(self, project_id: str, field_updates: dict, feedback: str | None = None) -> dict:
        payload: dict[str, Any] = {"accepted": False, "field_updates": field_updates}
        if feedback is not None:
            payload["feedback"] = feedback
        return self._request("POST", self._project_path(project_id) + "/confirm-question", payload)

    def get_terms(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/terms", response_kind="terms")

    def generate_terms(self, project_id: str, replace_existing: bool = False) -> dict:
        return self._request("POST", self._project_path(project_id) + "/terms/generate", {"replace_existing": replace_existing}, response_kind="terms")

    def replace_terms(self, project_id: str, expected_version: int, terms: list[dict]) -> dict:
        return self._request("PUT", self._project_path(project_id) + "/terms", {"expected_version": expected_version, "terms": terms}, response_kind="terms")

    def get_query_preview(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/query-preview", response_kind="preview")

    def get_term_history(self, project_id: str) -> dict:
        return self._request("GET", self._project_path(project_id) + "/terms/history", response_kind="history")

    def confirm_terms(self, project_id: str, expected_version: int) -> dict:
        return self._request("POST", self._project_path(project_id) + "/confirm-terms", {"expected_version": expected_version}, response_kind="terms")

    def export_terms_csv(self, project_id: str) -> tuple[str, bytes]:
        response = self._http_request("GET", self._project_path(project_id) + "/terms/export.csv")
        header = Message()
        header["Content-Disposition"] = response.headers.get("Content-Disposition", "")
        filename = (header.get_filename() or "terms.csv").replace("\\", "/").split("/")[-1]
        if not re.fullmatch(r"[\w.-]+\.csv", filename) or filename.startswith("."):
            filename = "terms.csv"
        return filename, response.content
