"""Translate an operator note into a bounded telemetry handling profile."""

from dataclasses import dataclass
import json
import re
import time
from urllib.request import Request, urlopen


DURATIONS = (2, 4, 6, 8, 10, 12)
NUMBER_WORDS = dict(zip(("two", "four", "six", "eight", "ten", "twelve"), DURATIONS))
TIME = re.compile(r"\b(2|4|6|8|10|12|two|four|six|eight|ten|twelve)\s*(?:seconds?|secs?|s)\b")
RAW_TERMS = re.compile(r"\b(raw|individual|unaggregated|sample-by-sample|each reading|every reading|each measurement)\b")
SUMMARY_TERMS = re.compile(r"\b(summary|summaries|aggregates?|averages?|rollups?)\b")

SYSTEM = """Convert a building operator note into exactly one JSON object with fields zone, action, seconds.
Zones are lab and hall. Action raw means keep individual telemetry readings for review.
Action summary means aggregate readings. Use the requested duration in seconds (2, 4, 6, 8, 10, 12).
Only use an explicit, single zone and supported duration. A clear negative of one action with an
explicit request for the other is permitted. If there is no actionable request, or the scope, duration
or action is missing or ambiguous, output {"zone":"none","action":"none","seconds":0}.
Examples:
"Keep unaggregated lab readings for four seconds." -> {"zone":"lab","action":"raw","seconds":4}
"For eight seconds, send a hall summary only." -> {"zone":"hall","action":"summary","seconds":8}
"Hall ventilation just restarted." -> {"zone":"none","action":"none","seconds":0}
"""


@dataclass(frozen=True)
class Profile:
    zone: str
    action: str
    duration_s: int

    def __post_init__(self):
        if (self.zone not in ("lab", "hall") or self.action not in ("raw", "summary")
                or type(self.duration_s) is not int or self.duration_s not in DURATIONS):
            raise ValueError("Invalid operator profile")


def parse_profile(result):
    if not isinstance(result, dict) or set(result) != {"zone", "action", "seconds"}:
        raise ValueError("Expected zone, action, and seconds")
    zone, action, seconds = (result[key] for key in ("zone", "action", "seconds"))
    if zone == action == "none" and type(seconds) is int and seconds == 0:
        return None
    return Profile(zone, action, seconds)


def keyword_profile(note):
    note = note.lower()
    zones = set(re.findall(r"\b(?:lab|hall)\b", note))
    durations = set(TIME.findall(note))
    raw, summary = bool(RAW_TERMS.search(note)), bool(SUMMARY_TERMS.search(note))
    if len(zones) != 1 or len(durations) != 1 or raw == summary:
        return None
    duration = durations.pop()
    seconds = NUMBER_WORDS[duration] if duration in NUMBER_WORDS else int(duration)
    return Profile(zones.pop(), "raw" if raw else "summary", seconds)


def grammar():
    def literal(text):
        return json.dumps(text)

    def profile(zones, actions, seconds):
        enum = lambda options: "(" + " | ".join(literal(json.dumps(option)) for option in options) + ")"
        return " ".join((
            literal('{"zone":'), enum(zones), literal(',"action":'), enum(actions),
            literal(',"seconds":'), enum(seconds), literal("}"),
        ))

    return "root ::= " + profile(("lab", "hall"), ("raw", "summary"), DURATIONS) + " | " + profile(
        ("none",), ("none",), (0,),
    )


def slm_profile(note, port):
    prompt = ("<|im_start|>system\n" + SYSTEM + "<|im_end|>\n"
              "<|im_start|>user\n" + note + "<|im_end|>\n<|im_start|>assistant\n")
    request = Request(f"http://127.0.0.1:{port}/completion", data=json.dumps({
        "prompt": prompt, "grammar": grammar(), "n_predict": 64, "temperature": 0,
        "seed": 1927, "cache_prompt": True,
    }).encode(), headers={"Content-Type": "application/json"})
    started = time.monotonic_ns()
    content = error = profile = None
    try:
        with urlopen(request, timeout=8) as response:
            content = json.load(response)["content"]
        profile = parse_profile(json.loads(content))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        error = type(exc).__name__
    return profile, {"latency_ms": round((time.monotonic_ns() - started) / 1e6, 3),
                     "response": content, "error": error}
