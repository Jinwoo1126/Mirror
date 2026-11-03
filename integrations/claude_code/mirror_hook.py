#!/usr/bin/env python3
"""Claude Code hook: bring what Mirror knows into the prompt, optionally remember it.

Registered for UserPromptSubmit. It reads the hook JSON from stdin, asks the Mirror app
for understanding related to the prompt and prints it as additionalContext, which Claude
sees but which does not appear as a chat message. It fails open: if Mirror is down, slow
or confused, the prompt goes through untouched and the exit code is 0.

  python3 mirror_hook.py --install [--url http://HOST:8000] [--capture] [--project DIR]
  python3 mirror_hook.py --uninstall [--project DIR]

Config (first found): $MIRROR_HOOK_CONFIG, <project>/.claude/mirror-hook.json,
~/.config/mirror/hook.json. $MIRROR_URL overrides the url.
  {"url": "http://127.0.0.1:8000", "capture": false, "token": ""}
Capture is off by default. When on, the user's own prompt (8 to 2000 characters, and not
one that looks like it holds a secret) is sent to Mirror in the background to be remembered.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000"
MIN_PROMPT, MIN_CAPTURE, MAX_CAPTURE = 12, 8, 2000
RETRIEVE_TIMEOUT = 5
SECRET = re.compile(
    r"api[_-]?key|secret|passw(or)?d|token|bearer\s|sk-[A-Za-z0-9]{10,}|-----BEGIN|"
    r"AKIA[0-9A-Z]{12}", re.IGNORECASE)


def config_paths(project: str | None = None) -> list[Path]:
    paths = []
    if os.environ.get("MIRROR_HOOK_CONFIG"):
        paths.append(Path(os.environ["MIRROR_HOOK_CONFIG"]))
    proj = project or os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        paths.append(Path(proj) / ".claude" / "mirror-hook.json")
    paths.append(Path.home() / ".config" / "mirror" / "hook.json")
    return paths


def load_config(project: str | None = None) -> dict:
    cfg = {"url": DEFAULT_URL, "capture": False, "token": ""}
    for p in config_paths(project):
        try:
            cfg.update(json.loads(p.read_text()))
            break
        except (OSError, ValueError):
            continue
    if os.environ.get("MIRROR_URL"):
        cfg["url"] = os.environ["MIRROR_URL"]
    return cfg


def prompt_of(payload: dict) -> str:
    return str(payload.get("prompt_text") or payload.get("prompt") or "").strip()


def skip_reason(payload: dict, text: str) -> str | None:
    if os.environ.get("MIRROR_HOOK") == "off":
        return "turned off"
    if payload.get("source") == "auto":
        return "not typed by the user"
    if len(text) < MIN_PROMPT:
        return "too short"
    if text.startswith("/") or "<command-name>" in text:
        return "slash command"
    return None


def looks_secret(text: str) -> bool:
    return bool(SECRET.search(text))


def _post(url: str, path: str, body: dict, token: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def build_context(items: list[dict], scenarios: list[dict] | None = None) -> str:
    lines = ["Background about the user, from Mirror's memory of their past conversations. "
             "Use it only when it is relevant, do not mention it unprompted, and never make "
             "claims about the user's personality or type."]
    if scenarios:
        lines.append("Situation this looks like: " + ", ".join(s["name"] for s in scenarios) + ".")
    for it in items:
        lines.append(f"- {it['statement']} ({it.get('evidence', 0)} evidence, "
                     f"{it.get('status', 'candidate')})")
    return "\n".join(lines)


def run(payload: dict, cfg: dict) -> dict | None:
    """The hook's answer for one prompt, or None to leave the prompt alone."""
    text = prompt_of(payload)
    if skip_reason(payload, text):
        return None
    if cfg.get("capture") and MIN_CAPTURE <= len(text) <= MAX_CAPTURE and not looks_secret(text):
        spawn_capture(text)
    data = _post(cfg["url"], "/api/retrieve", {"request": text, "budget": 6},
                 cfg.get("token", ""), RETRIEVE_TIMEOUT)
    items = data.get("items") or []
    if not items:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": build_context(items, data.get("scenarios"))}}


def spawn_capture(text: str) -> None:
    """Remember the prompt without making the user wait for the extractor."""
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--capture"],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    p.stdin.write(text.encode("utf-8"))
    p.stdin.close()


def capture_from_stdin() -> None:
    cfg = load_config()
    text = sys.stdin.read()
    _post(cfg["url"], "/api/memories", {"text": text}, cfg.get("token", ""), 180)


# -- install / uninstall -------------------------------------------------------

def _settings_path(project: str) -> Path:
    return Path(project) / ".claude" / "settings.local.json"


def _is_ours(entry: dict) -> bool:
    return any("mirror_hook.py" in h.get("command", "") for h in entry.get("hooks", []))


def install(project: str, url: str, capture: bool) -> Path:
    path = _settings_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text()) if path.exists() else {}
    events = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    events[:] = [e for e in events if not _is_ours(e)]
    events.append({"hooks": [{
        "type": "command", "command": f'python3 "{os.path.abspath(__file__)}"',
        "timeout": 8, "statusMessage": "Asking Mirror..."}]})
    path.write_text(json.dumps(settings, indent=2) + "\n")
    (path.parent / "mirror-hook.json").write_text(
        json.dumps({"url": url, "capture": capture, "token": ""}, indent=2) + "\n")
    return path


def uninstall(project: str) -> bool:
    path = _settings_path(project)
    if not path.exists():
        return False
    settings = json.loads(path.read_text())
    events = settings.get("hooks", {}).get("UserPromptSubmit", [])
    kept = [e for e in events if not _is_ours(e)]
    changed = len(kept) != len(events)
    if changed:
        if kept:
            settings["hooks"]["UserPromptSubmit"] = kept
        else:
            settings["hooks"].pop("UserPromptSubmit", None)
            if not settings["hooks"]:
                settings.pop("hooks")
        path.write_text(json.dumps(settings, indent=2) + "\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mirror hook for Claude Code")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--capture", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--capture-prompts", dest="cap", action="store_true",
                    help="with --install: also remember the user's prompts")
    ap.add_argument("--project", default=os.getcwd())
    args = ap.parse_args(argv)
    if args.install:
        print("installed:", install(args.project, args.url, args.cap))
        return 0
    if args.uninstall:
        print("removed" if uninstall(args.project) else "nothing to remove")
        return 0
    try:
        if args.capture:
            capture_from_stdin()
            return 0
        payload = json.loads(sys.stdin.read() or "{}")
        out = run(payload, load_config(payload.get("cwd")))
        if out:
            print(json.dumps(out))
    except Exception as e:                      # fail open: never get in the way of the prompt
        if os.environ.get("MIRROR_HOOK_DEBUG"):
            print(f"mirror hook: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
