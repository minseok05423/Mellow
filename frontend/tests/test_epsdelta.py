import sys, urllib.request, json
from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
fails = []
def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(viewport={"width": 1400, "height": 820})
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:5173/epsdelta.html")
    page.wait_for_load_state("networkidle")

    for demo in ["square", "step"]:
        page.click(f'#tabs button[data-demo="{demo}"]')
        page.wait_for_timeout(1100)  # demo-switch morph is 900 ms
        for eps in ["0.8", "0.3", "0.05"]:
            page.fill("#eps", eps)
            page.wait_for_timeout(400)
            page.screenshot(path=f"{OUT}/{demo}_eps{eps}.png")
            verdict = page.inner_text("#verdict")
            no_fit = "No δ fits" in verdict
            expect = demo == "step" and float(eps) < 0.5
            check(page.inner_text("#epsOut") == f"{float(eps):.3f}", f"{demo} ε={eps}: ε readout")
            check(no_fit == expect, f"{demo} ε={eps}: 'No δ fits' shown={no_fit}, expected={expect}  [{verdict[:60]}...]")

    # Real mouse drag on the canvas handle (square demo, ε=0.3).
    page.click('#tabs button[data-demo="square"]'); page.wait_for_timeout(1100)
    page.fill("#eps", "0.3"); page.wait_for_timeout(100)
    box = page.locator("#board").bounding_box()
    H = box["height"]
    sy = lambda y: 20 + (2.9 - y) / 3.5 * (H - 40)   # matches square.bounds and pad=20
    hx, hy = box["x"] + 74, box["y"] + sy(1.3)
    page.mouse.move(hx, hy)
    check(page.evaluate("document.getElementById('board').style.cursor") == "ns-resize", "hovering handle shows ns-resize cursor")
    page.mouse.down()
    page.mouse.move(hx, box["y"] + sy(1.6), steps=12)
    page.screenshot(path=f"{OUT}/drag_mid.png")
    page.mouse.up()
    eps_after = float(page.inner_text("#epsOut"))
    check(abs(eps_after - 0.6) < 0.01, f"drag handle from ε=0.3 to y=1.6 gives ε≈0.6 (got {eps_after})")
    # Drag the bottom edge downward also grows ε.
    page.mouse.move(box["x"] + 600, box["y"] + sy(1 - 0.6)); page.mouse.down()
    page.mouse.move(box["x"] + 600, box["y"] + sy(0.2), steps=8); page.mouse.up()
    check(abs(float(page.inner_text("#epsOut")) - 0.8) < 0.01, f"drag bottom edge down to y=0.2 gives ε≈0.8 (got {page.inner_text('#epsOut')})")
    # Dragging past the range clamps.
    page.mouse.move(hx, box["y"] + sy(1.8)); page.mouse.down()
    page.mouse.move(hx, box["y"] + 25, steps=5); page.mouse.up()
    check(page.inner_text("#epsOut") == "1.000", f"drag clamps at ε=1 (got {page.inner_text('#epsOut')})")
    page.mouse.move(hx, box["y"] + sy(2.0)); page.mouse.down()
    page.mouse.move(hx, box["y"] + sy(1.0), steps=5); page.mouse.up()
    check(page.inner_text("#epsOut") == "0.010", f"drag clamps at ε=0.01 (got {page.inner_text('#epsOut')})")

    # Step demo boundary: ε = 0.5 exactly has no δ (strict inequality); 0.501 has one.
    page.click('#tabs button[data-demo="step"]'); page.wait_for_timeout(1100)
    page.fill("#eps", "0.5"); check("No δ fits" in page.inner_text("#verdict"), "step ε=0.5: no δ")
    page.fill("#eps", "0.501"); check("No δ fits" not in page.inner_text("#verdict"), "step ε=0.501: δ exists")

    # Mid-morph frame, and a narrow (phone-ish) layout.
    page.click('#tabs button[data-demo="square"]'); page.wait_for_timeout(450)
    page.screenshot(path=f"{OUT}/morph_mid.png")
    page.set_viewport_size({"width": 390, "height": 844}); page.wait_for_timeout(1000)
    page.screenshot(path=f"{OUT}/narrow.png")

    # Backend through the Vite proxy.
    health = json.loads(urllib.request.urlopen("http://localhost:5173/api/health").read())
    check(health.get("ok") is True, f"/api/health via proxy -> {health}")

    check(not errors, f"no console errors {errors}")
    b.close()
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
