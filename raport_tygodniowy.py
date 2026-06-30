#!/usr/bin/env python3
"""
raport_tygodniowy.py — Skrypt cotygodniowy uruchamiany przez GitHub Actions.

Co robi:
  1. Wyznacza zakres poprzedniego tygodnia (poniedzialek-piatek).
  2. Dla kazdego podatku (PIT, CIT, VAT, AKCYZA) uzupelnia archiwum i generuje plik Word.
  3. Zapisuje gotowe pliki do tabeli raporty_tygodniowe w Supabase.
  4. Wysyla e-mail z podsumowaniem (inteligentnie - nie spamuje przy 20 probach/weekend).

Wymagane zmienne srodowiskowe: patrz raport_silnik.py / _wczytaj_config_supabase().

Uruchomienie lokalne (test):
  python raport_tygodniowy.py --test-tydzien 2026-01-19
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

import db_core
import raport_silnik as silnik


def _wczytaj_config_supabase() -> dict:
    url = os.environ.get("SUPABASE_URL", "")
    if url:
        return {"url": url}
    return {
        "host":     os.environ["SUPABASE_HOST"],
        "port":     os.environ.get("SUPABASE_PORT", "5432"),
        "database": os.environ.get("SUPABASE_DB", "postgres"),
        "user":     os.environ["SUPABASE_USER"],
        "password": os.environ["SUPABASE_PASSWORD"],
    }


def main(data_testowa: str = None):
    print("=" * 70)
    print("PickPivot — Cotygodniowy Raport Interpretacji Podatkowych")
    print("=" * 70)

    if data_testowa:
        dzien_ref = datetime.strptime(data_testowa, "%Y-%m-%d")
        data_od = dzien_ref - timedelta(days=dzien_ref.weekday())
        data_do = data_od + timedelta(days=4)
        klucz   = db_core.klucz_tygodnia(data_od)
    else:
        data_od, data_do, klucz = db_core.zakres_poprzedniego_tygodnia()

    opis_okresu = f"{data_od.strftime('%d.%m')} — {data_do.strftime('%d.%m.%Y')}"
    print(f"Tydzien: {klucz} | Zakres: {data_od.date()} — {data_do.date()}")

    config = _wczytaj_config_supabase()
    db = db_core.SupabaseDB(config)
    db.inicjalizuj_schemat()
    print("Polaczenie z Supabase OK.")

    wyniki_per_podatek = {}
    sukces_calosc = True
    nowych_lacznie = 0

    for podatek in silnik.PODATKI_WSZYSTKIE:
        print(f"\n--- {podatek} ---")
        wynik = silnik.generuj_raport_dla_podatku(db, podatek, data_od, data_do, opis_okresu, log_fn=print)

        wyniki_per_podatek[podatek] = wynik["liczba_dok"]
        nowych_lacznie += wynik["nowych_pobranych"]

        if wynik["status"] == "ERROR":
            sukces_calosc = False
            continue

        if wynik["plik_bytes"]:
            nazwa_pliku = f"Raport_{podatek}_{klucz}_{data_od.strftime('%Y%m%d')}-{data_do.strftime('%Y%m%d')}.docx"
            db_core.zapisz_raport_tygodniowy(
                db, tydzien_klucz=klucz,
                data_od=data_od.strftime("%Y-%m-%d"),
                data_do=data_do.strftime("%Y-%m-%d"),
                podatek=podatek,
                liczba_dok=wynik["liczba_dok"],
                plik_bytes=wynik["plik_bytes"],
                nazwa_pliku=nazwa_pliku,
            )
            print(f"[{podatek}] Zapisano raport: {nazwa_pliku} ({len(wynik['plik_bytes'])/1024:.1f} KB)")

    print("\n" + "=" * 70)
    print("PODSUMOWANIE:")
    for pod, n in wyniki_per_podatek.items():
        print(f"  {pod}: {n} dokumentow")
    print(f"  Nowych dokumentow w tej probie: {nowych_lacznie}")
    print("=" * 70)

    # ── INTELIGENTNE POMIJANIE E-MAILA (patrz komentarz w workflow .yml) ──
    teraz = datetime.now()
    jest_pierwsza_proba = (teraz.weekday() == 5 and teraz.hour < 16)
    jest_ostatnia_proba  = (teraz.weekday() == 0 and teraz.hour < 3)
    wymuszony_test = data_testowa is not None

    wyslac_email = (
        nowych_lacznie > 0 or not sukces_calosc
        or jest_pierwsza_proba or jest_ostatnia_proba or wymuszony_test
    )

    if wyslac_email:
        gmail_adres = os.environ.get("GMAIL_ADRES")
        gmail_haslo = os.environ.get("GMAIL_HASLO_APLIKACJI")
        odbiorca    = os.environ.get("EMAIL_ODBIORCA", gmail_adres)

        if gmail_adres and gmail_haslo:
            _wyslij_email_tygodniowy(wyniki_per_podatek, opis_okresu, sukces_calosc,
                                       nowych_lacznie, gmail_adres, gmail_haslo, odbiorca)
        else:
            print("Brak konfiguracji email — pomijam wysylke.")
    else:
        print("Pomijam wysylke e-mail - brak nowych dokumentow (nie pierwsza/ostatnia proba w oknie).")

    if not sukces_calosc:
        sys.exit(1)


def _wyslij_email_tygodniowy(wyniki, opis_okresu, sukces, nowych_w_probie,
                                gmail_adres, gmail_haslo, odbiorca):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    tytul_status = "✅ Sukces" if sukces else "⚠️ Zakonczono z ostrzezeniami"
    temat = f"PickPivot — Raport tygodniowy {opis_okresu} [{tytul_status}]"

    info_nowych = (
        f"<p><b>Nowych dokumentow w tej probie:</b> {nowych_w_probie}</p>"
        if nowych_w_probie > 0 else
        "<p style='color:#888;'>To podsumowanie kontrolne — brak nowych dokumentow od ostatniej proby.</p>"
    )

    tresc_html = f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>📦 PickPivot — Cotygodniowy Raport Interpretacji</h2>
    <p><b>Okres:</b> {opis_okresu} (poniedzialek-piatek)</p>
    <p><b>Status:</b> {tytul_status}</p>
    {info_nowych}
    <h3>Stan archiwum dla tego tygodnia (lacznie):</h3>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
        <tr style="background-color: #2C3E50; color: white;">
            <th>Podatek</th><th>Liczba dokumentow</th>
        </tr>
        {"".join(f'<tr><td>{pod}</td><td>{n}</td></tr>' for pod, n in wyniki.items())}
    </table>
    <p style="margin-top: 20px;">
        Pliki Word sa gotowe do pobrania w aplikacji PickPivot, w module
        <b>"Raporty Tygodniowe"</b>.
    </p>
    <p style="color: #888; font-size: 12px;">
        Wiadomosc wygenerowana automatycznie przez GitHub Actions.
    </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = temat
    msg["From"]    = gmail_adres
    msg["To"]      = odbiorca
    msg.attach(MIMEText(tresc_html, "html", "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_adres, gmail_haslo)
            server.send_message(msg)
        print(f"E-mail z podsumowaniem wyslany do {odbiorca}.")
    except Exception as e:
        print(f"BLAD wysylki email: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-tydzien", type=str, default=None)
    args = parser.parse_args()
    main(data_testowa=args.test_tydzien)
