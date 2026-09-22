"""Capture demo screenshots of TVLens as the live user `test`.

Storytelling take (2026-09-20, demo/walkthrough.md decisions block): six
frames that follow one person deciding what to watch tonight, not a feature
tour, in story order:

  1. Top Picks    -- what test rated, ranked by distance from the crowd.
  2. Watch Next   -- the answer: unwatched shows sharing people with those.
  3. Detail hero  -- opens a pick, Game of Thrones.
  4. Detail why   -- the plain-language reason, the shared people.
  5. Side Quests  -- the surprise, off the usual path.
  6. End card     -- tvlens.org in large text, matching the video's closing
                      card (rendered with ffmpeg, not the browser).

Every browser frame (1-5) gets a small caption strip injected at the bottom
before the shot, carrying the beat's caption and tvlens.org, per the demo
decisions block ("screenshots carry the URL too"). The strip is fixed to the
viewport, so frames are taken as viewport screenshots (not element crops,
which would clip a fixed-position overlay outside the element's own box).
"""
import subprocess
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

BASE = "http://127.0.0.1:8007"
DEMO = Path("/home/patricioquezada/Work/tvlens/demo")
OUT = DEMO / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
TXT = DEMO / "video" / "txt"  # shared caption/wordmark text, also read by build_video.sh

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-gpu")
opts.add_argument("--window-size=1440,1000")
opts.add_argument("--force-device-scale-factor=2")
opts.add_argument("--hide-scrollbars")
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)

CAPTION_JS = """
(function(caption) {
    var bar = document.createElement('div');
    bar.id = '__demo_caption';
    bar.style.cssText = [
        'position:fixed', 'left:0', 'right:0', 'bottom:0', 'z-index:999999',
        'background:rgba(8,8,10,0.92)', 'border-top:1px solid rgba(232,155,45,0.35)',
        'padding:16px 30px', 'display:flex', 'justify-content:space-between',
        'align-items:baseline', "font-family:'DM Sans',sans-serif",
        'color:#f0ebe3', 'font-size:17px'
    ].join(';');
    var left = document.createElement('span');
    left.textContent = caption;
    var right = document.createElement('span');
    right.textContent = 'tvlens.org';
    right.style.cssText = 'color:#e89b2d;font-weight:700;letter-spacing:0.03em;white-space:nowrap;margin-left:24px;';
    bar.appendChild(left);
    bar.appendChild(right);
    document.body.appendChild(bar);
})(arguments[0]);
"""


def caption(text):
    driver.execute_script(CAPTION_JS, text)


def row_shot(title_prefix, path, cap):
    """Scroll the row whose heading starts with title_prefix into view, caption it,
    and take a viewport screenshot (not an element crop, so the fixed caption bar
    at the bottom of the window is included)."""
    rows = driver.find_elements(By.CSS_SELECTOR, "section.row")
    target = None
    for r in rows:
        try:
            t = r.find_element(By.CSS_SELECTOR, ".row-title").text.strip()
        except Exception:
            continue
        if t.startswith(title_prefix):
            target = r
            break
    if target is None:
        raise RuntimeError(f"row not found: {title_prefix}")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
    time.sleep(0.5)
    caption(cap)
    time.sleep(0.2)
    driver.save_screenshot(str(path))


def viewport_shot(path, cap, scroll_to=None):
    if scroll_to is not None:
        driver.execute_script("window.scrollTo(0, arguments[0]);", scroll_to)
        time.sleep(0.5)
    caption(cap)
    time.sleep(0.2)
    driver.save_screenshot(str(path))


def section_top(heading_prefix):
    # Selenium's execute_script runs the string as a function BODY (using
    # arguments[0], arguments[1], ...) -- it does not call an arrow-function
    # expression the way Playwright's page.evaluate does. Plain statements only.
    return driver.execute_script(
        """
        var prefix = arguments[0];
        var heads = Array.prototype.slice.call(document.querySelectorAll('.row-title, h2'));
        var target = null;
        for (var i = 0; i < heads.length; i++) {
            if (heads[i].textContent.trim().indexOf(prefix) === 0) { target = heads[i]; break; }
        }
        if (!target) { return 0; }
        var sec = target.closest('section') || target;
        return sec.getBoundingClientRect().top + window.scrollY - 40;
        """,
        heading_prefix,
    )


def build_end_card():
    """Render the end card as a still image with the same wordmark/URL treatment
    as the closing card in build_video.sh, from the same shared text files, so
    editing one caption source updates both video and screenshot."""
    fb = "/usr/share/fonts/noto/NotoSans-Black.ttf"
    fm = "/usr/share/fonts/noto/NotoSans-Medium.ttf"
    bg, white, amber = "0x08080a", "0xf0ebe3", "0xe89b2d"
    out = OUT / "06-end-card.png"
    filt = (
        f"drawtext=fontfile={fb}:textfile={TXT}/wtv.txt:fontsize=90:fontcolor={white}:x=w/2-tw-42:y=250,"
        f"drawtext=fontfile={fb}:textfile={TXT}/wlens.txt:fontsize=90:fontcolor={amber}:x=w/2-42:y=250,"
        f"drawtext=fontfile={fm}:textfile={TXT}/csub.txt:fontsize=38:fontcolor={white}:x=(w-tw)/2:y=400,"
        f"drawbox=x=(iw-300)/2:y=470:w=300:h=4:color={amber}@0.9:t=fill,"
        f"drawtext=fontfile={fb}:textfile={TXT}/curl.txt:fontsize=140:fontcolor={amber}:x=(w-tw)/2:y=540"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={bg}:s=1920x1080:d=1",
            "-vf", filt,
            "-frames:v", "1",
            str(out),
        ],
        check=True,
    )
    print("wrote:", out)


try:
    # Log in.
    driver.get(f"{BASE}/accounts/login/")
    driver.find_element(By.NAME, "username").send_keys("test")
    driver.find_element(By.NAME, "password").send_keys("tvlens-demo")
    driver.find_element(By.CSS_SELECTOR, "button[type=submit], input[type=submit]").click()
    time.sleep(1.0)
    print("after login url:", driver.current_url)

    # 1. Top Picks -- what test rated, the raw material.
    driver.get(f"{BASE}/")
    time.sleep(1.2)
    row_shot("Top Picks", OUT / "01-top-picks.png",
              "What test rated: Top Picks, ranked by distance from the crowd")

    # 2. Watch Next -- the answer.
    row_shot("Watch next", OUT / "02-watch-next.png",
              "Watch Next: unwatched shows that share people with what you loved")

    # 3 & 4. Detail hero, then the "why" reasons.
    driver.get(f"{BASE}/shows/game-of-thrones/")
    time.sleep(1.2)
    viewport_shot(OUT / "03-detail-hero.png", "Opens a pick: Game of Thrones", scroll_to=0)

    why_y = section_top("More shows like this")
    viewport_shot(OUT / "04-detail-why.png",
                   "Why: the people who made it, not popularity", scroll_to=why_y)

    # 5. Side Quests -- the surprise.
    driver.get(f"{BASE}/")
    time.sleep(1.2)
    row_shot("Side Quests", OUT / "05-side-quests.png",
              "Side Quests: off your usual path, through people you already trust")

    # 6. End card.
    build_end_card()

finally:
    driver.quit()
