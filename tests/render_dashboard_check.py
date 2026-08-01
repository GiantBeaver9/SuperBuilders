#!/usr/bin/env python3
"""Headless render check for the POV-1 study dashboard.

Writes a representative dashboard_data.json fixture into addon/ui/web, serves that
directory on a local port, loads dashboard.html in the preinstalled Chromium via
Playwright, asserts there are no console errors and that the key elements exist
(crossover SVG, concept-table rows, abstain block), and screenshots to
sim/dashboard_preview.png.

Run:  python3 tests/render_dashboard_check.py
Requires the preinstalled Chromium (PLAYWRIGHT_BROWSERS_PATH); does NOT install.
"""
import json
import os
import socket
import sys
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
WEB_DIR = REPO / "addon" / "ui" / "web"
SIM_DIR = REPO / "sim"
SHOT = SIM_DIR / "dashboard_preview.png"
FIXTURE = WEB_DIR / "dashboard_data.json"


def build_fixture() -> dict:
    """A representative payload: an abstained concept, both crossover buckets
    with the predicted sign flip (negative at 1-4, positive at 5+), and a
    lockstep=false latency."""
    concepts = [
        # Scored, retired via the novel gate (Arm A)
        {"concept_id": 1, "code": "CARD-101", "name": "Preload vs. afterload",
         "arm": "gate", "weight": 0.9, "novel_attempts": 14, "has_score": True,
         "performance": 0.78, "coverage_pct": 100.0, "card_mastery": 0.91,
         "retired": True, "retired_trigger": "novel_gate"},
        # Scored, not retired
        {"concept_id": 2, "code": "CARD-102", "name": "Frank–Starling mechanism",
         "arm": "gate", "weight": 0.7, "novel_attempts": 11, "has_score": True,
         "performance": 0.61, "coverage_pct": 100.0, "card_mastery": 0.74,
         "retired": False, "retired_trigger": None},
        # Abstained — below the 8-attempt line, coverage only
        {"concept_id": 3, "code": "RENAL-210", "name": "Countercurrent multiplier",
         "arm": "nogate", "weight": 1.0, "novel_attempts": 4, "has_score": False,
         "performance": None, "coverage_pct": 50.0, "card_mastery": 0.66,
         "retired": False, "retired_trigger": None},
        # No-gate, retired on card mastery alone
        {"concept_id": 4, "code": "RENAL-211", "name": "RAAS cascade",
         "arm": "nogate", "weight": 0.8, "novel_attempts": 12, "has_score": True,
         "performance": 0.69, "coverage_pct": 100.0, "card_mastery": 0.95,
         "retired": True, "retired_trigger": "card_mastery"},
        # Vanilla control, missing card_mastery to exercise the null path
        {"concept_id": 5, "code": "PULM-330", "name": "V/Q mismatch",
         "arm": "vanilla", "weight": 0.6, "novel_attempts": 9, "has_score": True,
         "performance": 0.55, "coverage_pct": 100.0, "card_mastery": None,
         "retired": False, "retired_trigger": None},
        # Vanilla, abstained with 0 attempts (0% coverage edge)
        {"concept_id": 6, "code": "PULM-331", "name": "Hypoxic vasoconstriction",
         "arm": "vanilla", "weight": 0.5, "novel_attempts": 0, "has_score": False,
         "performance": None, "coverage_pct": 0.0, "card_mastery": 0.42,
         "retired": False, "retired_trigger": None},
    ]
    return {
        "generated_ms": int(time.time() * 1000),
        "concepts": concepts,
        "abstain": {
            "threshold": 8, "scored": 4, "abstained": 2,
            "below_line_mean_acc": 61.5, "above_line_mean_acc": 64.2,
            "diff_pp": 2.7,
        },
        "arms": {
            "gate": {"concepts": 2, "retired": 1},
            "nogate": {"concepts": 2, "retired": 1},
            "vanilla": {"concepts": 2, "retired": 0},
        },
        "endpoints": {
            "crossover": [
                {"bucket": "1-4", "n_gate": 210, "n_nogate": 198,
                 "acc_gate_pct": 58.0, "acc_nogate_pct": 67.0, "diff_pp": -9.0},
                {"bucket": "5+", "n_gate": 160, "n_nogate": 171,
                 "acc_gate_pct": 74.0, "acc_nogate_pct": 63.0, "diff_pp": 11.0},
            ],
            "terminal": {"acc_gate_pct": 72.0, "acc_nogate_pct": 65.0,
                         "diff_pp": 7.0, "n_gate": 40, "n_nogate": 41},
            "throughput": {"gate_retired": 11, "nogate_retired": 16,
                           "pct_diff_A_vs_B": -31.3},
            "latency": {"r": 0.42, "lockstep": False},
        },
    }


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build_fixture(), indent=2), encoding="utf-8")

    port = free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    httpd = HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}/dashboard.html"
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            errs = []
            for scheme in ("light", "dark"):
                page = browser.new_page(color_scheme=scheme)
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                page.on("requestfailed", lambda r: failed.append(r.url + " :: " + str(r.failure)))
                page.goto(url, wait_until="networkidle")
                page.wait_for_selector("body[data-ready='1']", timeout=5000)

                # --- assertions -------------------------------------------------
                assert page.query_selector("#crossover svg") is not None, "crossover SVG missing"
                assert page.locator("#crossover svg rect").count() >= 4, "expected >=4 crossover bars"
                rows = page.locator("#concept-tbody tr").count()
                assert rows == 6, f"expected 6 concept rows, got {rows}"
                assert page.locator("#concept-tbody tr.row-abstain").count() == 2, "expected 2 abstained rows"
                assert page.locator("#abstain .abstain__cell").count() >= 4, "abstain block missing cells"
                # abstain text must appear literally
                assert "ABSTAIN — coverage" in page.content(), "ABSTAIN coverage text missing"
                # error banner must stay hidden
                assert page.query_selector("#error-banner[hidden]") is not None, "error banner is visible"
                # lockstep=false -> dissociated (green) badge present, not the dead one
                assert page.locator(".badge--good").count() == 1, "expected dissociated badge"
                assert page.locator(".badge--bad").count() == 0, "unexpected lockstep-dead badge"

                if scheme == "light":
                    page.screenshot(path=str(SHOT), full_page=True)
                page.close()

            browser.close()
    finally:
        httpd.shutdown()

    problems = []
    if console_errors:
        problems.append("console errors: " + " | ".join(console_errors))
    if page_errors:
        problems.append("page errors: " + " | ".join(page_errors))
    if failed:
        problems.append("failed requests: " + " | ".join(failed))

    if problems:
        print("FAIL")
        for pr in problems:
            print("  - " + pr)
        return 1

    print("PASS — no console errors; key elements present.")
    print(f"screenshot: {SHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
