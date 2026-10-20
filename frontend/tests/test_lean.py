"""Check with Lean: select lines -> confirm readings and the problem -> confirm the statement in words -> a card.

Run (dev server up on :5173, see tests/README.md):
    python tests/test_lean.py <screenshot_dir>          # Lean endpoints faked: the UI flow, fast and deterministic
    python tests/test_lean.py <screenshot_dir> --real   # the real backend, Lean and Claude (about a minute)
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
STATEMENT = {"statement": "(a b : ℤ) (ha : Even a) (hb : Even b) : Even (a + b)",
             "in_words": "For all integers $a$ and $b$, if $a$ and $b$ are even, then $a + b$ is even."}
VERDICTS = ["proved", "proved", "not proved", "proved"]


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
    errors, sent, events = [], {"n_statement": 0}, []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # A 503 from a test that fails the statement on purpose is expected; anything else is an error.
    page.on("console", lambda m: m.type == "error" and "503" not in m.text and errors.append(m.text))
    page.route("**/api/recognize", lambda r: r.fulfill(
        json={"latex": "", "confidence": None, "source": "none", "has_prose": False}))
    if not REAL:
        def statement(route):
            sent["statement"] = json.loads(route.request.post_data)
            sent["n_statement"] += 1
            if sent.get("fail_statement"):
                return route.fulfill(status=503, json={"detail": "no key for the Lean model (claude-sonnet-5-5)"})
            route.fulfill(json=STATEMENT)

        def lean_check(route):
            sent["check"] = json.loads(route.request.post_data)
            route.fulfill(json={"lines": [{"verdict": v, "message": f"msg {v}", "lean": []} for v in VERDICTS],
                                "complete": False, "message": "Look again at line 3.", "seconds": 9})
        page.route("**/api/lean/statement", statement)
        page.route("**/api/lean/check", lean_check)
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
    check(buttons == ["Check with Lean"], f"the only action is Check with Lean ({buttons})")
    page.click("text=Check with Lean")
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
    page.screenshot(path=f"{OUT}/lean_readings.png")

    if not REAL:  # the statement step can fail: the box says why and stays open
        sent["fail_statement"] = True
        page.click("text=Looks right, next")
        page.wait_for_selector(".chip-error")
        check("no key for the Lean model" in page.inner_text(".chip-error"), "a failed statement shows its reason")
        sent["fail_statement"] = False

    page.click("text=Looks right, next")
    page.wait_for_selector("text=Yes, check my proof", timeout=180_000)
    words = page.inner_text(".chip-words")
    check("even" in words, f"the statement is shown in words ({words})")
    check("Even" in page.inner_text(".chip-lean"), "and in Lean")
    if not REAL:
        check(sent["statement"] == {"problem": PROBLEM, "confirmed": True}, "statement request: the confirmed problem")
    readings = js(page, "return Object.values(editor.getDocumentSettings().meta.readings ?? {})"
                        ".map(r => r.confirmed)")
    check(readings and all(readings), f"every selected reading is now confirmed ({readings})")
    page.screenshot(path=f"{OUT}/lean_statement.png")

    # --- "Not what I meant" goes back to the readings, which are kept ---
    page.click("text=Not what I meant")
    check(page.locator(".chip .chip-read").count() == 5, "back to the five readings")
    if not REAL:
        check([e["event"] for e in events] == ["statement_rejected"], f"the rejection is logged ({events})")
    page.click("text=Looks right, next")
    page.wait_for_selector("text=Yes, check my proof", timeout=180_000)

    # --- check: one card next to the proof ---
    page.click("text=Yes, check my proof")
    page.wait_for_function("window.editor.getCurrentPageShapes().some(s => s.type === 'mellow-feedback')",
                           timeout=600_000)
    card = js(page, "return editor.getCurrentPageShapes().find(s => s.type === 'mellow-feedback').props")
    if not REAL:
        check(sent["check"] == {"statement": STATEMENT["statement"], "lines": PROOF, "confirmed": True},
              "check request: the statement and the four confirmed proof lines")
    msg = card["message"].split("\n")
    check(len(msg) == 5 and card["line"] == PROBLEM, f"card: a row per proof line plus a summary ({len(msg)} rows)")
    check(card["verdict"] == "lean_issues", f"the planted mistake keeps the proof from being complete ({card['verdict']})")
    check(msg[2].startswith("?") and msg[0].startswith("✓"), f"line 3 is the one flagged ({msg[2][:40]})")
    check(page.locator(".chip").count() == 0, "the box closes")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/lean_card.png")

    # --- after a ✗ or ?, one optional question; the check and the answer are logged ---
    page.wait_for_selector(".ask")
    page.click(".ask >> text=Fix it")
    check(page.locator(".ask").count() == 0, "the question goes away once answered")
    if not REAL:
        page.wait_for_timeout(300)
        names = [e["event"] for e in events]
        check(names == ["statement_rejected", "check_started", "check_result", "next_step"], f"events logged ({names})")
        check(events[2]["data"]["verdicts"] == {"proved": 3, "not proved": 1}, f"verdict counts ({events[2]['data']})")
        check(events[3]["data"] == {"answer": "Fix it"} and len({e["check_id"] for e in events}) == 1,
              "the answer belongs to the same check")

        # --- the same proof again: readings shown, the confirmed statement reused (no second wait) ---
        js(page, "editor.setCurrentTool('lasso-select')")
        stroke(page, [(100, 170), (290, 170), (290, 480), (100, 480), (100, 172)])
        page.wait_for_selector(".action-bar")
        page.click("text=Check with Lean")
        check(page.locator(".chip .chip-read").count() == 5, "the readings are shown again")
        page.click("text=Check again")
        page.wait_for_function("window.editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length == 2")
        check(sent["n_statement"] == 3, f"no new statement request ({sent['n_statement']})")

    # --- an old notebook's animated explanation still loads, as a plain card ---
    js(page, "editor.createShape({type: 'mellow-scene', x: 600, y: 900, props: {w: 300, h: 100, scene: '{}', "
             "line: 'old', message: 'an old explanation'}})")
    check(page.locator("text=Explanation (no longer animated)").count() == 1, "old explanation boards still render")

    check(not errors, f"no page errors {errors}")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
