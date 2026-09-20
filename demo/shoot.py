"""Capture demo screenshots of TVLens as the live user `test`."""
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

BASE = "http://127.0.0.1:8007"
OUT = Path("/home/patricioquezada/Work/tvlens/demo/screenshots")
OUT.mkdir(parents=True, exist_ok=True)

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-gpu")
opts.add_argument("--window-size=1440,1000")
opts.add_argument("--force-device-scale-factor=2")
opts.add_argument("--hide-scrollbars")
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)


def full_page_png(path):
    # Grow the window to the full document height so the screenshot is the whole page.
    h = driver.execute_script("return document.body.scrollHeight")
    driver.set_window_size(1440, h + 120)
    time.sleep(0.6)
    driver.save_screenshot(str(path))


try:
    # Log in.
    driver.get(f"{BASE}/accounts/login/")
    driver.find_element(By.NAME, "username").send_keys("test")
    driver.find_element(By.NAME, "password").send_keys("tvlens-demo")
    driver.find_element(By.CSS_SELECTOR, "button[type=submit], input[type=submit]").click()
    time.sleep(1.0)
    print("after login url:", driver.current_url)

    # Home page, full.
    driver.get(f"{BASE}/")
    time.sleep(1.2)
    full_page_png(OUT / "01-home-full.png")

    # Each recommendation row, focused.
    rows = driver.find_elements(By.CSS_SELECTOR, "section.row")
    titles = []
    idx = 2
    for r in rows:
        try:
            title = r.find_element(By.CSS_SELECTOR, ".row-title").text.strip()
        except Exception:
            title = "row"
        slug = (
            title.lower()
            .replace("for test", "")
            .strip()
            .replace(" ", "-")
            .replace("'", "")
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", r)
        time.sleep(0.4)
        r.screenshot(str(OUT / f"{idx:02d}-row-{slug}.png"))
        titles.append(title)
        idx += 1
    print("rows captured:", titles)

    # Bonus: a show detail page, to show the graph explanations ("why connected").
    driver.get(f"{BASE}/shows/game-of-thrones/")
    time.sleep(1.2)
    full_page_png(OUT / "07-detail-game-of-thrones.png")

finally:
    driver.quit()
