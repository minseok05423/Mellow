"""P2 Read + Check and P3a actions, end to end: live readings, lasso -> Check / Hint / Answer / Explain ->
confirm chip -> SymPy or the reasoning model -> feedback card.

Needs the backend running so /check on algebra is real SymPy. The recognizer, the page labeller and the
reasoning model are faked at the network layer, so the test doesn't depend on vendor keys or handwriting.
    python tests/test_check.py <screenshot_dir>
"""
import json
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
fails = []


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
    page.mouse.move(*to_screen(page, *pts[0]))
    page.mouse.down()
    for px, py in pts[1:]:
        page.mouse.move(*to_screen(page, px, py), steps=4)
    page.mouse.up()


def lasso(page, x0, y0, x1, y1):
    js(page, "editor.setCurrentTool('lasso-select')")
    stroke(page, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + 2)])


# Fake recognizer: line 1 (3 strokes) reads "(a+b)^2", line 2 (2 strokes) "a^2+b^2", anything else unread.
READS = {3: "(a+b)^2", 2: "a^2+b^2", 0: r"\text{Show that } 2^n > n \text{ for every } n",  # 0 strokes: a pasted image
         6: r"C o n t i n u o u s . s h o w",  # how MyScript's math mode reads words
         4: r"\text{Let } f:[0,1]\to[0,1] \text{ be}", 5: r"\text{continuous. Show there is } c \text{ with } f(c)=c"}
recognize_calls = []


def fake_recognize(route):
    body = json.loads(route.request.post_data)
    recognize_calls.append(body)
    latex = READS.get(len(body["strokes"]), "")
    route.fulfill(json={"latex": latex, "confidence": 0.95 if latex else None,
                        "source": ("myscript" if len(body["strokes"]) == 6 else "mathpix") if latex else "none", "has_prose": False})


# Word lines go to the reasoning model; fake it here (algebra still goes to the real SymPy backend).
check_calls, assist_calls, structure_calls, presolve_calls = [], [], [], []
QUESTION = "Which theorem gets you from here to $f(c)=c$?"
JUDGEMENT = "Gap: you use the IVT on $g$ without saying why $g$ is continuous."
ANSWER = "1. Let $g(x) = f(x) - x$. ✓\n2. By the IVT, $g$ has a root $c$.\n✓ = algebra checked by SymPy."
EXPLANATION = "1. Let $g(x) = f(x) - x$.\n    Why: a root of $g$ is a fixed point of $f$."
# What the backend's scene builder makes of a checked proof: an equation that evolves in place, then a highlight.
SCENE = {"schema_version": 1, "steps": [
    {"id": "s1", "op": "write", "latex": "g(x)=f(x)-x", "pos": [0.05, 0.07], "say": "Let $g(x) = f(x) - x$."},
    {"id": "s2", "op": "write", "latex": "g(0)=f(0)-0", "pos": [0.05, 0.195], "say": "At $0$.\nWhy: plug in ✓ checked by SymPy"},
    {"id": "s3", "op": "transform", "from": "s2", "latex": "g(0)=f(0)", "say": "Simplify."},
    {"id": "s4", "op": "highlight", "target": "s3", "say": "So $g(0) \\ge 0$."}]}

# A compiled picture (scene v2): a triangle drawn, its right angle marked, then turned into a new place.
PICTURE = {"schema_version": 2,
           "figure": {"points": {"A": [0, 0], "B": [2, 0], "C": [0, 1], "P": [3, 0], "Q": [3, 2], "R": [2, 0]},
                      "view": [-0.4, -0.4, 3.4, 2.4], "box": [0.04, 0.04, 0.92, 0.92]},
           "steps": [
               {"id": "s1", "op": "draw", "shape": {"kind": "polygon", "points": ["A", "B", "C"], "label": "T"}, "say": "A triangle."},
               {"id": "s2", "op": "draw", "shape": {"kind": "angle", "points": ["B", "A", "C"]}, "say": "A right angle.\n✓ checked by SymPy"},
               {"id": "s3", "op": "move", "target": "s1", "to": ["P", "Q", "R"], "say": "Turn it a quarter turn."},
               {"id": "s4", "op": "highlight", "target": "s3", "say": "Same piece, new place.\n✓ true in this picture (checked by SymPy)"}]}


def card(verdict, message):
    return {"verdict": verdict, "reason": "fake", "message": message, "counterexample": {}, "values": {}, "compared_with": None}


def fake_reasoner(route):
    body = json.loads(route.request.post_data)
    check_calls.append(body)
    if "\\text" in body["current"] or "C o n t" in body["current"]:
        route.fulfill(json=card("ai_check", JUDGEMENT))
    else:
        route.continue_()


def fake_assist(route):
    body = json.loads(route.request.post_data)
    assist_calls.append(body)
    reply = card(body["action"], {"hint": QUESTION, "answer": ANSWER, "explain": EXPLANATION}[body["action"]])
    if body["action"] == "explain" and body["kind"] == "problem":
        reply["scene"] = PICTURE if body.get("style") == "picture" else SCENE
    route.fulfill(json=reply)


def fake_structure(route):
    """The statement's lines are a problem, letter-by-letter words a proof step, the rest algebra."""
    lines = json.loads(route.request.post_data)["lines"]
    structure_calls.append(lines)
    role = lambda l: "problem" if "Let" in l or "Show" in l else "proof_step" if "C o n t" in l else "algebra"
    route.fulfill(json={"lines": [{"role": role(l), "segment": 0} for l in lines], "segments": ["Problem: a fixed point"]})


def fake_presolve(route):
    presolve_calls.append(json.loads(route.request.post_data))
    route.fulfill(status=202, json={"queued": True})


def wait_until(page, cond, ms=8000):
    for _ in range(ms // 200):
        if cond():
            return True
        page.wait_for_timeout(200)
    return cond()


def act(page, action):
    """Click an action next to the lassoed lines and wait until the chip knows what the selection is."""
    page.click(f"[data-action={action}]")
    page.wait_for_selector("#chip-kind")


def confirm(page):
    page.evaluate("document.querySelector('.chip .primary').click()")  # the chip can open below the viewport


def n_cards(page, n):
    page.wait_for_function(f"editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length === {n}",
                           timeout=8000)
    return js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').at(-1).props")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/recognize", fake_recognize)
    page.route("**/api/check", fake_reasoner)
    page.route("**/api/assist", fake_assist)
    page.route("**/api/structure", fake_structure)
    page.route("**/api/presolve", fake_presolve)
    page.goto("http://localhost:5173/")
    page.wait_for_load_state("networkidle")
    page.click("text=+ New notebook")
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(300)

    # Line 1: three strokes. Line 2: two strokes, further down.
    stroke(page, [(120, 200), (140, 185), (160, 200)])
    stroke(page, [(175, 190), (195, 205)])
    stroke(page, [(210, 185), (230, 200)])
    stroke(page, [(120, 300), (150, 290), (170, 305)])
    stroke(page, [(190, 295), (220, 300)])

    page.wait_for_selector(".reading .katex", timeout=5000)
    page.wait_for_timeout(300)
    check(page.locator(".reading .katex").count() == 2, "live readings appear under both lines")
    on_top = page.evaluate("""[...document.querySelectorAll('.reading')].every(r => {
        r.style.pointerEvents = 'auto';
        const b = r.getBoundingClientRect();
        const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
        r.style.pointerEvents = '';
        return r.contains(hit);
    })""")
    check(on_top, "readings are visible on top of the paper")
    check(all(c["image"].startswith("data:image/png") for c in recognize_calls), "recognize gets the line image")
    check(sorted(len(c["strokes"]) for c in recognize_calls) == [2, 3], "strokes grouped into 2 lines (3 + 2)")
    page.screenshot(path=f"{OUT}/p2_readings.png")

    # With the select tool, a reading can be highlighted and copied (with the pen it stays out of the way).
    js(page, "editor.setCurrentTool('select')")
    rb = page.locator(".reading").first.bounding_box()
    page.mouse.move(rb["x"] + 1, rb["y"] + rb["height"] / 2)
    page.mouse.down()
    page.mouse.move(rb["x"] + rb["width"] - 1, rb["y"] + rb["height"] / 2, steps=6)
    page.mouse.up()
    check(page.evaluate("getSelection().toString()") != "", "readings can be highlighted with the select tool")
    page.evaluate("getSelection().removeAllRanges()")
    js(page, "editor.setCurrentTool('draw')")

    # Lasso line 2 -> Check -> chip shows both readings -> confirm.
    lasso(page, 100, 270, 250, 330)
    page.wait_for_selector("[data-action=check]")
    check(page.locator(".action-btn").all_inner_texts() == ["Check ✓", "Hint", "Answer", "Explain with AI"],
          "four actions next to the selection")
    page.screenshot(path=f"{OUT}/p3_actions.png")
    act(page, "check")
    check(page.locator(".chip .chip-read").count() == 2, "chip shows this line and the line above")
    check(page.locator("#chip-kind").input_value() == "steps", "algebra lines are labelled as steps")
    page.screenshot(path=f"{OUT}/p2_chip.png")
    page.click("text=Looks right, check it")
    page.wait_for_selector(".feedback-refuted", timeout=8000)
    fb = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').map(s => s.props)")
    check(len(fb) == 1 and fb[0]["verdict"] == "refuted", "refuted card created")
    check("a = 1, b = 1" in fb[0]["message"], f"card shows the counterexample ({fb[0]['message']})")
    confirmed = js(page, "return Object.values(editor.getDocumentSettings().meta.readings).filter(r => r.confirmed).length")
    check(confirmed == 2, "both readings marked confirmed")
    page.screenshot(path=f"{OUT}/p2_refuted.png")

    # Line 3 isn't read by the fake recognizer: the chip opens in edit mode and the student types it.
    js(page, "editor.setCurrentTool('draw')")
    stroke(page, [(120, 400), (160, 395), (200, 405)])
    page.wait_for_timeout(1500)
    lasso(page, 100, 375, 230, 425)
    act(page, "check")
    page.locator(".chip input").first.fill("= a^2 + b^2")
    check(page.locator(".chip-preview .katex").count() == 1, "typed LaTeX gets a live preview")
    page.click("text=Looks right, check it")
    page.wait_for_selector(".feedback-verified", timeout=8000)
    check(True, "typed continuation line verified")

    # Cards are placed in free space, not on top of each other or the ink.
    boxes = js(page, """return editor.getCurrentPageShapes().filter(s => s.type !== 'mellow-page')
        .map(s => { const b = editor.getShapePageBounds(s); return [s.type, b.minX, b.minY, b.maxX, b.maxY] })""")
    cards = [b for b in boxes if b[0] == "mellow-feedback"]
    others = [b for b in boxes if b is not None]
    overlap = any(c is not o and c[1] < o[3] and o[1] < c[3] and c[2] < o[4] and o[2] < c[4] for c in cards for o in others)
    check(not overlap, "feedback cards don't overlap ink or each other")
    page.screenshot(path=f"{OUT}/p2_verified.png")

    # Toggle readings off and on.
    page.click("text=Readings on")
    check(page.locator(".reading").count() == 0, "readings toggle hides them")
    page.click("text=Readings off")

    # Persistence: cards and confirmed readings survive a reload.
    page.wait_for_timeout(800)
    page.reload()
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(500)
    n = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length")
    check(n == 2, "feedback cards survive reload")
    check(page.locator(".reading-confirmed").count() == 3, "confirmed readings survive reload")

    # A problem written over two lines. Hint: the chip knows it's a problem; one card from the reasoning model.
    js(page, "editor.setCurrentTool('draw')")
    for i in range(4):
        stroke(page, [(120 + i * 40, 520), (145 + i * 40, 510)])
    for i in range(5):
        stroke(page, [(120 + i * 40, 590), (145 + i * 40, 580)])
    # After a pause the page is labelled and the problem starts solving in the background.
    check(wait_until(page, lambda: any("Show there is" in c["problem"] for c in presolve_calls)),
          f"the problem found on the page is solved in the background ({presolve_calls})")
    lasso(page, 100, 490, 340, 615)
    act(page, "hint")
    check(page.locator(".chip-row", has_text="I read your lines as").count() == 1
          and page.locator(".chip-row", has_text="Next line").count() == 1, "chip shows both lines of the statement")
    check(page.locator("#chip-kind").input_value() == "problem", "the labeller marks the statement as a problem")
    check(page.locator(".chip-row", has_text="The problem these steps").count() == 0, "a problem needs no problem row")
    check("Problem: a fixed point" in page.locator(".chip").inner_text(), "chip shows the segment's description")
    page.screenshot(path=f"{OUT}/p3_chip.png")
    confirm(page)
    q = n_cards(page, 3)
    check(q["verdict"] == "hint" and q["message"] == QUESTION, f"the problem gets one hint ({q})")
    check("Show there is" in q["line"] and "Let" in q["line"], "card holds both lines")
    sent = assist_calls[-1]
    check((sent["action"], sent["kind"], sent["problem"]) == ("hint", "problem", None)
          and "Show there is" in sent["selection"] and "Let" in sent["selection"], f"hint request ({sent})")
    check(sent["selection"] in [c["problem"] for c in presolve_calls], "the hint asks about the same text that was presolved")
    check(len(js(page, "return Object.keys(editor.getDocumentSettings().meta.structure)")) == 1, "labels kept with the notebook")

    # Copying: the card's Copy button, and highlighting card text with the mouse + Ctrl+C.
    card_el = page.locator(".feedback", has_text="Which theorem")
    card_el.locator(".feedback-copy").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    check(clip.startswith("Hint: ") and QUESTION in clip and "\\text{Let }" in clip, "Copy button copies text + LaTeX")
    page.evaluate("navigator.clipboard.writeText('')")
    msg = card_el.locator(".feedback-msg").bounding_box()
    before = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').at(-1).x")
    page.mouse.move(msg["x"] + 2, msg["y"] + 4)
    page.mouse.down()
    page.mouse.move(msg["x"] + msg["width"] - 2, msg["y"] + msg["height"] - 4, steps=8)
    page.mouse.up()
    page.keyboard.press("Control+c")
    page.wait_for_timeout(200)
    clip = page.evaluate("navigator.clipboard.readText()")
    after = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').at(-1).x")
    check("Which theorem" in clip and "f(c)=c" in clip, f"highlighted card text copies, math as LaTeX ({clip!r})")
    check(before == after, "highlighting text doesn't drag the card")
    page.screenshot(path=f"{OUT}/p3_hint.png")

    # Answer and Explain on the problem, each one labelled card.
    lasso(page, 100, 490, 340, 615)
    act(page, "answer")
    confirm(page)
    a = n_cards(page, 4)
    check(a["verdict"] == "answer" and "✓" in a["message"], "Answer gives the checked proof")
    check("Answer · AI" in page.locator(".feedback-answer").inner_text(), "answer card is labelled as the AI's")
    page.screenshot(path=f"{OUT}/p3_answer.png")

    # Explain on a problem: an animated board that plays the checked proof step by step.
    lasso(page, 100, 490, 340, 615)
    act(page, "explain")
    confirm(page)
    page.wait_for_selector(".scene")
    board = page.locator(".scene")
    shape = js(page, "return editor.getCurrentPageShapes().find(s => s.type === 'mellow-scene').props")
    check("Why:" in shape["message"] and json.loads(shape["scene"]) == SCENE, "Explain places a board with the scene and its text")
    # The board can sit below the fold, so its buttons are pressed via the DOM (the camera stays put for the
    # strokes drawn later in this test).
    play = lambda: page.evaluate("document.querySelector('.scene .scene-controls .primary').click()")
    idle = "!document.querySelector('.scene .scene-controls .primary').disabled"
    for _ in range(2):
        play()
        page.wait_for_function(idle, timeout=5000)
    check(board.locator(".scene-obj").count() == 2 and board.locator(".scene-count").inner_text() == "2 / 4",
          "each click plays one step")
    check("checked by SymPy" in board.locator(".scene-checked").inner_text(), "checked steps say so")
    play()
    page.wait_for_timeout(400)
    page.screenshot(path=f"{OUT}/p3_scene_transform.png")
    page.wait_for_function(idle, timeout=5000)
    check(board.locator(".scene-obj").count() == 2 and "f(0)" in board.locator(".scene-obj").last.inner_text(),
          "transform replaces the line in place")
    play()
    page.wait_for_function("document.querySelector('.scene .scene-controls .primary').textContent === 'Done'", timeout=5000)
    check(board.locator(".scene-lit").count() == 1, "the closing highlight lights the last line")
    page.screenshot(path=f"{OUT}/p3_scene_end.png")
    page.evaluate("[...document.querySelectorAll('.scene .scene-controls button')].find(b => b.textContent.includes('Replay')).click()")
    check(board.locator(".scene-obj").count() == 0 and board.locator(".scene-count").inner_text() == "0 / 4", "Replay starts over")
    js(page, "editor.deleteShapes(editor.getCurrentPageShapes().filter(s => s.type === 'mellow-scene'))")

    # Explain as a picture: the confirm box offers it; the board draws shapes, turns a piece, lights it up.
    lasso(page, 100, 490, 340, 615)
    act(page, "explain")
    check(page.locator("#chip-style").input_value() == "steps", "Explain offers step by step or a picture")
    page.select_option("#chip-style", "picture")
    confirm(page)
    page.wait_for_selector(".scene")
    check(assist_calls[-1].get("style") == "picture", "the request asks for a picture")
    for k in range(4):
        play()
        page.wait_for_function(f"{idle} || document.querySelector('.scene .scene-controls .primary').textContent === 'Done'", timeout=5000)
    paths = page.evaluate("[...document.querySelectorAll('.scene-figure path')].map(p => p.getAttribute('d'))")
    check(len(paths) == 2 and paths[0].count("L") == 2, f"the figure draws a triangle and an angle mark ({paths})")
    check(page.locator(".scene-label").count() == 1 and "true in this picture" in board.locator(".scene-checked").inner_text(),
          "labels and picture-only checks show")
    lit = page.evaluate("document.querySelector('.scene-figure path').style.stroke")
    check("247, 217, 76" in lit or "f7d94c" in lit, f"highlight turns the moved piece yellow ({lit})")
    # Out of the way of the lines written below in this test (a press on a board or card is for it, not the pen).
    js(page, "editor.deleteShapes(editor.getCurrentPageShapes().filter(s => s.type === 'mellow-scene'))")

    # Check on a problem: nothing to check, and no request.
    calls = len(check_calls)
    lasso(page, 100, 490, 340, 615)
    act(page, "check")
    confirm(page)
    c = n_cards(page, 5)
    check(c["verdict"] == "noted" and "nothing to check" in c["message"] and len(check_calls) == calls,
          "Check on a problem says there's nothing to check yet")

    # Three math lines in one lasso: one card, not three.
    lasso(page, 100, 170, 260, 425)
    act(page, "check")
    confirm(page)
    last = n_cards(page, 6)
    check(last["verdict"] == "refuted" and "Line 2:" in last["message"], f"one card for three lines ({last['message']})")

    # Words read letter by letter are a proof step: the AI judges them (never a SymPy refutation), with the
    # problem above found by its label.
    js(page, "editor.setCurrentTool('draw')")
    for i in range(6):
        stroke(page, [(120 + i * 30, 700), (140 + i * 30, 690)])
    page.wait_for_timeout(2500)
    lasso(page, 100, 670, 320, 725)
    act(page, "check")
    check("Show there is" in page.locator(".chip-row", has_text="The problem these steps").inner_text().replace("\xa0", " "),
          "the chip shows the problem found above")
    page.screenshot(path=f"{OUT}/p3_steps_chip.png")
    confirm(page)
    last = n_cards(page, 7)
    check(last["verdict"] == "ai_check" and last["message"] == JUDGEMENT,
          f"letter-by-letter words get the AI's judgement, never a refutation ({last['verdict']})")
    check("Show there is" in (check_calls[-1].get("problem") or ""), "the check request carries the problem")
    check(page.locator(".feedback-ai_check").inner_text().count("AI, not verified") == 1, "AI check is labelled")
    page.wait_for_timeout(800)
    page.reload()  # readings from before the fix get re-read when the notebook opens
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(2500)
    check(sum(len(c["strokes"]) == 6 for c in recognize_calls) == 2, "an old letter-by-letter reading is re-read once")

    # Hint on a proof step: the problem comes from the page's labels.
    lasso(page, 100, 670, 320, 725)
    act(page, "hint")
    confirm(page)
    n_cards(page, 8)
    sent = assist_calls[-1]
    check(sent["kind"] == "steps" and "Show there is" in (sent["problem"] or ""), f"hint on a step carries its problem ({sent})")
    page.screenshot(path=f"{OUT}/p3_step_hint.png")

    # A step on the next page still finds its problem on the page before.
    page.click("text=+ Page")
    page2 = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-page').sort((a, b) => a.y - b.y)[1].y")
    js(page, f"editor.centerOnPoint({{x: 400, y: {page2 + 250}}})")
    page.wait_for_timeout(300)
    js(page, "editor.setCurrentTool('draw')")
    stroke(page, [(120, page2 + 200), (150, page2 + 190), (170, page2 + 205)])
    stroke(page, [(190, page2 + 195), (220, page2 + 200)])
    page.wait_for_timeout(1500)
    lasso(page, 100, page2 + 170, 250, page2 + 230)
    act(page, "hint")
    check("Show there is" in page.locator(".chip-row", has_text="The problem these steps").inner_text().replace("\xa0", " "),
          "a step on page 2 finds the problem on page 1")
    confirm(page)
    n_cards(page, 9)
    check("Show there is" in (assist_calls[-1]["problem"] or ""), "and sends it with the hint")
    page.screenshot(path=f"{OUT}/p3_next_page.png")

    # A pasted image (a screenshot of a problem sheet) is read by the image reader and works like handwriting.
    js(page, f"""editor.createAssets([{{id: 'asset:sheet', typeName: 'asset', type: 'image', meta: {{}},
        props: {{name: 'sheet.png', w: 300, h: 80, mimeType: 'image/png', isAnimated: false,
                 src: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='}}}}]);
        editor.createShape({{type: 'image', x: 120, y: {page2 + 320}, props: {{assetId: 'asset:sheet', w: 300, h: 80}}}})""")
    check(wait_until(page, lambda: any(c["strokes"] == [] and c["image"].startswith("data:image/png") for c in recognize_calls)),
          "a pasted image is sent to the image reader, with no strokes")
    page.wait_for_timeout(500)
    js(page, "editor.setCurrentTool('select'); editor.select(editor.getCurrentPageShapes().find(s => s.type === 'image').id)")
    page.wait_for_selector("[data-action=hint]")
    act(page, "hint")
    check(page.locator("#chip-kind").input_value() == "problem", "the image's text is labelled as a problem")
    confirm(page)
    n_cards(page, 10)
    check("Show that" in assist_calls[-1]["selection"] and assist_calls[-1]["kind"] == "problem",
          f"Hint on an image sends what it says ({assist_calls[-1]['selection']})")
    page.screenshot(path=f"{OUT}/p3_image.png")

    check(not errors, f"no page errors {errors}")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
