"""FastAPI 路由。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import bootstrap, require_api_key
from app.job_status import get_job_store
from app.llm_settings import PRESETS, load_settings, save_settings
from app.pipeline_core import PipelineContext
from app.pipeline_runner import get_pipeline_runner
from app.reports_util import find_latest_report
from app.weibo_login import is_login_file_present
from app.web.lan_auth import verify_lan_token

router = APIRouter()

STEP_COUNT = 4


class RunRequest(BaseModel):
    keyword: str = Field(..., min_length=1)
    start_date: str
    end_date: str
    target_count: int = Field(80, ge=1, le=500)
    efficient: bool = False


class SettingsUpdate(BaseModel):
    preset_id: str = "deepseek"
    api_key: str = ""
    base_url: Optional[str] = None
    model: Optional[str] = None


@router.get("/status")
def api_status(_: None = Depends(verify_lan_token)) -> Dict[str, Any]:
    snap = get_job_store().snapshot()
    runner = get_pipeline_runner()
    return {
        "pipeline_busy": runner.is_running(),
        "running": snap.running,
        "keyword": snap.keyword,
        "step_index": snap.step_index,
        "step_name": snap.step_name,
        "step_label": snap.step_label,
        "progress": round(snap.progress, 4),
        "logs": snap.logs[-120:],
        "error": snap.error,
        "report_path": snap.report_path,
        "login_ok": is_login_file_present(),
    }


@router.get("/login/status")
def api_login_status(_: None = Depends(verify_lan_token)) -> Dict[str, Any]:
    return {
        "logged_in": is_login_file_present(),
        "message": (
            "微博登录态有效，可以发起监测。"
            if is_login_file_present()
            else "请在本机 GUI「微博登录」页扫码登录后再监测。"
        ),
    }


@router.post("/login/start")
def api_login_start(_: None = Depends(verify_lan_token)) -> Dict[str, str]:
    return {
        "message": "微博登录请在 Windows 电脑上的 GUI「微博登录」页操作，手机端仅查看状态。"
    }


@router.post("/login/confirm")
def api_login_confirm(_: None = Depends(verify_lan_token)) -> Dict[str, str]:
    return {
        "message": "请在本机 GUI 点击「确认已登录并保存」。"
    }


@router.get("/settings")
def api_get_settings(_: None = Depends(verify_lan_token)) -> Dict[str, Any]:
    s = load_settings()
    key = s.api_key
    masked = ""
    if len(key) > 8:
        masked = key[:4] + "****" + key[-4:]
    elif key:
        masked = "****"
    return {
        "preset_id": s.preset_id,
        "presets": [
            {"id": p.id, "label": p.label, "base_url": p.base_url, "model": p.model}
            for p in PRESETS.values()
        ],
        "api_key_masked": masked,
        "has_api_key": bool(key),
        "base_url": s.base_url,
        "model": s.model,
    }


@router.put("/settings")
def api_put_settings(
    body: SettingsUpdate, _: None = Depends(verify_lan_token)
) -> Dict[str, str]:
    try:
        save_settings(
            body.preset_id,
            body.api_key.strip(),
            base_url=body.base_url,
            model=body.model,
        )
        bootstrap()
        return {"message": "设置已保存"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/run")
def api_run(body: RunRequest, _: None = Depends(verify_lan_token)) -> Dict[str, str]:
    runner = get_pipeline_runner()
    if runner.is_running():
        raise HTTPException(status_code=409, detail="已有任务在运行（本机 GUI 或手机）")

    try:
        require_api_key()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    ctx = PipelineContext.from_gui(
        body.keyword.strip(),
        body.start_date.strip(),
        body.end_date.strip(),
        body.target_count,
        body.efficient,
    )
    store = get_job_store()
    store.reset(ctx.keyword)

    def on_log(line: str) -> None:
        store.append_log(line)

    def on_step_start(idx: int, name: str, label: str) -> None:
        store.set_step(idx, name, label, idx / STEP_COUNT)

    def on_step_done(idx: int, name: str, label: str) -> None:
        store.set_step(idx, name, label, (idx + 1) / STEP_COUNT)

    def on_complete(report_path: Path) -> None:
        store.finish_success(str(report_path))

    def on_error(err: Exception) -> None:
        store.finish_error(str(err))

    started = runner.run_async(
        ctx,
        on_log=on_log,
        on_step_start=on_step_start,
        on_step_done=on_step_done,
        on_complete=on_complete,
        on_error=on_error,
        auto_open_report=False,
    )
    if not started:
        raise HTTPException(status_code=409, detail="无法启动任务")
    return {"message": "任务已启动", "keyword": ctx.keyword}


@router.get("/report/latest")
def api_report_latest(
    keyword: str, _: None = Depends(verify_lan_token)
) -> Dict[str, str]:
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="缺少 keyword")
    try:
        path = find_latest_report(keyword.strip())
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "keyword": keyword.strip(),
        "filename": path.name,
        "open_url": f"/api/report/open?keyword={keyword.strip()}",
    }


@router.get("/report/open")
def api_report_open(
    keyword: str, _: None = Depends(verify_lan_token)
) -> FileResponse:
    try:
        path = find_latest_report(keyword.strip())
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if path.suffix.lower() != ".html":
        raise HTTPException(status_code=400, detail="仅支持 HTML 报告")
    return FileResponse(path, media_type="text/html; charset=utf-8", filename=path.name)
