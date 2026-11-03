import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "integrations" / "claude_code" / "mirror_hook.py"
spec = importlib.util.spec_from_file_location("mirror_hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

ITEMS = [{"statement": "Prefers short answers", "evidence": 3, "status": "active"}]


@pytest.fixture
def server():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body, self.headers.get("Authorization")))
            out = ({"items": ITEMS, "scenarios": [{"id": "code_review", "name": "Review code my way"}]}
                   if self.path == "/api/retrieve" else {"ok": True})
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def call(payload, url, cfg=None, tmp=None):
    env = {"MIRROR_URL": url, "PATH": "/usr/bin:/bin", "HOME": str(tmp or "/tmp")}
    if cfg is not None:
        p = Path(tmp) / "cfg.json"
        p.write_text(json.dumps(cfg))
        env["MIRROR_HOOK_CONFIG"] = str(p)
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=20)


def test_adds_context_in_the_documented_shape(server, tmp_path):
    url, seen = server
    r = call({"hook_event_name": "UserPromptSubmit", "prompt_text": "Please review this pull request"},
             url, tmp=tmp_path)
    assert r.returncode == 0
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "Prefers short answers" in out["additionalContext"]
    assert "Review code my way" in out["additionalContext"]
    assert seen[0][0] == "/api/retrieve" and seen[0][1]["request"].startswith("Please review")


def test_old_field_name_prompt_still_works(server, tmp_path):
    url, _ = server
    r = call({"prompt": "Help me plan the migration"}, url, tmp=tmp_path)
    assert "additionalContext" in r.stdout


@pytest.mark.parametrize("payload", [
    {"prompt_text": "hi"}, {"prompt_text": "/clear everything now please"},
    {"prompt_text": "a long enough prompt here", "source": "auto"},
    {"prompt_text": "<command-name>/foo</command-name> and more text"}])
def test_skipped_prompts_never_reach_mirror(server, tmp_path, payload):
    url, seen = server
    r = call(payload, url, tmp=tmp_path)
    assert r.returncode == 0 and r.stdout == "" and not seen


def test_fails_open_when_mirror_is_down(tmp_path):
    r = call({"prompt_text": "A perfectly normal long prompt"}, "http://127.0.0.1:9", tmp=tmp_path)
    assert r.returncode == 0 and r.stdout == ""


def test_garbage_input_does_not_break_the_prompt(tmp_path):
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                       capture_output=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0 and r.stdout == ""


def test_capture_is_off_by_default_and_on_when_configured(server, tmp_path):
    url, seen = server
    call({"prompt_text": "I always pick the quiet table"}, url, tmp=tmp_path)
    time.sleep(0.5)
    assert not [p for p, *_ in seen if p == "/api/memories"]
    call({"prompt_text": "I always pick the quiet table"}, url, cfg={"capture": True}, tmp=tmp_path)
    for _ in range(30):
        if [p for p, *_ in seen if p == "/api/memories"]:
            break
        time.sleep(0.1)
    saved = [b for p, b, _ in seen if p == "/api/memories"]
    assert saved == [{"text": "I always pick the quiet table"}]


def test_prompts_that_look_like_secrets_are_never_captured(server, tmp_path):
    url, seen = server
    call({"prompt_text": "my api_key is abc and the password is hunter2"}, url,
         cfg={"capture": True}, tmp=tmp_path)
    time.sleep(0.6)
    assert not [p for p, *_ in seen if p == "/api/memories"]
    assert hook.looks_secret("token: sk-abcdefghijklmnop")
    assert not hook.looks_secret("I like quiet cafes")


def test_install_is_idempotent_keeps_other_hooks_and_uninstall_removes_only_ours(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "settings.local.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls)"]},
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    hook.install(str(proj), "http://x:8000", True)
    hook.install(str(proj), "http://x:8000", True)
    s = json.loads((proj / ".claude" / "settings.local.json").read_text())
    cmds = [h["command"] for e in s["hooks"]["UserPromptSubmit"] for h in e["hooks"]]
    assert cmds.count("echo mine") == 1 and sum("mirror_hook.py" in c for c in cmds) == 1
    assert s["permissions"] == {"allow": ["Bash(ls)"]}
    assert json.loads((proj / ".claude" / "mirror-hook.json").read_text())["capture"] is True
    assert hook.uninstall(str(proj)) is True
    s2 = json.loads((proj / ".claude" / "settings.local.json").read_text())
    assert [h["command"] for e in s2["hooks"]["UserPromptSubmit"] for h in e["hooks"]] == ["echo mine"]
    assert hook.uninstall(str(proj)) is False


def test_muse_payload_uses_the_prompt_field(server, tmp_path):
    url, seen = server
    r = call({"hook_event_name": "UserPromptSubmit", "prompt": "Please review this pull request",
              "session_id": "s", "turn_id": "t", "cwd": str(tmp_path), "transcript_path": None,
              "model": "muse-spark-1.3-contributor", "permission_mode": "default"}, url, tmp=tmp_path)
    assert "additionalContext" in r.stdout and seen[0][1]["request"].startswith("Please review")


def test_other_hook_events_are_ignored(server, tmp_path):
    url, seen = server
    r = call({"hook_event_name": "Stop", "prompt": "Please review this pull request"}, url, tmp=tmp_path)
    assert r.stdout == "" and not seen


def test_install_for_muse_writes_muse_hooks_json_with_an_absolute_interpreter(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    path = hook.install(str(proj), "http://x:8000", False, target="muse")
    assert path == proj / ".muse" / "hooks.json"
    cmd = json.loads(path.read_text())["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert cmd.startswith('"/') and "mirror_hook.py" in cmd           # absolute python path
    assert json.loads((proj / ".muse" / "mirror-hook.json").read_text())["url"] == "http://x:8000"
    assert not (proj / ".claude").exists()
    assert hook.uninstall(str(proj), target="muse") is True
    assert hook.uninstall(str(proj), target="muse") is False


def test_config_is_found_in_the_muse_folder(tmp_path):
    (tmp_path / ".muse").mkdir()
    (tmp_path / ".muse" / "mirror-hook.json").write_text('{"url": "http://muse-test:1"}')
    assert hook.load_config(str(tmp_path))["url"] == "http://muse-test:1"


def test_muse_skill_is_installed_and_removed_without_touching_other_skills(tmp_path):
    proj = tmp_path / "p"
    other = proj / ".agents" / "skills" / "other"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\n")
    dest = hook.install_skill(str(proj))
    assert (dest / "SKILL.md").read_text().startswith("---\nname: mirror")
    assert hook.remove_skill(str(proj)) is True and not dest.exists()
    assert (other / "SKILL.md").exists() and hook.remove_skill(str(proj)) is False


def test_the_mcp_snippet_has_the_shape_muse_code_accepted():
    s = hook.mcp_snippet("http://h:8100/mcp")["mcp_servers"]["mirror"]
    assert s == {"transport": "streamable_http", "url": "http://h:8100/mcp",
                 "headers": {"Authorization": "Bearer <MIRROR_TOKEN>"}}
    assert "<MIRROR_TOKEN>" not in json.dumps(hook.mcp_snippet("u", "real"))


def test_skill_file_has_the_frontmatter_muse_validates():
    text = (hook.SKILL_SRC / "SKILL.md").read_text()
    assert text.startswith("---\nname: mirror\ndescription: ")
