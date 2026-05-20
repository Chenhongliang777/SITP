import os

from utils.project_root import get_project_root


def test_project_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CSL_SENTINEL_ROOT", str(tmp_path))
    # 模块有缓存，需清缓存
    import utils.project_root as pr

    pr._cached = None
    assert get_project_root() == tmp_path.resolve()
    pr._cached = None
    monkeypatch.delenv("CSL_SENTINEL_ROOT", raising=False)
