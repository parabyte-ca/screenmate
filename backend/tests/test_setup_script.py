"""setup.sh, run for real against fake docker/curl/id so nothing touches a daemon."""
import os
import pathlib
import shutil
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

SHIMS = {
    "docker": r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
[[ -n "${FAKE_NO_DAEMON:-}" && "$1" == "info" ]] && exit 1
[[ "$1" == "info" && -n "${FAKE_NVIDIA:-}" ]] && echo "Runtimes: io.containerd.runc.v2 nvidia runc"
exit 0
''',
    "curl": r'''#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
[[ -n "${FAKE_UNHEALTHY:-}" ]] && exit 7
exit 0
''',
    "id": '#!/usr/bin/env bash\necho "${FAKE_UID:-1000}"\n',
    "sleep": "#!/usr/bin/env bash\nexit 0\n",
    "hostname": '#!/usr/bin/env bash\necho "192.168.1.50"\n',
    "timedatectl": '#!/usr/bin/env bash\necho "Europe/London"\n',
    "nvidia-smi": "#!/usr/bin/env bash\nexit 0\n",
}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "screenmate"
    (r / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "setup.sh", r / "setup.sh")
    shutil.copy(ROOT / ".env.example", r / ".env.example")
    (r / "scripts/upgrade-agent.sh").write_text('#!/usr/bin/env bash\necho "agent $*" >> "$FAKE_LOG"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in SHIMS.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return r, bin_dir, tmp_path / "calls.log"


def run(repo, *args, nvidia=False, **env):
    r, bin_dir, log = repo
    shims = dict(os.environ)
    if not nvidia:
        (bin_dir / "nvidia-smi").unlink(missing_ok=True)
    shims.update(PATH=f"{bin_dir}:/usr/bin:/bin", FAKE_LOG=str(log),
                 **({"FAKE_NVIDIA": "1"} if nvidia else {}), **env)
    proc = subprocess.run(["bash", str(r / "setup.sh"), *args], cwd=r, env=shims,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def env_of(r):
    return dict(line.split("=", 1) for line in (r / ".env").read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def test_unattended_install(repo, tmp_path):
    r, _, _ = repo
    media = tmp_path / "tv"
    media.mkdir()
    proc, calls = run(repo, "-y", "--port", "5000", "--media", f"{media}/", "--media", "/mnt/movies",
                      "--tz", "America/Toronto", "--no-agent")
    assert proc.returncode == 0, proc.stderr
    env = env_of(r)
    assert (env["SCREENMATE_PORT"], env["TZ"], env["ENCODER_BACKEND"]) == ("5000", "America/Toronto", "cpu")
    assert env["MEDIA_PATHS"] == f"{media},/mnt/movies"
    assert env["DATABASE_URL"].endswith("screenmate.db")  # .env.example was the starting point
    override = (r / "docker-compose.override.yml").read_text()
    assert f'"{media}:{media}"' in override and '"/mnt/movies:/mnt/movies"' in override
    assert "build:" not in override and "nvidia" not in override
    assert calls.index("docker compose pull") < calls.index("docker compose up -d --remove-orphans")
    assert any("localhost:5000/health" in c for c in calls)
    assert "Warning: /mnt/movies doesn't exist" in proc.stdout
    assert "http://192.168.1.50:5000" in proc.stdout
    assert (r / "upgrade").is_dir() and (r / "data").is_dir()


def test_rerun_keeps_previous_answers_and_does_not_duplicate(repo):
    r, _, _ = repo
    run(repo, "-y", "--port", "5000", "--no-agent")
    (r / ".env").write_text((r / ".env").read_text() + "MY_OWN=1\n")
    proc, _ = run(repo, "-y", "--no-agent")
    assert proc.returncode == 0, proc.stderr
    env = env_of(r)
    assert env["SCREENMATE_PORT"] == "5000" and env["MY_OWN"] == "1"
    assert env["TZ"] == "Europe/London"  # from the host
    assert (r / ".env").read_text().count("SCREENMATE_PORT=") == 1
    assert "services:" in (r / "docker-compose.override.yml").read_text()


def test_gpu_build_and_agent(repo):
    r, _, _ = repo
    proc, calls = run(repo, "-y", "--build", "--agent", nvidia=True, FAKE_UID="0")
    assert proc.returncode == 0, proc.stderr
    override = (r / "docker-compose.override.yml").read_text()
    assert "build: ." in override and "driver: nvidia" in override
    assert env_of(r)["ENCODER_BACKEND"] == "nvenc"  # detected and accepted by default
    assert "docker compose build" in calls and "docker compose pull" not in calls
    assert "agent --install" in calls


def test_agent_needs_root(repo):
    proc, calls = run(repo, "-y", "--agent", FAKE_UID="1000")
    assert proc.returncode == 0
    assert "sudo" in proc.stdout and not any(c.startswith("agent") for c in calls)


def test_keeps_a_hand_written_override(repo):
    r, _, _ = repo
    (r / "docker-compose.override.yml").write_text("services:\n  screenmate:\n    mem_limit: 2g\n")
    run(repo, "-y", "--no-agent")
    assert "mem_limit" in (r / "docker-compose.override.yml.bak").read_text()


@pytest.mark.parametrize("args,env,message", [
    (["--port", "99999"], {}, "Port must be"),
    (["--media", "relative/path"], {}, "absolute"),
    (["--bogus"], {}, "Unknown option"),
    ([], {"FAKE_NO_DAEMON": "1"}, "Docker daemon"),
    ([], {"FAKE_UNHEALTHY": "1"}, "didn't answer"),
])
def test_errors(repo, args, env, message):
    proc, _ = run(repo, "-y", "--no-agent", *args, **env)
    assert proc.returncode != 0 and message in proc.stderr
