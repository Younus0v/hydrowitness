"""The one-click launcher: starts, serves only this computer, signs the page in, keeps its token private."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import run_local  # noqa: E402


def test_token_is_made_once_kept_and_private(tmp_path):
    f = tmp_path / "tok"
    a = run_local.read_or_make_token(f)
    assert len(a) >= 20 and all(c.isalnum() or c in "-_" for c in a)
    assert run_local.read_or_make_token(f) == a
    if os.name == "posix":
        assert (f.stat().st_mode & 0o077) == 0
    f.write_text("short")                                  # a damaged file is replaced, not trusted
    assert run_local.read_or_make_token(f) != "short"


def test_free_port_is_actually_free():
    import socket
    p = run_local.free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", p))


def test_starts_on_localhost_requires_the_token_and_the_page_picks_it_up(tmp_path):
    tokf = tmp_path / "tok"
    env = {**os.environ, "HYDROWITNESS_NO_BROWSER": "1"}
    port = run_local.free_port(18000)
    proc = subprocess.Popen([sys.executable, str(ROOT / "run_local.py"), "--port", str(port), "--token-file", str(tokf)],
                            cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                health = json.load(urllib.request.urlopen(base + "/api/health", timeout=1))
                break
            except OSError:
                if proc.poll() is not None:
                    pytest.fail("launcher exited early: " + proc.stdout.read().decode(errors="replace"))
                time.sleep(0.2)
        else:
            pytest.fail("launcher did not start")
        assert health["auth_required"] is True and health["demo_config"] is True and health["read_only"] is True
        tok = tokf.read_text().strip()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/state", timeout=2)
        assert e.value.code == 401
        req = urllib.request.Request(base + "/api/state", headers={"Authorization": "Bearer " + tok})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        assert tok not in page                              # the token is never served in the page itself
        assert "#token=" in page                            # but the page knows how to take it from the fragment
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_start_scripts_exist_and_are_wired_to_the_launcher():
    mac = (ROOT / "START_HERE_Mac_Linux.command").read_text()
    win = (ROOT / "START_HERE_Windows.bat").read_bytes()
    assert "run_local.py" in mac and "requirements-run.txt" in mac
    assert b"run_local.py" in win and b"requirements-run.txt" in win
    assert b"\r\n" in win and b"\n" not in win.replace(b"\r\n", b"")      # CRLF throughout, as cmd.exe expects
    assert all(b < 128 for b in win)                                      # plain ASCII: no code-page surprises
    req = {l.split(">=")[0].strip() for l in (ROOT / "requirements-run.txt").read_text().splitlines()
           if l.strip() and not l.startswith("#")}
    assert req == {"fastapi", "uvicorn", "pydantic", "numpy"}
