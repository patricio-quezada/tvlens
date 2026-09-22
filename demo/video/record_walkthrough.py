"""Record a silent walkthrough of TVLens as the live user `test`.

Storytelling take (2026-09-20, demo/walkthrough.md decisions block): one person
deciding what to watch tonight, not a feature tour. The camera follows the
story in this order:

  1. Top Picks   -- what test rated, the raw material every row is built from.
  2. Watch Next  -- the answer: unwatched shows that share people with those.
  3. A show's "why" -- open Game of Thrones, scroll to the shared-people reason.
  4. Side Quests -- the surprise, off the usual path.

Logs in out of frame (a throwaway context), then records a second context that
starts already signed in, so the video is only the product. Motion is a hand
written eased scroll, so the timing is deterministic and the captions in
build_video.sh line up with the scenes. Each scene boundary prints its elapsed
time (time.monotonic() since the recorded context's first page was created) so
build_video.sh's caption windows can be set from measured numbers, not guesses.

Output: demo/video/raw/<hash>.webm  (renamed to walkthrough-raw.webm by the shell script)

Run with the dev server up:
    .venv/bin/python manage.py runserver 127.0.0.1:8011 &
    .venv/bin/python demo/video/record_walkthrough.py
"""

import pathlib
import time

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


t0 = None


def mark(label):
    print(f"{time.monotonic() - t0:6.2f}s  {label}")


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
    t0 = time.monotonic()
    page.goto(f"{BASE}/", wait_until="networkidle")
    mark("home loaded")
    hold(page, 400)  # let posters paint

    # Scene 1: Top Picks -- what the person rated, the raw material.
    y_top = page.evaluate(TOP_OF, "Top Picks")
    smooth(page, y_top, 1200)
    mark("scrolled to Top Picks")
    hold(page, 4200)
    mark("Top Picks hold done")

    # Scene 2: Watch Next -- the answer built from those ratings.
    y_next = page.evaluate(TOP_OF, "Watch next")
    smooth(page, y_next, 1500)
    mark("scrolled to Watch Next")
    hold(page, 4200)
    mark("Watch Next hold done")

    # Navigate into a show (real click on its card) to explain why it is here.
    page.locator("a[href='/shows/game-of-thrones/']").first.click()
    page.wait_for_load_state("networkidle")
    mark("Game of Thrones detail loaded")
    page.evaluate("window.scrollTo(0,0)")
    hold(page, 400)

    # Scene 3: detail hero, brief, then the plain-language "why" reasons.
    hold(page, 2000)
    mark("hero hold done")
    y_why = page.evaluate(TOP_OF, "More shows like this")
    smooth(page, y_why, 2000)
    mark("scrolled to why reasons")
    hold(page, 4500)
    mark("why hold done")

    # Scene 4: Side Quests -- the surprise, back on the home page.
    page.goto(f"{BASE}/", wait_until="networkidle")
    mark("home reloaded")
    y_side = page.evaluate(TOP_OF, "Side Quests")
    smooth(page, y_side, 1500)
    mark("scrolled to Side Quests")
    hold(page, 4200)
    mark("Side Quests hold done")

    ctx.close()  # writes the webm
    mark("context closed")
    browser.close()

# Playwright names the file by a hash; rename to the stable name build_video.sh reads.
vids = sorted(RAW.glob("page@*.webm"))
if vids:
    dest = RAW / "walkthrough-raw.webm"
    vids[-1].replace(dest)
    print("wrote:", dest)
else:
    print("NONE")
