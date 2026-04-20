"""
EMA közösségi regiszter (veterinary) scraper

URL: https://ec.europa.eu/health/documents/community-register/html/reg_vet_act.htm?sort=n

JAVÍTÁS (2025): Az összehasonlítás kulcsa a TERMÉKNÉV (normalizálva), nem a
link-URL. Így ha egy meglévő termék oldalára új dokumentumot töltenek fel és
a link megváltozik, a script NEM jelzi új tételként — csak valóban új
termékbejegyzéseknél jelez.

HASZNÁLAT:
    python ema_register_scraper.py          # normál futás
    python ema_register_scraper.py --headful  # látható böngésző (hibakeresés)
    python ema_register_scraper.py --timeout 180  # lassú kapcsolatnál

TELEPÍTÉS (egyszer):
    pip install playwright
    playwright install chromium
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
    """Terméknév normalizálása: kisbetű, extra szóközök eltávolítása."""
    return re.sub(r"\s+", " ", name.lower().strip())


def scrape(headless: bool, timeout_s: int) -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        print(f"  Navigálás: {URL}")

        try:
            page.goto(URL, wait_until="networkidle", timeout=timeout_s * 1000)
        except PWTimeout:
            print("  Timeout a betöltésnél, folytatás…")

        # Várakozás az összes keret betöltésére
        page.wait_for_timeout(3000)

        rows: list[dict] = []

        # Az összes keretet végigpróbáljuk
        all_frames = [page.main_frame] + list(page.frames)

        for frame in all_frames:
            try:
                # Keresünk legalább 5 v*.htm linkre mutató anchort
                anchors = frame.locator("a[href*='.htm']").all()
                vet_links = [
                    a for a in anchors
                    if re.search(r"v\d+\.htm", a.get_attribute("href") or "")
                ]
                if len(vet_links) < 5:
                    continue

                # Ez a tartalmi keret — kinyerjük az összes terméket
                for a in vet_links:
                    try:
                        href = a.get_attribute("href") or ""
                        name = (a.inner_text() or "").strip()
                        if not name:
                            continue
                        rows.append({
                            "name": name,
                            "name_key": normalize_name(name),
                            "link": href,
                        })
                    except Exception:
                        continue
                break  # megvan a tartalmi keret

            except Exception:
                continue

        # Ha a link-alapú keresés nem adott eredményt, próbáljuk táblázatból
        if not rows:
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
                        href = ""
                        for lnk in links:
                            h = lnk.get_attribute("href") or ""
                            if ".htm" in h:
                                href = h
                                break
                        name = nonempty[0]
                        rows.append({
                            "name": name,
                            "name_key": normalize_name(name),
                            "link": href,
                        })
                    if rows:
                        break
                except Exception:
                    continue

        browser.close()

    # Deduplikálás TERMÉKNÉV alapján (ez a fix — nem link alapján!)
    seen = set()
    uniq = []
    for r in rows:
        key = r["name_key"]
        if key in seen:
            continue
        seen.add(key)
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
    data = {
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "items": items,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict]):
    columns = ["name", "link"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({"name": r.get("name", ""), "link": r.get("link", "")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="ema_state")
    ap.add_argument("--output-dir", default=".")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    snapshot_path = Path(args.state_dir) / "latest.json"
    out_dir = Path(args.output_dir)

    # Előző állapot betöltése
    prev_data = load_snapshot(snapshot_path)
    prev_items = prev_data.get("items", [])
    # Kulcs: normalizált terméknév
    prev_keys = {normalize_name(r.get("name", "")) for r in prev_items if r.get("name")}
    prev_captured = prev_data.get("captured_at", "—")

    print(f"Előző snapshot: {prev_captured} ({len(prev_keys)} termék)")
    print("Aktuális oldal lekérdezése…")

    current = scrape(headless=not args.headful, timeout_s=args.timeout)
    print(f"  Aktuális termékek száma: {len(current)}")

    if not current:
        print("HIBA: Nem sikerült adatot kinyerni az oldalról.", file=sys.stderr)
        print("Próbáld: python ema_register_scraper.py --headful", file=sys.stderr)
        sys.exit(1)

    # Összehasonlítás TERMÉKNÉV alapján — nem link alapján!
    new_items = [r for r in current if r["name_key"] not in prev_keys]
    removed_keys = prev_keys - {r["name_key"] for r in current}

    print(f"  Új tételek az előző futás óta: {len(new_items)}")
    if removed_keys:
        print(f"  Eltűnt tételek: {len(removed_keys)}")

    write_csv(out_dir / "full_list.csv", current)
    write_csv(out_dir / "new_since_last_run.csv", new_items)
    save_snapshot(snapshot_path, current)

    print(f"\nKész:")
    print(f"  Teljes lista : {(out_dir / 'full_list.csv').resolve()}")
    print(f"  Új tételek   : {(out_dir / 'new_since_last_run.csv').resolve()}")

    if not prev_keys:
        print("\nEz az első futás — alapállapot rögzítve. A következő futáskor")
        print("már látszanak a valóban új tételek.")
    elif new_items:
        print("\nÚJ TÉTELEK:")
        for r in new_items:
            print(f"  • {r['name']}")
    else:
        print("\nNincs új tétel az előző futás óta.")


if __name__ == "__main__":
    main()
