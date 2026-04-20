"""
EMA közösségi regiszter (veterinary) scraper v2
URL: https://ec.europa.eu/health/documents/community-register/html/reg_vet_act.htm?sort=n
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://ec.europa.eu/health/documents/community-register/html/reg_vet_act.htm?sort=n"


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().strip())


def scrape(headless: bool, timeout_s: int) -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            locale="en-US",
            timezone_id="Europe/Budapest",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
        """)

        page = ctx.new_page()
        print(f"  Navigálás: {URL}")

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        except PWTimeout:
            print("  Timeout, folytatás…")

        # Cookie banner
        try:
            for sel in ["#cookie-consent-accept", "button:has-text('Accept')", "button:has-text('Elfogad')"]:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    break
        except Exception:
            pass

        page.wait_for_timeout(8000)

        rows: list[dict] = []
        all_frames = [page.main_frame] + list(page.frames)

        for frame in all_frames:
            try:
                anchors = frame.locator("a[href*='.htm']").all()
                vet_links = [a for a in anchors if re.search(r"v\d+\.htm", a.get_attribute("href") or "")]
                if len(vet_links) < 5:
                    continue
                print(f"  Tartalmi keret: {len(vet_links)} termék")
                for a in vet_links:
                    try:
                        href = a.get_attribute("href") or ""
                        name = (a.inner_text() or "").strip()
                        if name:
                            rows.append({"name": name, "name_key": normalize_name(name), "link": href})
                    except Exception:
                        continue
                break
            except Exception:
                continue

        if not rows:
            print("  Fallback: táblázat keresés…")
            for frame in all_frames:
                try:
                    trs = frame.locator("table tr")
                    count = trs.count()
                    if count < 5:
                        continue
                    for i in range(count):
                        tds = trs.nth(i).locator("td")
                        if tds.count() == 0:
                            continue
                        texts = [t.strip() for t in tds.all_text_contents()]
                        nonempty = [t for t in texts if t]
                        if not nonempty:
                            continue
                        links = trs.nth(i).locator("a").all()
                        href = next((lnk.get_attribute("href") for lnk in links if ".htm" in (lnk.get_attribute("href") or "")), "")
                        rows.append({"name": nonempty[0], "name_key": normalize_name(nonempty[0]), "link": href})
                    if rows:
                        break
                except Exception:
                    continue

        if not rows:
            try:
                snippet = page.content()[:800].replace("\n", " ")
                print(f"  Oldal tartalom: {snippet}")
            except Exception:
                pass

        browser.close()

    seen, uniq = set(), []
    for r in rows:
        if r["name_key"] not in seen:
            seen.add(r["name_key"])
            uniq.append(r)
    return uniq


def load_snapshot(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_snapshot(path: Path, items: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"captured_at": dt.datetime.now().isoformat(timespec="seconds"), "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["name", "link"])
        w.writeheader()
        for r in rows:
            w.writerow({"name": r.get("name", ""), "link": r.get("link", "")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="ema_state")
    ap.add_argument("--output-dir", default=".")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    snapshot_path = Path(args.state_dir) / "latest.json"
    out_dir = Path(args.output_dir)

    prev_data = load_snapshot(snapshot_path)
    prev_items = prev_data.get("items", [])
    prev_keys = {normalize_name(r.get("name", "")) for r in prev_items if r.get("name")}
    print(f"Előző snapshot: {prev_data.get('captured_at', '—')} ({len(prev_keys)} termék)")
    print("Aktuális oldal lekérdezése…")

    current = scrape(headless=not args.headful, timeout_s=args.timeout)
    print(f"  Aktuális termékek száma: {len(current)}")

    if not current:
        print("HIBA: Nem sikerült adatot kinyerni.", file=sys.stderr)
        sys.exit(1)

    new_items = [r for r in current if r["name_key"] not in prev_keys]
    print(f"  Új tételek: {len(new_items)}")

    write_csv(out_dir / "full_list.csv", current)
    write_csv(out_dir / "new_since_last_run.csv", new_items)
    save_snapshot(snapshot_path, current)

    if not prev_keys:
        print("\nEz az első futás — alapállapot rögzítve.")
    elif new_items:
        print("\nÚJ TÉTELEK:")
        for r in new_items:
            print(f"  • {r['name']}")
    else:
        print("\nNincs új tétel.")


if __name__ == "__main__":
    main()
