from __future__ import annotations

from app.progress import ProgressStore


def test_progress_is_monotonic_and_completes() -> None:
    store = ProgressStore()
    job = store.create("job-12345678")
    job.report(30, "下载中")
    job.report(20, "旧阶段")
    assert job.percent == 30
    assert job.stage == "旧阶段"

    job.report(80, "转码中")
    job.complete()
    snapshot = job.snapshot()
    assert snapshot["percent"] == 100
    assert snapshot["status"] == "completed"
    assert store.get("job-12345678") is job

    failed = store.create("job-87654321")
    failed.report(10, "解析中")
    failed.fail("NO_AUDIO", "没有音频")
    assert failed.snapshot()["status"] == "failed"
    assert "NO_AUDIO" in (failed.snapshot()["error"] or "")
