"""
send_report.py — EMA + NÉBIH eredmények emailben.
Ezt a GitHub Actions futtatja automatikusan a két scraper után.
"""

import csv
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


def read_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build_html(new_ema: list[dict], nebih: list[dict]) -> str:
    # EMA szekció
    if new_ema:
        ema_rows = "".join(
            f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>{r.get('name','')}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>"
            f"<a href='{r.get('link','')}'>link</a></td></tr>"
            for r in new_ema
        )
        ema_html = f"""
        <h2 style='color:#1a56db'>🆕 Új EMA veterinárius engedélyek ({len(new_ema)} db)</h2>
        <table style='border-collapse:collapse;width:100%'>
          <tr style='background:#f3f4f6'>
            <th style='padding:8px 12px;text-align:left'>Terméknév</th>
            <th style='padding:8px 12px;text-align:left'>Link</th>
          </tr>
          {ema_rows}
        </table>
        """
    else:
        ema_html = "<h2 style='color:#6b7280'>EMA — nincs új tétel az előző futás óta</h2>"

    # NÉBIH szekció
    if nebih:
        cols = list(nebih[0].keys())
        headers = "".join(f"<th style='padding:8px 12px;text-align:left;white-space:nowrap'>{c}</th>" for c in cols)
        nebih_rows = "".join(
            "<tr>" + "".join(
                f"<td style='padding:6px 12px;border-bottom:1px solid #eee;white-space:nowrap'>{r.get(c,'')}</td>"
                for c in cols
            ) + "</tr>"
            for r in nebih
        )
        nebih_html = f"""
        <h2 style='color:#1a56db'>🇭🇺 NÉBIH — 20 legfrissebb engedély</h2>
        <div style='overflow-x:auto'>
        <table style='border-collapse:collapse;width:100%;font-size:13px'>
          <tr style='background:#f3f4f6'>{headers}</tr>
          {nebih_rows}
        </table>
        </div>
        """
    else:
        nebih_html = "<h2 style='color:#6b7280'>NÉBIH — nem sikerült adatot lekérni</h2>"

    return f"""
    <html><body style='font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px'>
      <h1 style='color:#111'>📋 Napi állatgyógyászati engedély riport</h1>
      <p style='color:#6b7280;font-size:13px'>Automatikusan generálva — EMA + NÉBIH figyelő</p>
      <hr style='border:none;border-top:1px solid #eee;margin:20px 0'>
      {ema_html}
      <hr style='border:none;border-top:1px solid #eee;margin:20px 0'>
      {nebih_html}
      <hr style='border:none;border-top:1px solid #eee;margin:20px 0'>
      <p style='color:#9ca3af;font-size:12px'>
        EMA forrás: ec.europa.eu/health/documents/community-register<br>
        NÉBIH forrás: atiportal.nebih.gov.hu
      </p>
    </body></html>
    """


def main():
    gmail_user = os.environ["GMAIL_USER"]
    gmail_pass = os.environ["GMAIL_PASS"]
    recipient  = os.environ["RECIPIENT"]

    new_ema = read_csv("output/new_since_last_run.csv")
    nebih   = read_csv("output/nebih_latest.csv")

    html = build_html(new_ema, nebih)

    ema_status = f"{len(new_ema)} új tétel" if new_ema else "nincs új"
    subject = f"Állatgyógyászati riport — EMA ({ema_status}) + NÉBIH top 20"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = gmail_user
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(gmail_user, gmail_pass)
        s.sendmail(gmail_user, recipient, msg.as_string())

    print(f"Email elküldve → {recipient}")


if __name__ == "__main__":
    main()
