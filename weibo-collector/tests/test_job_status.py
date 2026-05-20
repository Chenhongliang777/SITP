"""JobStatusStore 快照与日志缓冲。"""
from app.job_status import get_job_store


def test_job_store_reset_and_finish():
    store = get_job_store()
    store.reset("测试联赛")
    store.append_log("line1")
    store.set_step(1, "preprocess", "清洗", 0.25)
    snap = store.snapshot()
    assert snap.running
    assert snap.keyword == "测试联赛"
    assert snap.step_index == 1
    assert "line1" in snap.logs
    store.finish_success("/tmp/report.html")
    done = store.snapshot()
    assert not done.running
    assert done.report_path == "/tmp/report.html"
    assert done.progress == 1.0
