#!/usr/bin/env python3
"""
raport_na_zadanie.py — Generuje raport dla wybranego roku/miesiaca/podatku
i wysyla go mailem. Uruchamiany recznie przez GitHub Actions (workflow_dispatch)
na zadanie uzytkownika z aplikacji Streamlit.

Wymagane zmienne srodowiskowe:
  SUPABASE_HOST, SUPABASE_PORT, SUPABASE_DB, SUPABASE_USER, SUPABASE_PASSWORD
  GMAIL_ADRES, GMAIL_HASLO_APLIKACJI, EMAIL_ODBIORCA

Parametry (argumenty CLI):
  --rok       np. 2026
  --miesiac   np. 1 (styczen) .. 12 (grudzien)
  --podatek   PIT | CIT | VAT | AKCYZA | WSZYSTKIE

Przyklad:
  python raport_na_zadanie.py --rok 2026 --miesiac 1 --podatek VAT
  python raport_na_zadanie.py --rok 2026 --miesiac 2 --podatek WSZYSTKIE
"""

import os
import sys
import argparse

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


def main(rok: int, miesiac: int, podatek: str):
    print("=" * 70)
    print(f"PickPivot — Raport na zadanie: {podatek} / {miesiac:02d}.{rok}")
    print("=" * 70)

    data_od, data_do, opis_okresu = silnik.zakres_z_roku_miesiaca(rok, miesiac)
    print(f"Zakres: {data_od.date()} — {data_do.date()} ({opis_okresu})")

    config = _wczytaj_config_supabase()
    db = db_core.SupabaseDB(config)
    db.inicjalizuj_schemat()
    print("Polaczenie z Supabase OK.")

    podatki_do_przetworzenia = (
        silnik.PODATKI_WSZYSTKIE if podatek == "WSZYSTKIE" else [podatek]
    )

    wyniki = []
    for pod in podatki_do_przetworzenia:
        print(f"\n--- {pod} ---")
        wynik = silnik.generuj_raport_dla_podatku(
            db, pod, data_od, data_do, opis_okresu, log_fn=print
        )
        wyniki.append(wynik)
        print(f"[{pod}] Status: {wynik['status']} | Dokumentow: {wynik['liczba_dok']}")

    # Email
    gmail_adres = os.environ.get("GMAIL_ADRES")
    gmail_haslo = os.environ.get("GMAIL_HASLO_APLIKACJI")
    odbiorca    = os.environ.get("EMAIL_ODBIORCA", gmail_adres)

    if not gmail_adres or not gmail_haslo:
        print("\nBrak konfiguracji email — pomijam wysylke.")
    else:
        ktoregokolwiek_ma_plik = any(w["plik_bytes"] for w in wyniki)
        if not ktoregokolwiek_ma_plik:
            print("\nBrak dokumentow dla zadnego z wybranych podatkow — pomijam wysylke maila.")
        elif len(wyniki) == 1:
            w = wyniki[0]
            if w["plik_bytes"]:
                nazwa = f"Raport_{w['podatek']}_{opis_okresu.replace(' ', '_')}.docx"
                silnik.wyslij_email_z_zalacznikiem(
                    w["plik_bytes"], nazwa, w["podatek"], opis_okresu, w["liczba_dok"],
                    gmail_adres, gmail_haslo, odbiorca, log_fn=print,
                )
            else:
                print(f"Brak dokumentow dla {w['podatek']} w tym okresie — pomijam wysylke.")
        else:
            silnik.wyslij_email_podsumowanie_wielu(
                wyniki, opis_okresu, gmail_adres, gmail_haslo, odbiorca, log_fn=print,
            )

    print("\n" + "=" * 70)
    print("PODSUMOWANIE:")
    for w in wyniki:
        print(f"  {w['podatek']}: {w['liczba_dok']} dokumentow (status: {w['status']})")
    print("=" * 70)

    bledy = [w for w in wyniki if w["status"] == "ERROR"]
    if bledy:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rok", type=int, required=True)
    parser.add_argument("--miesiac", type=int, required=True, choices=range(1, 13))
    parser.add_argument(
        "--podatek", type=str, required=True,
        choices=["PIT", "CIT", "VAT", "AKCYZA", "WSZYSTKIE"]
    )
    args = parser.parse_args()
    main(rok=args.rok, miesiac=args.miesiac, podatek=args.podatek)
