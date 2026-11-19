"""Notebook checks: library, pages, the stripped toolbar, drawing + raw ink, lasso, undo, eraser, persistence.

Run (dev server must be up on :5173, see tests/README.md):
    python tests/test_notebook.py <screenshot_dir>
Mouse only: pen pressure, palm rejection and coalesced Pencil events need a real device.
"""
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
URL = "http://localhost:5173/"
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def js(page, expr):
    return page.evaluate(f"(() => {{ const editor = window.editor; {expr} }})()")


def shapes(page, kind):
    return js(page, f"return editor.getCurrentPageShapes().filter(s => s.type === '{kind}')")


def to_screen(page, x, y):
    p = js(page, f"return editor.pageToScreen({{x: {x}, y: {y}}})")
    return p["x"], p["y"]


def stroke(page, pts):
    """Draw one stroke through page-space points."""
    x, y = to_screen(page, *pts[0])
    page.mouse.move(x, y)
    page.mouse.down()
    for px, py in pts[1:]:
        page.mouse.move(*to_screen(page, px, py), steps=4)
    page.mouse.up()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))

    # --- library -> new notebook ---
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    check(page.locator("text=No notebooks yet").count() == 1, "library starts empty")
    page.click("text=+ New notebook")
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(500)
    nb_url = page.url
    check("#/nb/" in nb_url, f"opened notebook at {nb_url}")
    pages = shapes(page, "mellow-page")
    check(len(pages) == 1 and pages[0]["props"]["paper"] == "lined" and pages[0]["isLocked"], "one locked lined page")
    check(js(page, "return editor.getCurrentToolId()") == "draw", "pen tool active on open")
    tools = page.locator(".tlui-main-toolbar [data-testid^='tools.']").evaluate_all("els => els.map(e => e.dataset.testid)")
    check(sorted(tools) == ["tools.draw", "tools.eraser", "tools.hand", "tools.lasso-select", "tools.select"],
          f"toolbar has only pen, eraser, lasso, select, hand ({tools})")

    # --- write a 'line' of three strokes: 2, q, ^2-ish squiggles ---
    stroke(page, [(120, 200), (140, 185), (160, 200), (120, 230), (165, 230)])
    stroke(page, [(200, 205), (185, 215), (200, 225), (210, 205), (210, 245)])
    stroke(page, [(225, 180), (240, 172), (248, 185), (228, 198), (250, 198)])
    draws = shapes(page, "draw")
    check(len(draws) == 3, f"3 draw shapes ({len(draws)})")
    inks = [d["meta"].get("ink") for d in draws]
    check(all(inks), "every stroke has meta.ink")
    if all(inks):
        ink = inks[0]
        dts = [pt[3] for pt in ink["pts"]]
        check(len(ink["pts"]) >= 10, f"ink has full-rate points ({len(ink['pts'])})")
        check(dts == sorted(dts) and dts[-1] > 0, "ink timestamps increase")
        check(ink["pointer"] == "mouse" and all(len(pt) == 4 for pt in ink["pts"]), "ink points are [dx,dy,p,dt]")
        # first point sits at the shape origin (stroke started at page (120, 200))
        check(abs(draws[0]["x"] + ink["pts"][0][0] - 120) < 2 and abs(draws[0]["y"] + ink["pts"][0][1] - 200) < 2,
              "ink is anchored in page space")
    page.screenshot(path=f"{OUT}/p1_written.png")

    # --- lasso the line ---
    js(page, "editor.setCurrentTool('lasso-select')")
    loop = [(100, 160), (270, 160), (270, 260), (100, 260), (100, 162)]
    stroke(page, loop)
    sel = js(page, "return editor.getSelectedShapes().map(s => s.type)")
    check(sorted(sel) == ["draw", "draw", "draw"], f"lasso selects the 3 strokes, not the page ({sel})")
    check(js(page, "return editor.getCurrentToolId()") == "select", "lasso hands over to select tool")
    page.screenshot(path=f"{OUT}/p1_lassoed.png")

    # --- undo/redo keeps ink attached ---
    js(page, "editor.selectNone(); editor.setCurrentTool('draw')")
    stroke(page, [(120, 320), (300, 320)])
    check(len(shapes(page, "draw")) == 4, "4th stroke drawn")
    js(page, "editor.undo()")
    check(len(shapes(page, "draw")) == 3, "undo removes the stroke")
    js(page, "editor.redo()")
    redone = [d for d in shapes(page, "draw") if abs(d["y"] - 320) < 5]
    check(len(redone) == 1 and redone[0]["meta"].get("ink"), "redo restores the stroke with its ink")

    # --- pages: add, paper, eraser can't erase pages ---
    page.click("text=+ Page")
    pages = shapes(page, "mellow-page")
    check(len(pages) == 2 and pages[1]["y"] > pages[0]["y"] + pages[0]["props"]["h"], "+ Page appends below")
    page.select_option("select[aria-label=Paper]", "grid")
    check(shapes(page, "mellow-page")[0]["props"]["paper"] == "grid", "paper switch applies to page in view")
    js(page, "editor.setCurrentTool('eraser')")
    stroke(page, [(400, 500), (600, 520), (400, 540)])
    check(len(shapes(page, "mellow-page")) == 2, "eraser leaves pages alone")
    js(page, "editor.setCurrentTool('draw')")
    page.screenshot(path=f"{OUT}/p1_grid.png")

    # --- a screenshot through the Image button: on the page, selected, and read when checked ---
    page.screenshot(path=f"{OUT}/upload.png", clip={"x": 300, "y": 200, "width": 300, "height": 120})
    reads = []
    page.route("**/api/recognize", lambda r: (reads.append(r.request.post_data_json), r.fulfill(
        json={"latex": "\\text{Show that } n^2 + n \\text{ is even}", "confidence": None, "source": "llm",
              "has_prose": True})))
    page.set_input_files(".topbar input[type=file]", f"{OUT}/upload.png")
    page.wait_for_function("window.editor.getCurrentPageShapes().some(s => s.type === 'image')")
    check(js(page, "return editor.getSelectedShapes().map(s => s.type)") == ["image"], "the image lands selected")
    page.wait_for_selector(".action-bar")
    page.click("text=Check my proof")
    page.wait_for_selector(".chip .chip-read")
    check(len(reads) == 1 and reads[0]["strokes"] == [] and reads[0]["image"].startswith("data:image/png"),
          "the image is sent to the reader as an image, with no strokes")
    check("is even" in page.inner_text(".chip .chip-read"), "its reading is in the confirm box")
    page.screenshot(path=f"{OUT}/p1_image.png")
    page.click(".chip >> text=Cancel")
    js(page, "editor.deleteShapes(editor.getCurrentPageShapes().filter(s => s.type === 'image'))")
    js(page, "editor.setCurrentTool('draw')")

    # --- persistence ---
    page.wait_for_timeout(800)  # tldraw persists on a short throttle
    page.reload()
    page.wait_for_function("window.editor !== undefined")
    page.wait_for_timeout(500)
    check(len(shapes(page, "draw")) == 4 and len(shapes(page, "mellow-page")) == 2, "content survives reload")
    check(all(d["meta"].get("ink") for d in shapes(page, "draw")), "ink survives reload")

    # --- iPad-ish viewport ---
    page.set_viewport_size({"width": 820, "height": 1180})
    page.wait_for_timeout(500)
    page.screenshot(path=f"{OUT}/p1_ipad.png")

    # --- library: rename + delete ---
    page.click(".topbar .back")
    page.wait_for_selector(".library")
    check(page.locator(".cover").count() == 1, "notebook listed in library")
    page.screenshot(path=f"{OUT}/p1_library.png")
    page.once("dialog", lambda d: d.accept("Proofs week 1"))
    page.click("text=Rename")
    check(page.locator("text=Proofs week 1").count() == 1, "rename works")
    page.once("dialog", lambda d: d.accept())
    page.click("text=Delete")
    check(page.locator(".cover").count() == 0, "delete works")
    page.goto(nb_url)
    page.wait_for_selector(".library")
    check(True, "deleted notebook's URL falls back to the library")

    check(not errors, f"no page errors {errors}")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
