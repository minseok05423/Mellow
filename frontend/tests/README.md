# Frontend tests (Playwright, headless Chromium)

One-time setup (keeps Playwright out of the app's dependencies):
```bash
python3 -m venv ~/.venvs/mellow-tests
~/.venvs/mellow-tests/bin/pip install -r frontend/tests/requirements.txt
~/.venvs/mellow-tests/bin/playwright install --with-deps chromium
```

Run, with the dev server up (`cd frontend && npm run dev`). If you script server start/stop, launch `node_modules/.bin/vite` directly: `npm run dev` leaves Vite running as an orphaned child.
```bash
~/.venvs/mellow-tests/bin/python frontend/tests/test_notebook.py /tmp/shots       # the notebook
~/.venvs/mellow-tests/bin/python frontend/tests/test_lean.py /tmp/shots           # Check with Lean, endpoints faked
~/.venvs/mellow-tests/bin/python frontend/tests/test_lean.py /tmp/shots --real    # the same with the real backend,
                                                                                  # Lean and Claude (~1 min, a few cents)
```
Each prints PASS/FAIL per check, exits non-zero on failure, and saves screenshots to the given folder.

What these can't cover: real pen input (pressure, Apple Pencil's coalesced 240 Hz events,
palm rejection). Check those by hand on a pen device.
