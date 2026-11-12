"""Panggil Claude lewat Claude Code (`claude -p`) supaya memakai LANGGANAN Claude (Pro/Max),
bukan kredit API.

Setup sekali: install Claude Code, jalankan `claude` lalu login dengan akun Claude kamu.
Pilih backend lewat .env:
  CLAUDE_BACKEND=cli    pakai langganan (claude -p)
  CLAUDE_BACKEND=api    pakai ANTHROPIC_API_KEY (kredit API)
  CLAUDE_BACKEND=auto   (default) cli kalau perintah `claude` ada, kalau tidak api
  CLAUDE_MODEL=sonnet   model untuk mode cli (sonnet / opus / haiku); default sonnet supaya hemat kuota
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def claude_bin() -> str | None:
    custom = os.environ.get("CLAUDE_BIN")
    if custom:
        return custom
    # utamakan claude.exe (installer native): claude.cmd dari npm dijalankan lewat cmd.exe yang
    # merusak argumen panjang/berbaris. Installer native menaruhnya di ~/.local/bin.
    native = os.path.expanduser(r"~\.local\bin\claude.exe" if os.name == "nt" else "~/.local/bin/claude")
    if os.path.exists(native):
        return native
    return shutil.which("claude")


def backend() -> str:
    b = (os.environ.get("CLAUDE_BACKEND") or "auto").strip().lower()
    if b in ("cli", "api"):
        return b
    return "cli" if claude_bin() else "api"


def model() -> str:
    from .settings import load
    return (os.environ.get("CLAUDE_MODEL") or load().get("claude_model") or "sonnet").strip()


def label(api_model: str) -> str:
    return f"langganan Claude via claude -p, model {model()}" if backend() == "cli" else f"API, {api_model}"


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError(f"jawaban bukan JSON: {text[:160]}")
    return json.loads(text[start:end + 1])


def run_text(system: str, prompt: str, model_name: str | None = None, timeout: int = 900) -> str:
    """Plain-text answer from `claude -p` (used by the OpenAI-compatible bridge)."""
    exe = claude_bin()
    if not exe:
        raise RuntimeError("perintah `claude` tidak ditemukan. Install Claude Code lalu login sekali dengan `claude`")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [exe, "-p", "--output-format", "json", "--model", model_name or model(), "--tools", "",
               "--no-session-persistence"]
        if system:
            sp = os.path.join(tmp, "system.txt")
            with open(sp, "w", encoding="utf-8") as f:
                f.write(system)
            cmd += ["--system-prompt-file", sp]
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=tmp, env=env, timeout=timeout, creationflags=flags)
    try:
        data = json.loads((res.stdout or "").strip())
    except json.JSONDecodeError:
        lines = (res.stderr or res.stdout or f"kode {res.returncode}").strip().splitlines()
        raise RuntimeError(f"claude -p gagal: {lines[-1][:200] if lines else res.returncode}") from None
    if data.get("is_error") or data.get("subtype", "success") != "success":
        raise RuntimeError(f"claude -p gagal: {str(data.get('result') or data.get('subtype'))[:200]}")
    return str(data.get("result") or "")


def ask(system: str, prompt: str, schema: type[T], timeout: int = 600) -> T:
    """Kirim prompt ke `claude -p`, minta jawaban JSON sesuai skema pydantic, kembalikan objeknya."""
    exe = claude_bin()
    if not exe:
        raise RuntimeError("perintah `claude` tidak ditemukan. Install Claude Code lalu login dengan "
                           "akun Claude kamu (jalankan `claude` sekali), atau set CLAUDE_BACKEND=api")
    js = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    sys_prompt = (system + "\n\nJawab HANYA dengan satu objek JSON yang sesuai skema berikut, tanpa teks lain "
                  "dan tanpa memakai tool apa pun:\n" + js)
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # paksa pakai login langganan
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # dijalankan dari folder kosong supaya CLAUDE.md / setelan project lain tidak ikut terbaca;
    # prompt lewat stdin & system prompt lewat file (batas/escaping command line Windows)
    with tempfile.TemporaryDirectory() as tmp:
        sp = os.path.join(tmp, "system.txt")
        with open(sp, "w", encoding="utf-8") as f:
            f.write(sys_prompt)
        cmd = [exe, "-p", "--output-format", "json", "--model", model(), "--tools", "",
               "--no-session-persistence", "--system-prompt-file", sp]
        if not exe.lower().endswith((".cmd", ".bat")):  # JSON berkutip tidak aman lewat cmd.exe
            cmd += ["--json-schema", js]
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", cwd=tmp, env=env, timeout=timeout, creationflags=flags)
    out = (res.stdout or "").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        lines = (res.stderr or out or f"kode {res.returncode}").strip().splitlines()
        msg = lines[-1][:200] if lines else str(res.returncode)
        if any(w in msg.lower() for w in ("auth", "login", "log in")):
            msg += " -> jalankan `claude` sekali lalu login dengan akun Claude kamu"
        raise RuntimeError(f"claude -p gagal: {msg}") from None
    if data.get("is_error") or data.get("subtype", "success") != "success":
        msg = str(data.get("result") or data.get("subtype"))[:200]
        if any(w in msg.lower() for w in ("auth", "login", "log in")):
            msg += " -> buka terminal, jalankan `claude` sekali lalu login dengan akun Claude kamu"
        raise RuntimeError(f"claude -p gagal: {msg}")
    payload = data.get("structured_output")
    if payload is None:
        payload = _extract_json(str(data.get("result", "")))
    return schema.model_validate(payload)
