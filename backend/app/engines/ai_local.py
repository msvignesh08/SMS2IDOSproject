"""
SMS-2 IDOS — LOCAL LLM (the model runs on the plant's own server)
=================================================================
The offline answer to the reasoning problem. Where ai_brain.py calls out to
Anthropic, this talks to a model served on the plant's own hardware, so a
question and the heat data behind it never leave the site. That is the whole
point: a melt shop's production data is commercially sensitive, and plenty of
plant networks have no outbound route at all.

NOT A NEW DEPENDENCY. Speaks the OpenAI-compatible /v1/chat/completions shape
over urllib, which is what Ollama, llama.cpp's server, vLLM, LM Studio and
LocalAI all expose. So the plant picks the runtime and the model; SMS-2 only
needs the URL.

    SMS2_LOCAL_LLM_URL     e.g. http://127.0.0.1:11434/v1   <- turns it on
    SMS2_LOCAL_LLM_MODEL   e.g. qwen2.5:7b-instruct
    SMS2_LOCAL_LLM_KEY     optional; most local servers need none

SPECIALISED, NOT GENERIC. A 7B model does not know how this shop runs, so it
is given the knowledge rather than expected to have it: app/data/metallurgy.md
is injected on every request, alongside the same READ-ONLY plant tools
ai_brain.py uses. That file is the specialisation — edit it when plant
practice changes, no code involved.

HONEST ABOUT ITS CEILING. A small local model reasons less well than a
frontier one and calls tools less reliably. It is better than a keyword match
and worse than Opus; the resolution order in routers/ai.py reflects that.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy.orm import Session

from . import ai_brain

TIMEOUT = 180           # local CPU inference is slow; be patient rather than wrong
MAX_TURNS = 8
_KNOWLEDGE = Path(__file__).resolve().parent.parent / "data" / "metallurgy.md"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def config() -> dict:
    """What is configured — WITHOUT any key."""
    url = _env("SMS2_LOCAL_LLM_URL")
    return {"enabled": bool(url),
            "url": url,
            "model": _env("SMS2_LOCAL_LLM_MODEL") or "(server default)",
            "key_set": bool(_env("SMS2_LOCAL_LLM_KEY")),
            "knowledge_pack": _KNOWLEDGE.name if _KNOWLEDGE.exists() else None}


def knowledge() -> str:
    """The plant knowledge pack. Missing is not fatal — the model still has
    the persona and the tools, it is just less fluent in this shop."""
    try:
        return _KNOWLEDGE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _openai_tools() -> list[dict]:
    """ai_brain's tools in the OpenAI function shape. One definition of the
    tools, two wire formats — a second copy would drift."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in ai_brain.TOOLS]


def _post(path: str, body: dict) -> dict:
    url = _env("SMS2_LOCAL_LLM_URL").rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    key = _env("SMS2_LOCAL_LLM_KEY")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def ping() -> dict:
    """Is the local server up and which models does it hold?

    Used by the admin screen so 'is the AI on' is answerable without asking
    the model a question and waiting a minute for it.
    """
    if not config()["enabled"]:
        return {"ok": False, "error": "SMS2_LOCAL_LLM_URL is not set."}
    url = _env("SMS2_LOCAL_LLM_URL").rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url)
        key = _env("SMS2_LOCAL_LLM_KEY")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        return {"ok": True,
                "models": [m.get("id") for m in (data.get("data") or [])][:20]}
    except urllib.error.URLError as e:
        return {"ok": False,
                "error": f"Could not reach the local model server at "
                         f"{_env('SMS2_LOCAL_LLM_URL')} ({e.reason}). Is it running?"}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def ask(db: Session, question: str, mode: str = "production_engineer",
        history: list | None = None) -> dict:
    """Answer using the plant's own model. Never raises."""
    if not config()["enabled"]:
        return {"ok": False, "reason": "not_configured"}

    system = (ai_brain.SYSTEM
              + "\n\nMODE: " + ai_brain.MODES.get(mode, ai_brain.MODES["production_engineer"])
              + "\n\n" + knowledge())
    messages = [{"role": "system", "content": system}]
    messages += list(history or [])
    messages.append({"role": "user", "content": question})

    body_base = {"model": _env("SMS2_LOCAL_LLM_MODEL") or "default",
                 "tools": _openai_tools(),
                 # Low but not zero: a melt-shop answer should be stable, and
                 # a small model at high temperature invents plant numbers.
                 "temperature": 0.2}
    tool_calls: list[str] = []
    try:
        for _ in range(MAX_TURNS):
            r = _post("/chat/completions", {**body_base, "messages": messages})
            choice = (r.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            calls = msg.get("tool_calls") or []
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             **({"tool_calls": calls} if calls else {})})
            if not calls:
                break
            for c in calls:
                fn = (c.get("function") or {})
                name = fn.get("name", "")
                tool_calls.append(name)
                try:
                    # Small models sometimes emit arguments as a dict rather
                    # than the JSON string the spec asks for. Accept both —
                    # rejecting it would fail the whole answer over a quirk.
                    raw = fn.get("arguments") or "{}"
                    args = raw if isinstance(raw, dict) else json.loads(raw)
                    out = ai_brain._run_tool(db, name, args)
                except Exception as e:                       # noqa: BLE001
                    out = {"error": f"{type(e).__name__}: {e}"}
                messages.append({"role": "tool",
                                 "tool_call_id": c.get("id", name),
                                 "name": name,
                                 "content": json.dumps(out, default=str)[:30000]})
        text = (messages[-1].get("content") or "").strip() if messages else ""
        if not text:
            # Ended on a tool result with nothing said: ask once for the answer.
            messages.append({"role": "user",
                             "content": "Now answer the question using what you read."})
            r = _post("/chat/completions", {**body_base, "messages": messages})
            text = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return {"ok": True, "answer": (text or "").strip() or "(no answer produced)",
                "source": "local", "model": body_base["model"], "mode": mode,
                "tools_used": tool_calls}
    except urllib.error.URLError as e:
        return {"ok": False, "reason": "error",
                "detail": f"Local model server unreachable ({e.reason})."}
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "reason": "error", "detail": f"{type(e).__name__}: {e}"[:400]}
