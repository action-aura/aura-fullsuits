"""Laptop Products -> Import, through the real dialog. 'look' prints the dialog;
'run <csv>' uploads the file, follows the dialog's buttons, and prints the
product list afterwards."""
import sys
from playwright.sync_api import sync_playwright

MODE = sys.argv[1] if len(sys.argv) > 1 else "look"
SHOTS = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\join-door-shots\rehearsal"


def visible_controls(pg):
    out = []
    for sel in ("input", "select", "textarea", "button"):
        for el in pg.locator(sel).all():
            try:
                if not el.is_visible():
                    continue
                label = el.get_attribute("type") if sel == "input" else (el.inner_text() or "").strip()[:50]
                out.append((sel, el.get_attribute("id"), el.get_attribute("placeholder"), label))
            except Exception:  # noqa: BLE001
                pass
    return out


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto("http://127.0.0.1:5010/")
    pg.wait_for_selector("#rl-pass, #sub-header-section", timeout=30000)
    if pg.locator("#rl-pass").count():
        pg.fill("#rl-email", "desk-owner@rehearsal.local")
        pg.fill("#rl-pass", "DeskOwner2026!Pass")
        pg.click("#rl-btn")
        pg.wait_for_selector("#sub-header-section", timeout=30000)
    pg.wait_for_timeout(1500)
    pg.evaluate("(s) => SubsystemApp._navigate(s)", "products")
    pg.wait_for_timeout(2000)
    pg.locator("#subsystem-shell button:visible").filter(has_text="Import").first.click()
    pg.wait_for_timeout(1500)
    pg.screenshot(path=SHOTS + r"\laptop-import-dialog.png")
    for c in visible_controls(pg):
        if c[0] != "button" or c[3] not in ("Edit", "Stock", "Delete"):
            print(c)
    text = pg.locator("body").inner_text()
    print("dialog text:", [l.strip() for l in text.splitlines() if l.strip()][-14:])
    if MODE == "run":
        csv_path = sys.argv[2]
        file_input = pg.locator("input[type=file]").first
        file_input.set_input_files(csv_path)
        pg.wait_for_timeout(2500)
        pg.screenshot(path=SHOTS + r"\laptop-import-1-file.png")
        text = pg.locator("body").inner_text()
        print("after file:", [l.strip() for l in text.splitlines() if l.strip()][-10:])
        pg.click("#iw-next1")
        pg.wait_for_timeout(2000)
        pg.screenshot(path=SHOTS + r"\laptop-import-2-map.png")
        sels = pg.locator("select:visible").all()
        print("mapping selects:", len(sels))
        for s in sels[:12]:
            try:
                print("   ", s.get_attribute("id"), "->", s.input_value())
            except Exception:  # noqa: BLE001
                pass
        btns = [(c[1], c[3]) for c in visible_controls(pg) if c[0] == "button" and c[3] not in ("Edit", "Stock", "Delete")]
        print("buttons on map step:", btns[-5:])
        nxt = pg.locator("button:visible").filter(has_text="Next").last
        nxt.click()
        pg.wait_for_timeout(2500)
        pg.screenshot(path=SHOTS + r"\laptop-import-3-review.png")
        text = pg.locator("body").inner_text()
        print("review step:", [l.strip() for l in text.splitlines() if l.strip()][-14:])
        btns = [(c[1], c[3]) for c in visible_controls(pg) if c[0] == "button" and c[3] not in ("Edit", "Stock", "Delete")]
        print("buttons on review step:", btns[-5:])
        go = pg.locator("button:visible").filter(has_text="Import").last
        go.click()
        pg.wait_for_timeout(4000)
        pg.screenshot(path=SHOTS + r"\laptop-import-4-done.png")
        text = pg.locator("body").inner_text()
        print("after import:", [l.strip() for l in text.splitlines() if l.strip()][-12:])
        pg.evaluate("(s) => SubsystemApp._navigate(s)", "products")
        pg.wait_for_timeout(2500)
        text = pg.locator("#subsystem-shell").inner_text()
        print("rows with AHM-:", [l.strip()[:90] for l in text.splitlines() if "AHM-" in l])
    b.close()
