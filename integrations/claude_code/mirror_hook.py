#!/usr/bin/env python3
"""Claude Code and Muse Code hook: bring what Mirror knows into the prompt, optionally remember it.

Registered for UserPromptSubmit in either tool (they use the same hook shape). It reads the hook JSON from stdin, asks the Mirror app
for understanding related to the prompt and prints it as additionalContext, which Claude
sees but which does not appear as a chat message. It fails open: if Mirror is down, slow
or confused, the prompt goes through untouched and the exit code is 0.

  python3 mirror_hook.py --install [--target claude|muse] [--url http://HOST:8000] [--project DIR]
  python3 mirror_hook.py --uninstall [--target claude|muse] [--project DIR]

Config (first found): $MIRROR_HOOK_CONFIG, <project>/.claude/mirror-hook.json,
<project>/.muse/mirror-hook.json, ~/.config/mirror/hook.json. Muse Code runs hooks with a
cleared environment, so the config file is how it finds Mirror. $MIRROR_URL overrides the url.
  {"url": "http://127.0.0.1:8000", "capture": false, "token": ""}
Capture is off by default. When on, the user's own prompt (8 to 2000 characters, and not
one that looks like it holds a secret) is sent to Mirror in the background to be remembered.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
        paths.append(Path(proj) / ".muse" / "mirror-hook.json")
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
    """The typed prompt. Claude Code documents prompt_text; Muse Code sends prompt."""
    for key in ("prompt_text", "prompt", "user_prompt"):
        if isinstance(payload.get(key), str) and payload[key].strip():
            return payload[key].strip()
    return ""


def skip_reason(payload: dict, text: str) -> str | None:
    if os.environ.get("MIRROR_HOOK") == "off":
        return "turned off"
    if payload.get("hook_event_name") not in (None, "UserPromptSubmit"):
        return "not a prompt event"
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


# A standard-library copy of mirror/delivery.py, which is the reference.
# tests/test_delivery.py checks that both write the same text.
GUIDANCE_PATH = (Path(__file__).resolve().parent.parent.parent
                 / "engine" / "skills" / "mirror-use" / "SKILL.md")
FALLBACK_GUIDANCE = (
    "Use what fits the situation to interpret the request and make the choices the user "
    "leaves to you. Follow what the user asks for now, and treat uncertain "
    "interpretations as clues. Apply an understanding only within its conditions and "
    "where it distinguishes plausible options; otherwise choose from the task facts.")
HEADER = ("Background about the user, from Mirror. Each line is a provisional hypothesis "
          "drawn from their own past choices, with how much supports it and when it "
          "applies. None of it is a fact about who they are.")
FOOTER = ("Do not mention this background unprompted, and never make claims about the "
          "user's personality or type.")


def load_guidance(path: Path = GUIDANCE_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK_GUIDANCE
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    body = " ".join(p.strip() for p in text.strip().split("\n\n") if p.strip())
    return " ".join(body.split()) or FALLBACK_GUIDANCE


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n else f"no {word}"


def describe(item: dict) -> list[str]:
    statement = (item.get("statement") or "").strip().rstrip(".")
    status = item.get("status") or "candidate"
    tentative = "" if status == "active" else ", still tentative"
    support = int(item.get("evidence") or 0)
    contrary = int(item.get("counter_examples") or 0)
    lines = [f"- {statement}. [{status} hypothesis{tentative} · "
             f"{_count(support, 'supporting')}, {_count(contrary, 'contrary')}]"]
    conditions = [c for c in (item.get("conditions") or []) if str(c).strip()]
    if conditions:
        lines.append("  Applies when: " + "; ".join(str(c).strip() for c in conditions))
    return lines


def build_context(items: list[dict], scenarios: list[dict] | None = None,
                  guidance: str | None = None) -> str:
    lines = [HEADER, guidance if guidance is not None else load_guidance(), FOOTER]
    if scenarios:
        lines.append("Situation this looks like: " + ", ".join(s["name"] for s in scenarios) + ".")
    for it in items:
        lines.extend(describe(it))
    return "\n".join(lines)


def run(payload: dict, cfg: dict) -> dict | None:
    """The hook's answer for one prompt, or None to leave the prompt alone."""
    text = prompt_of(payload)
    if skip_reason(payload, text):
        return None
    if cfg.get("capture") and MIN_CAPTURE <= len(text) <= MAX_CAPTURE and not looks_secret(text):
        prior = last_assistant_reply(payload.get("transcript_path"))
        spawn_capture({"text": text,
                       "prior_answer": "" if looks_secret(prior) else prior[:MAX_CAPTURE],
                       "episode": str(payload.get("session_id") or "")})
    data = _post(cfg["url"], "/api/retrieve", {"request": text, "budget": 6},
                 cfg.get("token", ""), RETRIEVE_TIMEOUT)
    items = data.get("items") or []
    if not items:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": build_context(items, data.get("scenarios"))}}


TRANSCRIPT_TAIL = 400_000   # bytes read from the end of the transcript


def last_assistant_reply(transcript_path) -> str:
    """The assistant's last text reply in a Claude Code transcript (JSONL), or ''.

    A correction means little without what it corrects. Muse Code sends no transcript,
    so there this is always ''.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TRANSCRIPT_TAIL))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if entry.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts = [content]
        else:
            parts = [c.get("text", "") for c in content or ()
                     if isinstance(c, dict) and c.get("type") == "text"]
        reply = "\n".join(p for p in parts if p).strip()
        if reply:
            return reply
    return ""


def spawn_capture(body: dict) -> None:
    """Remember the prompt without making the user wait for the extractor."""
    p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--capture"],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    p.stdin.write(json.dumps(body).encode("utf-8"))
    p.stdin.close()


def capture_from_stdin() -> None:
    cfg = load_config()
    raw = sys.stdin.read()
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):        # older callers sent the bare prompt
        body = {"text": raw}
    body = {k: v for k, v in body.items() if k in ("text", "prior_answer", "episode") and v}
    _post(cfg["url"], "/api/memories", body, cfg.get("token", ""), 180)


# -- install / uninstall -------------------------------------------------------

SKILL_SRC = Path(__file__).resolve().parent.parent / "muse" / "skills" / "mirror"
# how to apply what comes back; installed beside the Muse skill, never over it
USE_SKILL_SRC = GUIDANCE_PATH.parent

TARGETS = {"claude": (".claude", "settings.local.json"), "muse": (".muse", "hooks.json")}


def _settings_path(project: str, target: str = "claude") -> Path:
    folder, name = TARGETS[target]
    return Path(project) / folder / name


def _is_ours(entry: dict) -> bool:
    return any("mirror_hook.py" in h.get("command", "") for h in entry.get("hooks", []))


def install(project: str, url: str, capture: bool, target: str = "claude",
            token: str = "") -> Path:
    path = _settings_path(project, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(path.read_text()) if path.exists() else {}
    events = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    events[:] = [e for e in events if not _is_ours(e)]
    # an absolute interpreter, because Muse Code runs hooks with a cleared environment
    entry = {"type": "command", "command": f'"{sys.executable}" "{os.path.abspath(__file__)}"',
             "timeout": 8}
    if target == "claude":
        entry["statusMessage"] = "Asking Mirror..."
    events.append({"hooks": [entry]})
    path.write_text(json.dumps(settings, indent=2) + "\n")
    cfg_path = path.parent / "mirror-hook.json"
    cfg_path.write_text(
        json.dumps({"url": url, "capture": capture, "token": token}, indent=2) + "\n")
    if token:
        cfg_path.chmod(0o600)      # it holds a credential
    return path


def install_skill(project: str) -> Path | None:
    """Muse Code skill that tells the agent when to call Mirror's MCP tools."""
    if not SKILL_SRC.exists():
        return None
    dest = Path(project) / ".agents" / "skills" / "mirror"
    shutil.copytree(SKILL_SRC, dest, dirs_exist_ok=True)
    if (USE_SKILL_SRC / "SKILL.md").exists():
        shutil.copytree(USE_SKILL_SRC, dest.parent / "mirror-use", dirs_exist_ok=True)
    return dest


def remove_skill(project: str) -> bool:
    removed = False
    for name in ("mirror", "mirror-use"):
        dest = Path(project) / ".agents" / "skills" / name
        marker = dest / "SKILL.md"
        if marker.exists() and f"name: {name}\n" in marker.read_text():
            shutil.rmtree(dest)
            removed = removed or name == "mirror"
    return removed


def mcp_snippet(url: str, token: str = "<MIRROR_TOKEN>") -> dict:
    """The mcp_servers entry for ~/.config/muse/settings.json (streamable HTTP)."""
    return {"mcp_servers": {"mirror": {"transport": "streamable_http", "url": url,
                                       "headers": {"Authorization": f"Bearer {token}"}}}}


def uninstall(project: str, target: str = "claude") -> bool:
    path = _settings_path(project, target)
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
    ap.add_argument("--no-skill", action="store_true", help="with --target muse: skip the skill")
    ap.add_argument("--print-mcp", metavar="URL", help="print the Muse Code mcp_servers entry for "
                    "the edge server's /mcp URL (add it to ~/.config/muse/settings.json yourself)")
    ap.add_argument("--token-file", help="read the API token from this file (with --install: store it in the hook config; with --print-mcp: put it in the output)")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--target", choices=sorted(TARGETS), default="claude",
                    help="which tool to install into: claude (Claude Code) or muse (Muse Code)")
    args = ap.parse_args(argv)
    if args.print_mcp:
        token = Path(args.token_file).read_text().strip() if args.token_file else "<MIRROR_TOKEN>"
        print(json.dumps(mcp_snippet(args.print_mcp, token), indent=2))
        return 0
    if args.install:
        token = Path(args.token_file).read_text().strip() if args.token_file else ""
        print("installed:", install(args.project, args.url, args.cap, args.target, token))
        if args.target == "muse" and not args.no_skill:
            print("skill:", install_skill(args.project))
        return 0
    if args.uninstall:
        print("removed" if uninstall(args.project, args.target) else "nothing to remove")
        if args.target == "muse" and remove_skill(args.project):
            print("skill removed")
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
