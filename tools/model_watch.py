#!/usr/bin/env python3
"""Weekly check of MusicaGuide's model manifest (musicaguide/models.json).

For each provider whose API key is set (OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY):
  1. list the models the provider serves right now,
  2. flag manifest models that are missing, deprecated or scheduled for shutdown,
  3. send a tiny test message to the default and fallback models,
  4. repair what can be repaired safely (drop dead suggestions, promote a healthy
     suggestion when the default dies, map every dead ID to its replacement),
  5. list new models worth a look (never added automatically).

Writes the repaired manifest in place and a Markdown report. The GitHub workflow opens a
pull request when the file changed, so a human always approves before it goes live.

Exit codes: 0 = fine or repaired, 1 = a problem it could not repair (the workflow fails
and GitHub emails you), 2 = the manifest itself is invalid.

Usage:
  python3 tools/model_watch.py                # check + repair + report
  python3 tools/model_watch.py --validate     # schema check only (no network, no keys)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "musicaguide" / "models.json"
REPORT = ROOT / "model-watch-report.md"

PROVIDERS = ("openai", "anthropic", "groq")
ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")
TOKEN_PARAMS = {"max_tokens", "max_completion_tokens"}

KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "groq": "GROQ_API_KEY"}
LIST_URL = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models?limit=1000",
    "groq": "https://api.groq.com/openai/v1/models",
}
CHAT_URL = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
}


# ── Validation (mirrors ModelManifest::parse in the plugin) ─────────────────
def validate(manifest: dict) -> list[str]:
    errs = []
    if manifest.get("schema") != 1:
        errs.append("schema must be 1")
    provs = manifest.get("providers")
    if not isinstance(provs, dict):
        return errs + ["providers missing"]
    for name in PROVIDERS:
        p = provs.get(name)
        if not isinstance(p, dict):
            errs.append(f"{name}: missing")
            continue
        for field in ("default", "fallback"):
            if not ID_RE.match(str(p.get(field, ""))):
                errs.append(f"{name}.{field}: invalid model id {p.get(field)!r}")
        if p.get("token_param") not in TOKEN_PARAMS:
            errs.append(f"{name}.token_param must be one of {sorted(TOKEN_PARAMS)}")
        mot = p.get("max_output_tokens")
        if not isinstance(mot, int) or not 256 <= mot <= 16384:
            errs.append(f"{name}.max_output_tokens must be an int 256..16384")
        for s in p.get("suggested", []):
            if not ID_RE.match(str(s)):
                errs.append(f"{name}.suggested: invalid id {s!r}")
    for prov, mapping in (manifest.get("retired") or {}).items():
        for frm, to in mapping.items():
            if not ID_RE.match(frm.rstrip("*")) or not ID_RE.match(str(to)):
                errs.append(f"retired.{prov}: bad entry {frm!r} -> {to!r}")
    # A default/fallback/suggestion that the retired map would rewrite would make the
    # plugin migrate users onto a dead model and loop.
    for name in PROVIDERS:
        p = provs.get(name) or {}
        for mid in [p.get("default"), p.get("fallback"), *p.get("suggested", [])]:
            if mid and replacement_for(manifest, name, mid):
                errs.append(f"{name}: {mid} is in use but also on the retired list")
    return errs


def replacement_for(manifest: dict, provider: str, model: str) -> str:
    """Same rule as the plugin: exact match wins, else longest '*' prefix."""
    mapping = (manifest.get("retired") or {}).get(provider) or {}
    if model in mapping:
        return mapping[model]
    best, best_len = "", -1
    for frm, to in mapping.items():
        if frm.endswith("*") and model.lower().startswith(frm[:-1].lower()) and len(frm) > best_len:
            best, best_len = to, len(frm)
    return "" if best.lower() == model.lower() else best


# ── Provider calls ──────────────────────────────────────────────────────────
def _headers(provider: str, key: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:  # network
        return 0, {"error": {"message": str(e)}}


def list_models(provider: str, key: str) -> dict[str, dict]:
    """id -> {'status': 'active'|'deprecated'|'retired', 'shutdown': str|None}"""
    status, body = http("GET", LIST_URL[provider], _headers(provider, key))
    if status != 200:
        raise RuntimeError(f"{provider} /models returned {status}: {body.get('error')}")
    out = {}
    for m in body.get("data", []):
        mid = m.get("id", "")
        lifecycle = m.get("lifecycle") or "active"
        if m.get("active") is False:
            lifecycle = "retired"
        shutdown = m.get("shutdown_date")
        if shutdown and lifecycle == "active":
            lifecycle = "deprecated"
        out[mid] = {"status": lifecycle, "shutdown": shutdown}
    return out


def smoke_test(provider: str, key: str, cfg: dict, model: str) -> tuple[bool, str]:
    msgs = [{"role": "user", "content": "Reply with the single word: ok"}]
    if provider == "anthropic":
        body = {"model": model, "max_tokens": 64, "messages": msgs}
    else:
        body = {"model": model, "messages": msgs, cfg["token_param"]: 256}
        if cfg.get("send_temperature"):
            body["temperature"] = cfg.get("temperature", 0.7)
    status, resp = http("POST", CHAT_URL[provider], _headers(provider, key), body)
    if status == 200:
        return True, "ok"
    if status == 429:
        return True, "rate limited (treated as alive)"
    msg = (resp.get("error") or {}).get("message", "") if isinstance(resp.get("error"), dict) else str(resp.get("error"))
    return False, f"HTTP {status}: {msg[:200]}"


# ── The check itself (pure, so it can be tested with fakes) ─────────────────
def is_alive(info: dict[str, dict], model: str) -> bool:
    m = info.get(model)
    return m is not None and m["status"] == "active"


def candidates(cfg: dict, info: dict[str, dict]) -> list[str]:
    inc, exc = cfg.get("list_include", []), cfg.get("list_exclude", [])
    out = []
    for mid, m in info.items():
        if m["status"] != "active":
            continue
        if inc and not any(mid.lower().startswith(p.lower()) for p in inc):
            continue
        if any(b.lower() in mid.lower() for b in exc):
            continue
        out.append(mid)
    return sorted(out)


def provider_says_dead(info: dict[str, dict], model: str) -> bool:
    """Only the provider's own deprecated/retired flag counts. A model merely missing from
    the list may just be unavailable to the checker's key, so it is never auto-retired."""
    m = info.get(model)
    return m is not None and m["status"] in ("deprecated", "retired")


def check_provider(manifest: dict, provider: str, info: dict[str, dict], smoke) -> tuple[list[str], list[str], list[str]]:
    """Mutates manifest. Returns (changes, problems, notes)."""
    cfg = manifest["providers"][provider]
    retired = manifest.setdefault("retired", {}).setdefault(provider, {})
    changes, problems, notes = [], [], []

    def retire(old: str, new: str):
        if old != new and old not in retired and provider_says_dead(info, old):
            retired[old] = new
            changes.append(f"{provider}: map retired `{old}` -> `{new}`")

    def describe(mid: str) -> str:
        m = info.get(mid)
        if m is None:
            return "not listed for this key"
        return m["status"] + (f", shutdown {m['shutdown']}" if m.get("shutdown") else "")

    # Suggestions: drop the ones that are not active.
    dropped, alive_sugg = [], []
    for s in cfg.get("suggested", []):
        if is_alive(info, s):
            alive_sugg.append(s)
        else:
            dropped.append(s)
            changes.append(f"{provider}: drop suggestion `{s}` ({describe(s)})")
    cfg["suggested"] = alive_sugg

    # Default and fallback: list check, then a real request.
    for field in ("default", "fallback"):
        mid = cfg[field]
        listed = is_alive(info, mid)
        ok_call, detail = smoke(mid) if listed else (False, describe(mid))
        if listed and ok_call:
            continue
        replacement = next((s for s in alive_sugg if s != mid and smoke(s)[0]), None)
        if replacement:
            cfg[field] = replacement
            retire(mid, replacement)
            changes.append(f"{provider}: {field} `{mid}` failed ({detail}); now `{replacement}`")
        else:
            problems.append(f"{provider}: {field} `{mid}` failed ({detail}) and no healthy suggestion to promote")

    if cfg["default"] not in cfg["suggested"]:
        cfg["suggested"].insert(0, cfg["default"])

    # Dead suggestions still need to route saved settings somewhere.
    for s in dropped:
        retire(s, cfg["default"])
        if not provider_says_dead(info, s):
            notes.append(f"{provider}: `{s}` is not listed for the checker's key; dropped from the dropdown but NOT retired (may be an access-tier gap)")

    new = [m for m in candidates(cfg, info) if m not in cfg["suggested"] and not replacement_for(manifest, provider, m)]
    if new:
        notes.append(f"{provider}: {len(new)} active models not in the dropdown suggestions: " + ", ".join(f"`{m}`" for m in new[:25]))
    return changes, problems, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true", help="schema check only")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    errs = validate(manifest)
    if errs:
        print("Manifest invalid:\n- " + "\n- ".join(errs))
        return 2
    if args.validate:
        print("Manifest valid.")
        return 0

    all_changes, all_problems, all_notes, skipped = [], [], [], []
    for provider in PROVIDERS:
        key = os.environ.get(KEY_ENV[provider], "").strip()
        if not key:
            skipped.append(f"{provider} (no {KEY_ENV[provider]} secret)")
            continue
        try:
            info = list_models(provider, key)
        except RuntimeError as e:
            all_problems.append(str(e))
            continue
        cfg = manifest["providers"][provider]
        cache: dict[str, tuple[bool, str]] = {}

        def smoke(model, provider=provider, key=key, cfg=cfg, cache=cache):
            if model not in cache:
                cache[model] = smoke_test(provider, key, cfg, model)
            return cache[model]

        c, p, n = check_provider(manifest, provider, info, smoke)
        all_changes += c
        all_problems += p
        all_notes += n

    errs = validate(manifest)
    if errs:  # never write a manifest the plugin would reject
        all_problems += [f"repair produced an invalid manifest: {e}" for e in errs]
        all_changes = []

    if all_changes:
        manifest["updated"] = dt.date.today().isoformat()
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")

    lines = [f"# MusicaGuide model check, {dt.date.today().isoformat()}", ""]
    lines += ["## Changes made (review before merging)"] + [f"- {c}" for c in all_changes] if all_changes else ["No manifest changes needed."]
    if all_problems:
        lines += ["", "## Needs you"] + [f"- {p}" for p in all_problems]
    if all_notes:
        lines += ["", "## New models (not added automatically)"] + [f"- {n}" for n in all_notes]
    if skipped:
        lines += ["", "## Skipped"] + [f"- {s}" for s in skipped]
    lines += ["", "After merging, also copy `musicaguide/models.json` into the plugin repo's "
              "`resources/models.json` before the next release (offline fallback)."]
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
