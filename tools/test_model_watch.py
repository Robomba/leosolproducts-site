"""Offline tests for tools/model_watch.py. Run: python3 tools/test_model_watch.py"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import model_watch as mw  # noqa: E402

BASE = json.loads(mw.MANIFEST.read_text())
fails = 0


def check(ok, what):
    global fails
    print(("PASS  " if ok else "FAIL  ") + what)
    fails += 0 if ok else 1


def raw(d):
    return {k: {"status": v, "shutdown": None} for k, v in d.items()}


always_ok = lambda m: (True, "ok")  # noqa: E731

# 1. Shipped manifest validates; retired rule matches the plugin's.
check(mw.validate(BASE) == [], "shipped manifest valid")
check(mw.replacement_for(BASE, "openai", "gpt-4o-mini-2024-07-18") == "gpt-6-luna", "prefix rule like plugin")
check(mw.replacement_for(BASE, "openai", "gpt-5.6-sol") == "", "gpt-5 exact rule does not catch gpt-5.6")

# 2. All healthy -> no changes.
m = copy.deepcopy(BASE)
c, p, n = mw.check_provider(m, "openai", raw({"gpt-6-luna": "active", "gpt-6.1-terra": "active", "gpt-6.1-sol": "active", "gpt-6.2-astra": "active"}), always_ok)
check(c == [] and p == [], "healthy provider: no changes")
check(any("gpt-6.2-astra" in x for x in n), "new model reported, not added")
check("gpt-6.2-astra" not in m["providers"]["openai"]["suggested"], "new model not auto-added")

# 3. Default deprecated -> promoted + retired mapping.
m = copy.deepcopy(BASE)
c, p, n = mw.check_provider(m, "openai", raw({"gpt-6-luna": "deprecated", "gpt-6.1-terra": "active", "gpt-6.1-sol": "active"}), always_ok)
o = m["providers"]["openai"]
check(o["default"] == "gpt-6.1-terra" and o["fallback"] == "gpt-6.1-terra", "dead default promoted to next healthy suggestion")
check(m["retired"]["openai"].get("gpt-6-luna") == "gpt-6.1-terra", "dead default mapped forward")
check(p == [] and mw.validate(m) == [], "repaired manifest valid")

# 4. Missing (not flagged) suggestion: dropped from dropdown but NOT retired.
m = copy.deepcopy(BASE)
c, p, n = mw.check_provider(m, "anthropic", raw({"claude-haiku-5-5": "active", "claude-sonnet-5-5": "active"}), always_ok)
check("claude-opus-5-5" not in m["providers"]["anthropic"]["suggested"], "unlisted suggestion dropped")
check("claude-opus-5-5" not in m["retired"]["anthropic"], "unlisted suggestion NOT retired")
check(any("access-tier" in x for x in n), "access-tier note added")

# 5. Listed but the real call fails (e.g. param change) -> problem if nothing to promote.
m = copy.deepcopy(BASE)
bad = lambda mid: (False, "HTTP 400: Unsupported parameter")  # noqa: E731
c, p, n = mw.check_provider(m, "groq", raw({"openai/gpt-oss-120b": "active", "openai/gpt-oss-20b": "active", "qwen/qwen3.6-27b": "active", "groq/compound": "active"}), bad)
check(len(p) == 2, "call failures with no healthy alternative are reported as problems")
check(m["providers"]["groq"]["default"] == "openai/gpt-oss-120b", "default untouched when nothing healthy")

# 6. Validation catches a manifest the plugin would reject.
m = copy.deepcopy(BASE)
m["providers"]["openai"]["token_param"] = "max_output_tokens"
check(any("token_param" in e for e in mw.validate(m)), "bad token_param rejected")
m = copy.deepcopy(BASE)
m["retired"]["openai"]["gpt-6*"] = "gpt-6.1-sol"
check(any("also on the retired list" in e for e in mw.validate(m)), "retiring an in-use model rejected")

print("\nALL PASSED" if fails == 0 else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
