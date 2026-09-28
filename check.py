"""Endpoint check script for LastMile Alert.

Tests every API endpoint against a running server and prints PASS/FAIL
for each test. Uses only the Python standard library (no extra installs).

Usage:  python check.py [base_url]      (default http://127.0.0.1:5000)
"""

import json
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
RESULTS = []


def call(method, path, body=None):
    """Make one HTTP call and return (status_code, parsed_json_or_text)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def check(name, fn):
    """Run one test, recording PASS or FAIL with the reason."""
    try:
        detail = fn()
        RESULTS.append((name, True, detail))
        print(f"PASS  {name}  ({detail})")
    except AssertionError as exc:
        RESULTS.append((name, False, str(exc)))
        print(f"FAIL  {name}  ({exc})")
    except Exception as exc:  # noqa: BLE001 - report unexpected errors as FAIL
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
        print(f"FAIL  {name}  ({type(exc).__name__}: {exc})")


def main():
    print(f"Checking LastMile Alert at {BASE}\n")

    # 0. Start from a clean, known demo state.
    def t_reset():
        code, body = call("POST", "/api/reset")
        assert code == 200 and body.get("ok"), f"HTTP {code}: {body}"
        return "database wiped + reseeded"
    check("demo reset", t_reset)

    # 1. Health endpoint.
    def t_health():
        code, body = call("GET", "/api/health")
        assert code == 200, f"HTTP {code}"
        for key in ("status", "db", "alerts_count", "memory_mb", "port"):
            assert key in body, f"missing key '{key}'"
        assert body["status"] == "ok" and body["db"] == "connected"
        return f"alerts={body['alerts_count']} port={body['port']}"
    check("GET /api/health", t_health)

    # 2. Sample alerts for the intake page.
    def t_samples():
        code, body = call("GET", "/api/sample-alerts")
        assert code == 200 and len(body) >= 2, f"HTTP {code}: {body}"
        return f"{len(body)} samples"
    check("GET /api/sample-alerts", t_samples)

    # 3. Alert creation (uses the Assam sample official text).
    state = {}
    def t_create():
        code, samples = call("GET", "/api/sample-alerts")
        sample = samples[0]
        code, body = call("POST", "/api/alerts",
                          {"official_text": sample["official"] + " ",
                           "state": sample["state"],
                           "disaster_type": sample["disaster_type"]})
        assert code == 201, f"HTTP {code}: {body}"
        assert body["severity"] and body["affected_area"] and body["required_action"]
        assert "FLOOD ALERT" in body["simplified_text"]
        state["id"] = body["id"]
        return f"id={body['id']} severity={body['severity']}"
    check("POST /api/alerts", t_create)

    # 4. Validation: empty alert must be rejected, no traceback.
    def t_empty():
        code, body = call("POST", "/api/alerts",
                          {"official_text": "   ", "state": "Assam"})
        assert code == 400 and "error" in body, f"HTTP {code}: {body}"
        return "empty rejected with friendly error"
    check("POST /api/alerts (empty -> 400)", t_empty)

    # 5. Validation: over-long alert must be rejected.
    def t_long():
        code, body = call("POST", "/api/alerts",
                          {"official_text": "x" * 2001, "state": "Assam"})
        assert code == 400 and "error" in body, f"HTTP {code}: {body}"
        return "2001 chars rejected"
    check("POST /api/alerts (>2000 -> 400)", t_long)

    # 6. Validation: invalid state must be rejected.
    def t_state():
        code, body = call("POST", "/api/alerts",
                          {"official_text": "Flood warning", "state": "Narnia"})
        assert code == 400 and "error" in body, f"HTTP {code}: {body}"
        return "bad state rejected"
    check("POST /api/alerts (bad state -> 400)", t_state)

    # 7. List alerts.
    def t_list():
        code, body = call("GET", "/api/alerts")
        assert code == 200 and len(body) >= 3, f"HTTP {code}: {len(body) if isinstance(body, list) else body}"
        return f"{len(body)} alerts"
    check("GET /api/alerts", t_list)

    # 8. Get complete alert (fields + translations + visual).
    def t_get():
        code, body = call("GET", f"/api/alerts/{state['id']}")
        assert code == 200, f"HTTP {code}"
        for key in ("official_text", "simplified_text", "translations",
                    "visual", "affected_area"):
            assert key in body, f"missing '{key}'"
        assert "as" in body["translations"], "Assamese translation missing"
        return f"langs={list(body['translations'])}"
    check("GET /api/alerts/<id>", t_get)

    # 9. SMS delivery simulation.
    def t_sms():
        code, body = call("POST",
                          f"/api/alerts/{state['id']}/simulate-delivery",
                          {"lang": "as"})
        assert code == 200, f"HTTP {code}: {body}"
        sms = body["sms"]
        assert sms["chunk_count"] >= 1
        assert all(c["chars"] <= 160 for c in sms["chunks"]), "chunk > 160 chars!"
        assert all(c["final_status"] == "DELIVERED" for c in sms["chunks"])
        assert any(c["retried"] for c in sms["chunks"]), "retry path not exercised"
        assert "ussd" in body and body["ussd"]["screens"]
        return f"{sms['chunk_count']} chunks, retry shown"
    check("POST /api/alerts/<id>/simulate-delivery", t_sms)

    # 10. Relay simulation.
    def t_relay():
        code, body = call("POST", f"/api/alerts/{state['id']}/simulate-relay")
        assert code == 200, f"HTTP {code}: {body}"
        assert body["volunteer_count"] == 5
        assert len(body["hops"]) == 7  # source + 5 volunteers + community
        assert body["all_meanings_preserved"]
        assert all(h["integrity"]["match"] for h in body["hops"])
        return "5 volunteers, all integrity checks pass"
    check("POST /api/alerts/<id>/simulate-relay", t_relay)

    # 11. Tracker starts empty (registered citizens only); register one.
    def t_tracker():
        code, body = call("GET", f"/api/alerts/{state['id']}/tracker")
        assert code == 200, f"HTTP {code}"
        assert body["stats"]["total"] == 0, \
            f"expected 0 seeded recipients, got {body['stats']['total']}"
        code, reg = call("POST", "/api/register",
                         {"name": "Tracker Citizen", "village": "Dhemaji",
                          "state": "Assam", "language": "Assamese",
                          "channel": "SMS"})
        assert code == 200 and reg.get("ok"), f"HTTP {code}: {reg}"
        code, body = call("GET", f"/api/alerts/{state['id']}/tracker")
        assert code == 200, f"HTTP {code}"
        assert body["stats"]["total"] >= 1, "registered citizen missing from tracker"
        for key in ("delivered", "read", "acknowledged", "failed", "no_response"):
            assert key in body["stats"], f"missing stat '{key}'"
        state["recipient"] = reg["recipient"]["id"]
        return f"0 seeded; registered citizen #{state['recipient']} tracked"
    check("GET /api/alerts/<id>/tracker", t_tracker)

    # 12. Acknowledge.
    def t_ack():
        code, body = call("POST", f"/api/alerts/{state['id']}/acknowledge",
                          {"recipient_id": state["recipient"]})
        assert code == 200 and body.get("ok"), f"HTTP {code}: {body}"
        assert body["tracker"]["stats"]["acknowledged"] >= 1
        return f"recipient {state['recipient']} acknowledged"
    check("POST /api/alerts/<id>/acknowledge", t_ack)

    # 13. Diagnostics buffer.
    def t_diag():
        code, body = call("GET", "/api/diagnostics")
        assert code == 200 and len(body) > 0, f"HTTP {code}"
        assert all("ts" in e and "category" in e for e in body)
        return f"{len(body)} events"
    check("GET /api/diagnostics", t_diag)

    # 14. HTML pages render.
    def t_pages():
        for path in ["/", "/new", "/debug", "/register", "/u",
                     f"/alert/{state['id']}",
                     f"/alert/{state['id']}/delivery",
                     f"/alert/{state['id']}/tracker"]:
            code, _ = call("GET", path)
            assert code == 200, f"{path} -> HTTP {code}"
        return "8 pages OK"
    check("page routes render", t_pages)

    # 15. One Message composer (bandwidth-aware single message per recipient).
    def t_one_message():
        rid = state["recipient"]
        code, body = call("GET", f"/api/alerts/{state['id']}/one-message?recipient_id={rid}")
        assert code == 200, f"HTTP {code}"
        m = body["one_message"]
        assert m["message"], "empty message"
        assert m["segments"] >= 1, "no SMS segments computed"
        assert m["chars"] <= m["chars_per_segment"] * 2, "exceeds 2-segment budget"
        assert m["bytes"] >= m["chars"], "byte count inconsistent"
        assert body["recipient"]["id"] == rid, "wrong recipient"
        assert len(body["recipients"]) >= 1, "recipient list missing"
        return f"{m['chars']} chars, {m['segments']} SMS ({m['encoding']}) in {m['language']}"
    check("GET /api/alerts/<id>/one-message", t_one_message)

    # 16. Recipient-side read receipt.
    def t_read():
        code, body = call("POST", f"/api/alerts/{state['id']}/read",
                          {"recipient_id": state["recipient"]})
        assert code == 200 and body.get("ok"), f"HTTP {code}: {body}"
        rec = next(r for r in body["tracker"]["recipients"]
                   if r["id"] == state["recipient"])
        assert rec["read_status"] == "Read", f"read_status={rec['read_status']}"
        return f"recipient {state['recipient']} marked read"
    check("POST /api/alerts/<id>/read", t_read)

    # 17. Citizen registration stores details; user alerts are per-citizen.
    def t_register():
        code, body = call("POST", "/api/register",
                          {"name": "Test Citizen", "village": "Testpur",
                           "state": "Assam", "language": "Assamese",
                           "channel": "SMS"})
        assert code == 200 and body.get("ok"), f"HTTP {code}: {body}"
        new_id = body["recipient"]["id"]
        assert body["recipient"]["name"] == "Test Citizen"
        assert body["recipient"]["language"] == "Assamese"
        code, user = call("GET", f"/api/users/{new_id}")
        assert code == 200 and user["state"] == "Assam", f"HTTP {code}"
        code, ua = call("GET", f"/api/users/{new_id}/alerts")
        assert code == 200 and len(ua["alerts"]) >= 1, f"HTTP {code}"
        first = ua["alerts"][0]
        assert first["in_my_state"] is True, "own-state alert not first"
        assert first["message"], "no citizen message"
        assert first["language"] == "Assamese", f"lang={first['language']}"
        # invalid registration rejected
        code, bad = call("POST", "/api/register", {"name": "", "state": "Assam"})
        assert code == 400, f"empty name accepted (HTTP {code})"
        code, bad = call("POST", "/api/register",
                         {"name": "X", "state": "Assam", "language": "Klingon"})
        assert code == 400, f"bad language accepted (HTTP {code})"
        return f"user #{new_id} stored; {len(ua['alerts'])} alerts in Assamese"
    check("POST /api/register + GET /api/users/<id>/alerts", t_register)

    # 18. Organiser message preview per language.
    def t_messages():
        code, body = call("GET", f"/api/alerts/{state['id']}/messages")
        assert code == 200, f"HTTP {code}"
        msgs = body["messages"]
        assert len(msgs) >= 2, "expected 2+ languages"
        for m in msgs:
            assert m["message"], f"empty message for {m['language']}"
            assert m["segments"] >= 1
        langs = ", ".join(m["language"] for m in msgs)
        return f"{len(msgs)} languages: {langs}"
    check("GET /api/alerts/<id>/messages", t_messages)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed.")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
