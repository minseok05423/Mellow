"""Check my proof: select lines -> confirm readings and the problem -> confirm the proof's steps -> a card.

Run (dev server up on :5173, see tests/README.md):
    python tests/test_check.py <screenshot_dir>          # /graph and /check faked: the UI flow, fast and deterministic
    python tests/test_check.py <screenshot_dir> --real   # the real backend (a model key, or MELLOW_AGENT_QUEUE with
                                                         # agents answering: see backend/leaneval/agentq.py)
Handwriting is mouse squiggles, so readings are typed in the confirm box, as a student fixes a misread line.
"""
import json
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "."
REAL = "--real" in sys.argv
URL = "http://localhost:5173/"
fails = []

PROBLEM = r"\text{Show that the sum of two even integers is even.}"
PROOF = [r"\text{Since } a \text{ is even, } a = 2k \text{ for some integer } k.",
         r"\text{Since } b \text{ is even, } b = 2m \text{ for some integer } m.",
         r"\text{Then } a + b = 2k + 2m = 2(km).",
         r"km \text{ is an integer, so } a + b \text{ is even.}"]
GIVEN = [{"id": 0, "kind": "given", "line": None, "text": "$a$ is even", "premises": []},
         {"id": 1, "kind": "given", "line": None, "text": "$b$ is even", "premises": []}]
STEPS = [(0, "$a = 2k$ for some integer $k$", [0]), (1, "$b = 2m$ for some integer $m$", [1]),
         (2, "$a + b = 2k + 2m = 2(km)$", [2, 3]), (3, "$a + b$ is even", [4])]
GRAPH = {"goal": "the sum of two even integers is even", "goal_order": [], "concludes": 5,
         "nodes": GIVEN + [{"id": i + 2, "kind": "claim", "line": line, "text": t, "premises": p}
                           for i, (line, t, p) in enumerate(STEPS)],
         "variables": [{"name": n, "kind": k, "introduced_at": at, "depends_on": []}
                       for n, k, at in [("a", "given", None), ("b", "given", None), ("k", "obtained", 2),
                                        ("m", "obtained", 3)]]}
VERDICTS = [("ok", "proved", ""), ("ok", "proved", ""), ("wrong", "tested", "False as written for $k = 1, m = 1$."),
            ("ok", "judged", "")]


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def js(page, expr):
    return page.evaluate(f"(() => {{ const editor = window.editor; {expr} }})()")


def to_screen(page, x, y):
    p = js(page, f"return editor.pageToScreen({{x: {x}, y: {y}}})")
    return p["x"], p["y"]


def stroke(page, pts):
    x, y = to_screen(page, *pts[0])
    page.mouse.move(x, y)
    page.mouse.down()
    for px, py in pts[1:]:
        page.mouse.move(*to_screen(page, px, py), steps=4)
    page.mouse.up()


def write_line(page, y):
    """A short squiggle line at page height y: three strokes, like a few handwritten symbols."""
    for x in (120, 170, 220):
        stroke(page, [(x, y), (x + 15, y - 12), (x + 30, y), (x + 15, y + 12)])


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors, sent, events = [], {"n_graph": 0}, []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # A 503 from a test that fails the graph on purpose is expected; anything else is an error.
    page.on("console", lambda m: m.type == "error" and "503" not in m.text and errors.append(m.text))
    page.route("**/api/recognize", lambda r: r.fulfill(
        json={"latex": "", "confidence": None, "source": "none", "has_prose": False}))
    if not REAL:
        def graph(route):
            sent["graph"] = json.loads(route.request.post_data)
            sent["n_graph"] += 1
            if sent.get("fail_graph"):
                return route.fulfill(status=503, json={"detail": "no key for the checking model (claude-sonnet-5-5)"})
            route.fulfill(json=GRAPH)

        def graph_check(route):
            sent["check"] = json.loads(route.request.post_data)
            route.fulfill(json={"nodes": [{"node": i + 2, "status": st, "tier": t, "message": m, "detail": ""}
                                          for i, (st, t, m) in enumerate(VERDICTS)],
                                "complete": False, "message": "Look again at step 4.", "seconds": 9})
        page.route("**/api/graph", graph)
        page.route("**/api/check", graph_check)
        page.route("**/api/event", lambda r: (events.append(json.loads(r.request.post_data)), r.fulfill(status=204)))

    page.goto(URL)
    page.click("text=+ New notebook")
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(500)

    # --- write a problem line and four proof lines, lasso them all ---
    for k in range(5):
        write_line(page, 200 + 60 * k)
    js(page, "editor.setCurrentTool('lasso-select')")
    stroke(page, [(100, 170), (290, 170), (290, 480), (100, 480), (100, 172)])
    page.wait_for_selector(".action-bar")
    buttons = page.locator(".action-bar button").all_inner_texts()
    check(buttons == ["Check my proof"], f"the only action is Check my proof ({buttons})")
    page.click("text=Check my proof")
    chip_x = page.locator(".chip").bounding_box()["x"]
    sel_right = js(page, "return editor.getSelectionScreenBounds().maxX")
    check(chip_x >= sel_right, f"the box sits beside the ink, not on it ({chip_x} >= {sel_right})")

    # --- confirm box: 5 rows, empty readings open for typing, the first line starts as the problem ---
    inputs = page.locator(".chip .chip-edit input")
    check(inputs.count() == 5, f"one row per line ({inputs.count()})")
    roles = page.locator(".chip select").evaluate_all("els => els.map(e => e.value)")
    check(roles == ["problem", "proof", "proof", "proof", "proof"], f"first line is the problem ({roles})")
    check(page.locator("text=Looks right, next").is_disabled(), "can't go on before the lines are read")
    for i, text in enumerate([PROBLEM, *PROOF]):
        inputs.nth(i).fill(text)
    page.screenshot(path=f"{OUT}/check_readings.png")

    if not REAL:  # the graph step can fail: the box says why and stays open
        sent["fail_graph"] = True
        page.click("text=Looks right, next")
        page.wait_for_selector(".chip-error")
        check("no key for the checking model" in page.inner_text(".chip-error"), "a failed graph shows its reason")
        sent["fail_graph"] = False

    page.click("text=Looks right, next")
    page.wait_for_selector("text=Yes, check my proof", timeout=180_000)
    steps = page.locator(".chip .graph-node").all_inner_texts()
    check(len(steps) >= 5 and any("2k" in t for t in steps), f"the proof's steps are shown to confirm ({steps})")
    if not REAL:
        check(sent["graph"] == {"problem": PROBLEM, "lines": PROOF, "confirmed": True},
              "graph request: the confirmed problem and proof lines")
        check(steps[4].lower().startswith("[4] claim") and "from [2], [3]" in steps[4], f"a step shows its premises ({steps[4]})")
    readings = js(page, "return Object.values(editor.getDocumentSettings().meta.readings ?? {})"
                        ".map(r => r.confirmed)")
    check(readings and all(readings), f"every selected reading is now confirmed ({readings})")
    page.screenshot(path=f"{OUT}/check_graph.png")

    # --- "Not what I meant" goes back to the readings, which are kept ---
    page.click("text=Not what I meant")
    check(page.locator(".chip .chip-read").count() == 5, "back to the five readings")
    if not REAL:
        check([e["event"] for e in events] == ["graph_rejected"], f"the rejection is logged ({events})")
    page.click("text=Looks right, next")
    page.wait_for_selector("text=Yes, check my proof", timeout=180_000)

    # --- check: one card next to the proof ---
    page.click("text=Yes, check my proof")
    page.wait_for_function("window.editor.getCurrentPageShapes().some(s => s.type === 'mellow-feedback')",
                           timeout=600_000)
    card = js(page, "return editor.getCurrentPageShapes().find(s => s.type === 'mellow-feedback').props")
    if not REAL:
        check(sent["check"] == {"graph": GRAPH, "confirmed": True}, "check request: the confirmed graph")
    msg = card["message"].split("\n")
    check(len(msg) >= 5 and card["line"] == PROBLEM, f"card: a row per step plus a summary ({len(msg)} rows)")
    check(card["verdict"] == "graph_issues", f"the planted mistake keeps the proof from being complete ({card['verdict']})")
    flagged = [r for r in msg[:-1] if not r.startswith("✓")]
    check(any("2(km)" in r for r in flagged), f"the 2(km) step is flagged ({flagged})")
    check(all(" · " in r for r in msg[:-1]), "every step shows its tier")
    if not REAL:
        check(msg[2].startswith("✗ [4]") and "Tested" in msg[2] and msg[0].startswith("✓ [2]") and "Proved by Lean" in msg[0],
              f"rows: verdict, node id, tier ({msg[2][:60]})")
    check(page.locator(".chip").count() == 0, "the box closes")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/check_card.png")

    # --- after a ✗ or ?, one optional question; the check and the answer are logged ---
    page.wait_for_selector(".ask")
    page.click(".ask >> text=Fix it")
    check(page.locator(".ask").count() == 0, "the question goes away once answered")
    if not REAL:
        page.wait_for_timeout(300)
        names = [e["event"] for e in events]
        check(names == ["graph_rejected", "check_started", "check_result", "next_step"], f"events logged ({names})")
        check(events[2]["data"]["verdicts"] == {"ok proved": 2, "wrong tested": 1, "ok judged": 1},
              f"verdict counts ({events[2]['data']})")
        check(events[3]["data"] == {"answer": "Fix it"} and len({e["check_id"] for e in events}) == 1,
              "the answer belongs to the same check")

        # --- the same proof again: readings shown, the confirmed graph reused (no second wait) ---
        js(page, "editor.setCurrentTool('lasso-select')")
        stroke(page, [(100, 170), (290, 170), (290, 480), (100, 480), (100, 172)])
        page.wait_for_selector(".action-bar")
        page.click("text=Check my proof")
        check(page.locator(".chip .chip-read").count() == 5, "the readings are shown again")
        page.click("text=Check again")
        page.wait_for_function("window.editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length == 2")
        check(sent["n_graph"] == 3, f"no new graph request ({sent['n_graph']})")

    # --- an old notebook's animated explanation still loads, as a plain card ---
    js(page, "editor.createShape({type: 'mellow-scene', x: 600, y: 900, props: {w: 300, h: 100, scene: '{}', "
             "line: 'old', message: 'an old explanation'}})")
    check(page.locator("text=Explanation (no longer animated)").count() == 1, "old explanation boards still render")

    check(not errors, f"no page errors {errors}")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
