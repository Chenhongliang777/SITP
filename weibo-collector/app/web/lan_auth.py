"""局域网访问令牌（存于 data/lan_access.token）。"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Query

from app.paths import get_data_dir

_TOKEN_FILE = "lan_access.token"


def token_file_path() -> Path:
    return get_data_dir() / _TOKEN_FILE


def get_or_create_lan_token() -> str:
    path = token_file_path()
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    token = secrets.token_urlsafe(24)
    path.write_text(token, encoding="utf-8")
    return token


def verify_lan_token(
    x_access_token: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
) -> None:
    """Header 供 API；Query 供浏览器直接打开报告 HTML。"""
    expected = get_or_create_lan_token()
    raw = (x_access_token or access_token or "").strip()
    if not raw or raw != expected:
        raise HTTPException(status_code=401, detail="访问令牌无效，请在手机页填写 PC 上显示的令牌")
