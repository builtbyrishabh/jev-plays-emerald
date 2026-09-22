import importlib.util
import json
from pathlib import Path
import pytest


def suite():
    spec = importlib.util.spec_from_file_location("suite", Path(__file__).parents[1] / "scripts/benchmark_suite.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_refuses_existing_directory_and_records_source(tmp_path):
    module = suite()
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    (source / "src/app.py").write_text("version = 1")
    (source / ".env").write_text("SECRET=private")
    before = module.source_fingerprint(source)
    (source / ".env").write_text("SECRET=changed")
    assert module.source_fingerprint(source) == before
    (source / "src/app.py").write_text("version = 2")
    assert module.source_fingerprint(source) != before
    output = tmp_path / "results"
    module.create_manifest(output, {"fingerprint": before})
    assert json.loads((output / "manifest.json").read_text())["fingerprint"] == before
    with pytest.raises(FileExistsError):
        module.create_manifest(output, {})


def test_schedule_has_five_hybrid_and_three_each_control():
    module = suite()
    schedule = module.schedule(5, 3)
    assert schedule.count("hybrid") == 5
    for variant in ("jev-only", "jev-grounded", "luna-only"):
        assert schedule.count(variant) == 3


def test_parallel_jobs_are_bounded_and_keep_planned_indices():
    from threading import Lock
    import time
    module = suite()
    lock = Lock()
    active = peak = 0

    def work(value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02 if value == 0 else 0.005)
        with lock:
            active -= 1
        return value * 2

    results = list(module.run_jobs(list(range(6)), work, 2))
    assert sorted(results) == [(index, index * 2) for index in range(6)]
    assert peak == 2
