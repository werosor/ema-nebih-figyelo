"""
NÉBIH ATIportal - Magyarországon engedélyezett állatgyógyászati készítmények
letöltő script.

Forrás: https://atiportal.nebih.gov.hu/moengallatgykesz.html
Az oldal JavaScripttel tölti be a táblázatot, ezért böngészőt (Playwright)
használunk, nem egyszerű HTTP-kérést.

Kimenet: CSV fájl a legfrissebb N sorral, "Eng. kiadás dátuma" szerint
csökkenő sorrendben rendezve.

==============================================================================
HASZNÁLAT (egyszeri telepítés után):

    python nebih_scraper.py

Kapcsolók:
    --days 30              csak az utolsó 30 napban kiadott új engedélyek
    --top 20               a legfrissebb 20 sor (alapértelmezés, ha nincs --days)
    --output new_nebih.csv kimeneti fájlnév (alapértelmezés: nebih_latest.csv)
    --headful              látható böngészőablakkal fut (hibakereséshez)

TELEPÍTÉS (egyszer kell):

    pip install playwright
    playwright install chromium

==============================================================================
"""

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = "https://atiportal.nebih.gov.hu/moengallatgykesz.html"

# A táblázat oszlopai (ahogy a NÉBIH jeleníti meg, balról jobbra).
# Ha változna, itt kell módosítani.
COLUMNS = [
    "Terméknév",
    "Hatóanyag",
    "Forgalomba hozatali engedély jogosultja",
    "Nyilvántartási szám",
    "Eng. kiadás dátuma",
    "Eng. érvényessége",
    "Forgalmazhatóság",
    "Kiadhatóság",
]


def parse_hu_date(s: str) -> dt.date | None:
    """Kezeli a 2026.04.19, 2026-04-19, 2026/04/19 formátumokat."""
    if not s:
        return None
    s = s.strip().rstrip(".")
    for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%Y. %m. %d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Néha "2026. 04. 19." formában jön
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def scrape(headless: bool = True) -> list[dict]:
    """Megnyitja az oldalt, kiolvassa a táblázat összes sorát."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="hu-HU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60_000)

        # Próbáljuk megvárni, míg a táblázat ténylegesen feltöltődik adatokkal.
        # A NÉBIH oldal DataTables-szerű tábla, a sorok <tr> elemek egy <tbody>-ban.
        try:
            page.wait_for_selector("table tbody tr", timeout=30_000)
        except PWTimeout:
            print("HIBA: a táblázat 30 másodperc alatt sem töltődött be.",
                  file=sys.stderr)
            browser.close()
            return []

        # Ha van lapozó, állítsuk "összes megjelenítése" módra, hogy egyszerre
        # lássuk az összes sort. A DataTables szabványos selectje általában:
        #   <select name="DataTables_Table_0_length">
        try:
            select = page.locator("select[name$='_length']").first
            if select.count() > 0:
                # Próbáljuk a legnagyobb értéket ("All" vagy -1) kiválasztani
                options = select.locator("option").all_text_contents()
                if options:
                    # prefer "All"/"Mind" különben a legnagyobb szám
                    target = None
                    for o in options:
                        if o.strip().lower() in ("all", "mind", "összes"):
                            target = o
                            break
                    if target is None:
                        # legnagyobb számértékű opció
                        numeric = [(int(re.sub(r"\D", "", o) or 0), o)
                                   for o in options]
                        target = max(numeric)[1]
                    select.select_option(label=target)
                    page.wait_for_timeout(1500)
        except Exception:
            pass  # ha nincs lapozó, nem baj

        # Gyűjtsük be az összes sort. Ha mégis több lap van, lapozzunk.
        rows: list[dict] = []
        page_num = 0
        while True:
            page_num += 1
            trs = page.locator("table tbody tr")
            count = trs.count()
            for i in range(count):
                cells = trs.nth(i).locator("td").all_text_contents()
                cells = [c.strip() for c in cells]
                if not any(cells):
                    continue
                # Ha több oszlop van a vártnál, az első N-et vesszük
                row = {
                    COLUMNS[j]: (cells[j] if j < len(cells) else "")
                    for j in range(len(COLUMNS))
                }
                rows.append(row)

            # Van "Következő" gomb, ami nincs letiltva?
            next_btn = page.locator(
                "a.paginate_button.next:not(.disabled), "
                "button.paginate_button.next:not([disabled]), "
                "a.next:not(.disabled)"
            ).first
            if next_btn.count() == 0 or page_num > 200:
                break
            try:
                next_btn.click()
                page.wait_for_timeout(1200)
            except Exception:
                break

        browser.close()
        return rows


def filter_and_sort(rows: list[dict], top: int | None, days: int | None):
    # Rendezés dátum szerint csökkenőbe. Ismeretlen dátum a végére.
    rows_with_date = [
        (parse_hu_date(r.get("Eng. kiadás dátuma", "")), r) for r in rows
    ]
    rows_with_date.sort(
        key=lambda x: (x[0] is None, -(x[0].toordinal() if x[0] else 0))
    )

    if days is not None:
        cutoff = dt.date.today() - dt.timedelta(days=days)
        rows_with_date = [(d, r) for (d, r) in rows_with_date
                          if d is not None and d >= cutoff]

    if top is not None:
        rows_with_date = rows_with_date[:top]

    return [r for _, r in rows_with_date]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None,
                    help="Csak az utolsó N napban kiadott engedélyek")
    ap.add_argument("--top", type=int, default=20,
                    help="Legfrissebb N sor (ha nincs --days)")
    ap.add_argument("--output", default="nebih_latest.csv")
    ap.add_argument("--headful", action="store_true",
                    help="Látható böngésző (hibakereséshez)")
    args = ap.parse_args()

    print(f"Oldal betöltése: {URL}")
    all_rows = scrape(headless=not args.headful)
    print(f"  Összes sor: {len(all_rows)}")

    top = None if args.days else args.top
    result = filter_and_sort(all_rows, top=top, days=args.days)
    print(f"  Kimeneti sorok: {len(result)}")

    out = Path(args.output)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in result:
            w.writerow(r)
    print(f"Kész: {out.resolve()}")


if __name__ == "__main__":
    main()
