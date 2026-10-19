"""P1 notebook checks: library, pages, drawing + raw ink, lasso, samples, undo, eraser, persistence.

Run (dev server must be up on :5173, see tests/README.md):
    python tests/test_notebook.py <screenshot_dir>
Mouse only: pen pressure, palm rejection and coalesced Pencil events need a real device.
"""
import json
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
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    # P1 is about the notebook itself: answer live-reading calls with "nothing read" (no backend needed).
    page.route("**/api/recognize", lambda r: r.fulfill(
        json={"latex": "", "confidence": None, "source": "none", "has_prose": False}))

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

    # --- save + download a bake-off sample ---
    page.click("text=Save sample")
    page.fill(".sample-form input", "2q^2")
    page.click(".sample-form button[type=submit]")
    page.wait_for_selector("text=Samples (1)")
    check(True, "sample saved, counter shows 1")
    with page.expect_download() as dl:
        page.click("text=Samples (1)")
    data = json.loads(open(dl.value.path()).read())
    s0 = data["samples"][0]
    check(data["schema_version"] == 1 and s0["truth"] == "2q^2" and s0["kind"] == "math", "download has the sample")
    check(len(s0["strokes"]) == 3 and s0["image"].startswith("data:image/png"), "sample has 3 strokes + PNG")
    xs = [pt[0] for st in s0["strokes"] for pt in st["pts"]]
    check(min(xs) >= -1, "sample strokes are relative to the selection's top-left")

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
