"""P2 Read + Check, end to end: live readings, lasso -> Check -> confirm chip -> SymPy -> feedback card.

Needs the backend running with no recognizer (RECOGNIZER=none) so /check is real SymPy; recognizer
answers are faked at the network layer so the test doesn't depend on vendor keys or handwriting quality.
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
READS = {3: "(a+b)^2", 2: "a^2+b^2",
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
check_calls = []
QUESTION = "Which theorem gets you from here to $f(c)=c$?"
ANSWER = "This holds once you note $g$ is continuous; then the IVT gives the root."


def fake_reasoner(route):
    body = json.loads(route.request.post_data)
    check_calls.append(body)
    if "\\text" in body["current"] or "C o n t" in body["current"]:
        answer = body.get("mode") == "answer"
        route.fulfill(json={"verdict": "answer" if answer else "unverified", "reason": "proof_steps",
                            "message": ANSWER if answer else QUESTION,
                            "counterexample": {}, "values": {}, "compared_with": None})
    else:
        route.continue_()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/recognize", fake_recognize)
    page.route("**/api/check", fake_reasoner)
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
    page.wait_for_selector(".check-btn")
    page.click(".check-btn")
    page.wait_for_selector(".chip")
    check(page.locator(".chip .chip-read").count() == 2, "chip shows this line and the line above")
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
    page.click(".check-btn")
    page.wait_for_selector(".chip input")
    page.fill(".chip input", "= a^2 + b^2")
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

    # A statement written over two lines: one lasso, both lines in the chip, one question from the reasoning model.
    js(page, "editor.setCurrentTool('draw')")
    for i in range(4):
        stroke(page, [(120 + i * 40, 520), (145 + i * 40, 510)])
    for i in range(5):
        stroke(page, [(120 + i * 40, 590), (145 + i * 40, 580)])
    page.wait_for_timeout(1500)
    lasso(page, 100, 490, 340, 615)
    page.click(".check-btn")
    page.wait_for_selector(".chip")
    check(page.locator(".chip-row", has_text="I read your lines as").count() == 1
          and page.locator(".chip-row", has_text="Next line").count() == 1, "chip shows both lines of the statement")
    page.click("text=Looks right, check it")
    page.wait_for_function("editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length === 3",
                           timeout=8000)
    fb = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').map(s => s.props)")
    q = fb[-1]
    check(q["verdict"] == "unverified" and q["message"] == QUESTION, f"the statement gets one question ({q})")
    check("Show there is" in q["line"] and "Let" in q["line"], "card holds both lines")
    sent = check_calls[-1]
    check(sent["reasoning_model"] and "Show there is" in sent["current"], "the whole statement goes to the reasoning model")

    # Copying: the card's Copy button, and highlighting card text with the mouse + Ctrl+C.
    card = page.locator(".feedback", has_text="Which theorem")
    card.locator(".feedback-copy").click()
    clip = page.evaluate("navigator.clipboard.readText()")
    check(clip.startswith("Question: ") and QUESTION in clip and "\\text{Let }" in clip, "Copy button copies text + LaTeX")
    page.evaluate("navigator.clipboard.writeText('')")
    msg = card.locator(".feedback-msg").bounding_box()
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
    page.screenshot(path=f"{OUT}/p2_statement.png")

    # Three math lines in one lasso: one card, not three.
    lasso(page, 100, 170, 260, 425)
    page.click(".check-btn")
    page.wait_for_selector(".chip")
    page.click("text=Looks right, check it")
    page.wait_for_function("editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length === 4",
                           timeout=8000)
    last = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').at(-1).props")
    check(last["verdict"] == "refuted" and "Line 2:" in last["message"], f"one card for three lines ({last['message']})")

    # Words read letter by letter are a statement, never refuted with nonsense counterexamples.
    js(page, "editor.setCurrentTool('draw')")
    for i in range(6):
        stroke(page, [(120 + i * 30, 700), (140 + i * 30, 690)])
    page.wait_for_timeout(2500)
    lasso(page, 100, 670, 320, 725)
    page.click(".check-btn")
    page.wait_for_selector(".chip")
    page.evaluate("document.querySelector('.chip .primary').click()")  # the chip opens below the viewport here
    page.wait_for_function("editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').length === 5",
                           timeout=8000)
    last = js(page, "return editor.getCurrentPageShapes().filter(s => s.type === 'mellow-feedback').at(-1).props")
    check(last["verdict"] == "unverified" and last["message"] == QUESTION,
          f"letter-by-letter words get a question, never a refutation ({last['verdict']})")
    page.wait_for_timeout(800)
    page.reload()  # readings from before the fix get re-read when the notebook opens
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(2500)
    check(sum(len(c["strokes"]) == 6 for c in recognize_calls) == 2, "an old letter-by-letter reading is re-read once")
    page.screenshot(path=f"{OUT}/p2_one_card.png")

    # Hints or answers: the student's choice, saved per browser.
    check(page.locator("select[aria-label=Feedback]").input_value() == "hint", "hints are the default")
    page.select_option("select[aria-label=Feedback]", "answer")
    page.reload()
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(500)
    check(page.locator("select[aria-label=Feedback]").input_value() == "answer", "the choice survives a reload")
    lasso(page, 100, 490, 340, 615)
    page.click(".check-btn")
    page.wait_for_selector(".chip")
    page.click("text=Looks right, check it")
    page.wait_for_selector(".feedback-answer", timeout=8000)
    check(check_calls[-1]["mode"] == "answer", "the request asks for an answer")
    check(page.locator(".feedback-answer").inner_text().count("not verified") == 1 and
          ANSWER.split("$")[0] in page.locator(".feedback-answer").inner_text(), "answer card is labelled unverified")
    page.screenshot(path=f"{OUT}/p2_answer.png")

    check(not errors, f"no page errors {errors}")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
