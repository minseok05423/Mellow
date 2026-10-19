"""Models menu: shows the backend's catalog, disables what has no key, and the choice is sent + remembered.
Needs the backend running (for /api/models). Reading requests are intercepted, so no vendor is billed."""
import json
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


sent = []


def capture(route):
    sent.append(json.loads(route.request.post_data))
    route.fulfill(json={"latex": "x", "confidence": 0.99, "source": "llm", "has_prose": False})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.route("**/api/recognize", capture)
    # Page labelling and background solving would call real models; this test is about the Models menu.
    page.route("**/api/structure", lambda r: r.fulfill(status=503, json={"detail": "faked off"}))
    page.route("**/api/presolve", lambda r: r.fulfill(status=202, json={"queued": True}))
    page.goto("http://localhost:5173/")
    page.wait_for_load_state("networkidle")
    page.click("text=+ New notebook")
    page.wait_for_function("window.editor !== undefined")
    page.click("text=Models ▾")
    page.wait_for_selector(".models-panel")
    cat = page.evaluate("fetch('/api/models').then(r => r.json())")
    for label, group in [("Handwriting reader", "recognizers"), ("Image reader (fallback)", "readers"), ("Reasoning", "reasoners")]:
        opts = page.eval_on_selector_all(f"select[aria-label='{label}'] option", "os => os.map(o => [o.value, o.disabled])")
        expected = [[m["id"], not m["available"]] for m in cat[group]]
        check(opts == expected, f"{label}: options match the backend catalog, unavailable ones disabled")
    page.screenshot(path=f"{OUT}/models_menu.png")

    readers = [m["id"] for m in cat["readers"] if m["available"]]
    reader = readers[-1]
    page.select_option("select[aria-label='Handwriting reader']", "none")
    page.select_option("select[aria-label='Image reader (fallback)']", reader)

    # Draw one stroke; the live reading request must carry the choices.
    s = page.evaluate("editor.pageToScreen({x: 120, y: 200})")
    page.mouse.move(s["x"], s["y"]); page.mouse.down(); page.mouse.move(s["x"] + 60, s["y"] + 5, steps=6); page.mouse.up()
    page.wait_for_timeout(1800)
    check(bool(sent) and sent[-1].get("recognizer") == "none" and sent[-1].get("reader_model") == reader,
          f"reading request carries the chosen models ({sent[-1].get('recognizer') if sent else None}, {sent[-1].get('reader_model') if sent else None})")

    page.reload()
    page.wait_for_function("window.editor !== undefined")
    page.click("text=Models ▾")
    check(page.input_value("select[aria-label='Image reader (fallback)']") == reader
          and page.input_value("select[aria-label='Handwriting reader']") == "none", "choices survive a reload")
    browser.close()

print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
