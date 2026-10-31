"""scripts/upgrade-agent.sh run for real on a scratch checkout (it must run as root,
like cron runs it on the host; skipped elsewhere)."""
import json
import os
import pathlib
import shutil
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT = ROOT / "scripts" / "upgrade-agent.sh"

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("flock"), reason="the helper runs as root with flock"
)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(AGENT, tmp_path / "scripts" / "upgrade-agent.sh")
    (tmp_path / "upgrade").mkdir()
    return tmp_path


def run(repo, **env):
    return subprocess.run(["bash", str(repo / "scripts" / "upgrade-agent.sh")], capture_output=True,
                          text=True, timeout=60, env={**os.environ, **env})


def agent(repo):
    return json.loads((repo / "upgrade" / "agent.json").read_text())


def test_every_run_writes_a_heartbeat(repo):
    proc = run(repo)
    assert proc.returncode == 0, proc.stderr
    a = agent(repo)
    assert a["busy"] is False and a["every_minutes"] == 5 and a["last_seen"].endswith("Z")


def test_a_run_that_finds_the_lock_held_still_reports_in_as_busy(repo):
    holder = subprocess.Popen(["flock", str(repo / "upgrade" / ".lock"), "sleep", "10"])
    try:
        time.sleep(0.5)
        proc = run(repo)
        assert proc.returncode == 0, proc.stderr
        assert agent(repo)["busy"] is True
        assert "Another instance is running" in proc.stdout
    finally:
        holder.kill()
        holder.wait()


def test_an_upgrade_that_hangs_is_stopped(repo):
    (repo / "update.sh").write_text("#!/usr/bin/env bash\nsleep 30\n")
    (repo / "upgrade" / "request.json").write_text(json.dumps({
        "id": "0123abcd-0123-4567-89ab-0123456789ab", "version": "v1.2.3", "requested_by": "test"}))
    start = time.monotonic()
    proc = run(repo, UPGRADE_TIMEOUT="1")
    assert proc.returncode == 0, proc.stderr
    assert time.monotonic() - start < 20
    status = json.loads((repo / "upgrade" / "status.json").read_text())
    assert status["state"] == "failed" and "124" in status["message"]
    assert "ran longer than 1s" in (repo / "upgrade" / "upgrade.log").read_text()
    assert agent(repo)["busy"] is False
