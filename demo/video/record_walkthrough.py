"""Record a silent walkthrough of TVLens as the live user `test`.

Logs in out of frame (a throwaway context), then records a second context that
starts already signed in, so the video is only the product. Motion is a hand
written eased scroll, so the timing is deterministic and the captions in
build_video.sh line up with the scenes.

Output: demo/video/raw/<hash>.webm  (renamed to walkthrough-raw.webm by the shell script)

Run with the dev server up:
    .venv/bin/python manage.py runserver 127.0.0.1:8011 &
    .venv/bin/python demo/video/record_walkthrough.py
"""

import pathlib

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8011"
HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / "raw"
RAW.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080

# A smooth eased scroll to an absolute Y, run inside the page. Returns when done.
EASE_SCROLL = """
([targetY, ms]) => new Promise(resolve => {
    const startY = window.scrollY;
    const dist = targetY - startY;
    const t0 = performance.now();
    function step(now) {
        const p = Math.min(1, (now - t0) / ms);
        const e = p < 0.5 ? 2*p*p : 1 - Math.pow(-2*p+2, 2)/2;  // easeInOutQuad
        window.scrollTo(0, startY + dist * e);
        if (p < 1) requestAnimationFrame(step); else resolve();
    }
    requestAnimationFrame(step);
});
"""

# Absolute Y of a section (by its heading text), placed a little below the top.
TOP_OF = """
(title) => {
    const heads = [...document.querySelectorAll('.row-title, h2')];
    const h = heads.find(e => e.textContent.trim().startsWith(title));
    if (!h) return 0;
    const sec = h.closest('section') || h;
    return sec.getBoundingClientRect().top + window.scrollY - 40;
};
"""


def smooth(page, y, ms):
    page.evaluate(EASE_SCROLL, [y, ms])


def hold(page, ms):
    page.wait_for_timeout(ms)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])

    # 1. Log in out of frame, keep the session.
    setup = browser.new_context(viewport={"width": W, "height": H})
    pg = setup.new_page()
    pg.goto(f"{BASE}/accounts/login/")
    pg.fill("input[name=username]", "test")
    pg.fill("input[name=password]", "tvlens-demo")
    pg.click("button[type=submit]")
    pg.wait_for_load_state("networkidle")
    state = setup.storage_state()
    setup.close()

    # 2. Recorded context, already signed in.
    ctx = browser.new_context(
        viewport={"width": W, "height": H},
        storage_state=state,
        record_video_dir=str(RAW),
        record_video_size={"width": W, "height": H},
    )
    page = ctx.new_page()
    page.goto(f"{BASE}/", wait_until="networkidle")
    hold(page, 400)  # let posters paint

    # Scene 1: home top, Watch next.
    hold(page, 5000)

    # Scroll down through Top Picks and Recently added to Side Quests.
    y_side = page.evaluate(TOP_OF, "Side Quests")
    y_top = page.evaluate(TOP_OF, "Top Picks")
    smooth(page, y_top, 2000)
    hold(page, 1500)
    smooth(page, y_side, 2500)

    # Scene 2: Side Quests hold.
    hold(page, 5000)

    # Navigate into a show (real click on its card).
    page.locator("a[href='/shows/game-of-thrones/']").first.click()
    page.wait_for_load_state("networkidle")
    page.evaluate("window.scrollTo(0,0)")
    hold(page, 500)

    # Scene 3: detail hero.
    hold(page, 4500)

    # Scroll to the plain-language reasons.
    y_why = page.evaluate(TOP_OF, "More shows like this")
    smooth(page, y_why, 2500)

    # Scene 4: the "why" reasons, the money shot. Linger.
    hold(page, 7500)

    ctx.close()  # writes the webm
    browser.close()

# Playwright names the file by a hash; rename to the stable name build_video.sh reads.
vids = sorted(RAW.glob("page@*.webm"))
if vids:
    dest = RAW / "walkthrough-raw.webm"
    vids[-1].replace(dest)
    print("wrote:", dest)
else:
    print("NONE")
