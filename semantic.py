"""Local SLM semantic scoring with bounded output, deadlines, and an audit log."""

from hashlib import sha256
import json
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


SYSTEM = """Rate a sensor window for building monitoring. Reply with just one digit.
0 means routine. 1 means investigate. 2 means critical heat at 32 C or above.
Below 32 C, consider warm and humid conditions in the equipment lab more
important than comparable conditions in a public hall. A hall should normally
be routine unless it is both unusually warm and humid. An isolated moderate
spike is less important than heat coupled with humidity. Use the measured
temperature maximum for the critical threshold.
Examples: hall 24 C max and 55 percent RH max -> 0; lab 28 C max and
61 percent RH max -> 1; hall 33 C max and 48 percent RH max -> 2."""


class LocalScorer:
    def __init__(self, port, audit_path, timeout=4):
        self.url = f"http://127.0.0.1:{port}/completion"
        self.audit_path = audit_path
        self.timeout = timeout
        self.records = {}
        if audit_path.exists():
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                self.records[entry["cache_key"]] = entry

    def score(self, case_id, features):
        case = json.dumps(features, sort_keys=True, separators=(",", ":"))
        prompt = ("<|im_start|>system\n" + SYSTEM + "<|im_end|>\n"
                  "<|im_start|>user\n" + case + "<|im_end|>\n<|im_start|>assistant\n")
        cache_key = sha256((case_id + "\n" + prompt).encode()).hexdigest()
        if cache_key in self.records:
            return self.records[cache_key]
        request = Request(self.url, data=json.dumps({
            "prompt": prompt, "grammar": "root ::= [012]", "n_predict": 3,
            "temperature": 0, "seed": 1927, "cache_prompt": True,
        }).encode(), headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        value, status, error = None, "fallback", None
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content = json.load(response)["content"].strip()
            if re.fullmatch("[012]", content):
                value, status = int(content), "model"
            else:
                error = "invalid scalar"
        except (URLError, OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
            error = type(exc).__name__
        entry = {
            "cache_key": cache_key, "case_id": case_id, "features": features,
            "status": status, "value": value, "error": error,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        self.records[cache_key] = entry
        return entry
