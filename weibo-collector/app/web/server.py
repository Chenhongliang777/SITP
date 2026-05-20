"""局域网 uvicorn 服务启停。"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.web.lan_auth import get_or_create_lan_token
from app.web.network import get_lan_ip

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

_server: Optional[object] = None
_thread: Optional[threading.Thread] = None
_host: str = DEFAULT_HOST
_port: int = DEFAULT_PORT


@dataclass
class LanServerInfo:
    running: bool
    host: str
    port: int
    lan_ip: str
    phone_url: str
    token: str


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


def create_app():
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    from app.web.routes import router

    application = FastAPI(title="CSL Sentinel 局域网服务", version="1.0")

    @application.get("/")
    async def index_page():
        index = _static_dir() / "index.html"
        if not index.exists():
            raise RuntimeError(f"缺少 H5 页面: {index}")
        return FileResponse(index, media_type="text/html; charset=utf-8")

    application.include_router(router, prefix="/api")
    static = _static_dir()
    if static.is_dir():
        application.mount("/assets", StaticFiles(directory=str(static)), name="assets")
    return application


def is_server_running() -> bool:
    return _thread is not None and _thread.is_alive()


def get_server_info() -> LanServerInfo:
    token = get_or_create_lan_token()
    ip = get_lan_ip()
    running = is_server_running()
    url = f"http://{ip}:{_port}/" if running else ""
    return LanServerInfo(
        running=running,
        host=_host,
        port=_port,
        lan_ip=ip,
        phone_url=url,
        token=token,
    )


def start_lan_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> LanServerInfo:
    global _server, _thread, _host, _port
    import uvicorn

    if is_server_running():
        return get_server_info()

    _host = host
    _port = port
    get_or_create_lan_token()
    app = create_app()
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    _server = uvicorn.Server(config)

    def _run() -> None:
        assert _server is not None
        _server.run()

    _thread = threading.Thread(target=_run, name="lan-web-server", daemon=True)
    _thread.start()
    return get_server_info()


def stop_lan_server() -> None:
    global _server, _thread
    if _server is not None:
        _server.should_exit = True
    if _thread is not None:
        _thread.join(timeout=8.0)
    _server = None
    _thread = None
