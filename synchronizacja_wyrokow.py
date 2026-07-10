#!/usr/bin/env python3
"""
synchronizacja_wyrokow.py — Cotygodniowa synchronizacja wyrokow sadow
administracyjnych (CBOSA) do bazy PickPivot. Uruchamiana przez GitHub Actions
w niedziele (oraz recznie: workflow_dispatch).

TRZY STRUMIENIE (sciezka C):
  1. METADANE (okno krotkie, dom. 10 dni): wszystkie nowe orzeczenia
     podatkowe — takze BEZ uzasadnienia. Baza od razu wie, ze wyrok
     istnieje (sentencja + metadane, status OCZEKUJE_NA_UZASADNIENIE).
  2. UZASADNIENIA (okno dlugie, dom. 180 dni, filtr "z uzasadnieniem"):
     dociaga pelne teksty do rekordow, ktore czekaly. Uzasadnienia
     publikowane sa czesto 2-3 miesiace po wyroku — stad dlugie okno.
  3. PRAWOMOCNOSC (okno dom. 15 miesiecy, filtr "prawomocne"):
     tylko listy wynikow (bez stron szczegolow) — oznacza w bazie
     rekordy, ktore w miedzyczasie sie uprawomocnily.

Po strumieniu 2: oznaczenie trwalych brakow (rekordy OCZEKUJACE starsze
niz ~9 miesiecy -> BEZ_UZASADNIENIA_TRWALE, nie beda juz rewizytowane).

TRYB KALIBRACJI (--kalibracja):
  Pierwsze uruchomienie na zywym CBOSA. Nie pisze NIC do bazy. Rozpoznaje
  formularz, wykonuje male zapytanie (VAT, 7 dni), pobiera 2 dokumenty
  i wypisuje wszystko, co sparsowal — do recznej weryfikacji, ze parser
  poprawnie czyta strukture strony, ZANIM zaczniemy zapisywac dane.

Zmienne srodowiskowe: SUPABASE_* oraz GMAIL_* jak w pozostalych skryptach.
"""

import os
import sys
import argparse
from datetime import datetime, timedelta

import db_core
import db_wyroki
import wyroki_cbosa as cbosa


OKNO_METADANE_DNI     = 10
OKNO_UZASADNIEN_DNI   = 180
OKNO_PRAWOMOCNE_DNI   = 455   # ~15 miesiecy


def _wczytaj_config_supabase() -> dict:
    return {
        "host":     os.environ["SUPABASE_HOST"],
        "port":     os.environ.get("SUPABASE_PORT", "5432"),
        "database": os.environ.get("SUPABASE_DB", "postgres"),
        "user":     os.environ["SUPABASE_USER"],
        "password": os.environ["SUPABASE_PASSWORD"],
    }


def _okno(dni: int):
    dz = datetime.now()
    od = dz - timedelta(days=dni)
    return od.strftime("%Y-%m-%d"), dz.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# STRUMIEN 1 — METADANE (wszystko nowe, takze bez uzasadnienia)
# ---------------------------------------------------------------------------
def strumien_metadane(db, sesja, formularz, log=print):
    od, do = _okno(OKNO_METADANE_DNI)
    log(f"\n=== STRUMIEN 1: METADANE | okno {od}..{do} ===")
    znane = db_wyroki.pobierz_id_wyrokow(db)

    for podatek, symbol in cbosa.SYMBOLE_PODATKOW.items():
        log(f"\n[{podatek}] symbol {symbol}")
        try:
            lista, total = cbosa.szukaj(sesja, formularz, symbol, od, do, log_fn=log)
        except cbosa.BladCBOSA as e:
            log(f"[{podatek}] BLAD wyszukiwania: {e}")
            db_wyroki.zapisz_historie_sync_wyrokow(db, "METADANE", od, do, podatek,
                                                    0, 0, 0, "ERROR", str(e))
            continue

        nowe_id = [i for i, _ in lista if i not in znane]
        log(f"[{podatek}] Znaleziono {len(lista)}"
            + (f" (CBOSA deklaruje {total})" if total is not None else "")
            + f", nowych do pobrania: {len(nowe_id)}")

        nowych, bledy = 0, 0
        for k, did in enumerate(nowe_id, 1):
            try:
                w = cbosa.pobierz_szczegoly(sesja, did, log_fn=None)
                db_wyroki.zapisz_wyrok(db, w)
                nowych += 1
                if k % 10 == 0 or k == len(nowe_id):
                    log(f"[{podatek}] Postep szczegolow: {k}/{len(nowe_id)}")
            except Exception as e:
                bledy += 1
                log(f"[{podatek}] Blad dokumentu {did}: {e}")

        status = "OK" if bledy == 0 else ("CZESCIOWO" if nowych else "ERROR")
        db_wyroki.zapisz_historie_sync_wyrokow(
            db, "METADANE", od, do, podatek, len(lista), nowych, 0, status,
            f"bledy dokumentow: {bledy}" if bledy else "")


# ---------------------------------------------------------------------------
# STRUMIEN 2 — UZASADNIENIA (dlugie okno, filtr "z uzasadnieniem")
# ---------------------------------------------------------------------------
def strumien_uzasadnienia(db, sesja, formularz, log=print):
    od, do = _okno(OKNO_UZASADNIEN_DNI)
    log(f"\n=== STRUMIEN 2: UZASADNIENIA | okno {od}..{do} (filtr: z uzasadnieniem) ===")
    znane = db_wyroki.pobierz_id_wyrokow(db)

    for podatek, symbol in cbosa.SYMBOLE_PODATKOW.items():
        log(f"\n[{podatek}] symbol {symbol}")
        try:
            lista, total = cbosa.szukaj(sesja, formularz, symbol, od, do,
                                        tylko_z_uzasadnieniem=True, log_fn=log)
        except cbosa.BladCBOSA as e:
            log(f"[{podatek}] BLAD wyszukiwania: {e}")
            db_wyroki.zapisz_historie_sync_wyrokow(db, "UZASADNIENIA", od, do, podatek,
                                                    0, 0, 0, "ERROR", str(e))
            continue

        # Rewizytujemy TYLKO to, co nie jest jeszcze KOMPLETNE w bazie —
        # dzieki temu cotygodniowy koszt to glownie listy, nie tysiace
        # stron szczegolow.
        do_pobrania = [i for i, _ in lista
                       if znane.get(i) != db_wyroki.STATUS_KOMPLETNY]
        log(f"[{podatek}] Z uzasadnieniem w oknie: {len(lista)}"
            + (f" (CBOSA: {total})" if total is not None else "")
            + f", wymaga pobrania/aktualizacji: {len(do_pobrania)}")

        nowych, zaktual, bledy = 0, 0, 0
        for k, did in enumerate(do_pobrania, 1):
            try:
                w = cbosa.pobierz_szczegoly(sesja, did, log_fn=None)
                wynik = db_wyroki.zapisz_wyrok(db, w)
                if wynik == "NOWY":
                    nowych += 1
                else:
                    zaktual += 1
                if k % 10 == 0 or k == len(do_pobrania):
                    log(f"[{podatek}] Postep: {k}/{len(do_pobrania)}")
            except Exception as e:
                bledy += 1
                log(f"[{podatek}] Blad dokumentu {did}: {e}")

        status = "OK" if bledy == 0 else ("CZESCIOWO" if (nowych + zaktual) else "ERROR")
        db_wyroki.zapisz_historie_sync_wyrokow(
            db, "UZASADNIENIA", od, do, podatek, len(lista), nowych, zaktual, status,
            f"bledy dokumentow: {bledy}" if bledy else "")

    trwale = db_wyroki.oznacz_trwale_braki(db)
    if trwale:
        log(f"\nOznaczono {trwale} rekordow jako BEZ_UZASADNIENIA_TRWALE "
            f"(starsze niz {db_wyroki.DNI_DO_TRWALEGO_BRAKU} dni, nadal bez uzasadnienia).")


# ---------------------------------------------------------------------------
# STRUMIEN 3 — PRAWOMOCNOSC (tylko listy, zero stron szczegolow)
# ---------------------------------------------------------------------------
def strumien_prawomocnosc(db, sesja, formularz, log=print):
    od, do = _okno(OKNO_PRAWOMOCNE_DNI)
    log(f"\n=== STRUMIEN 3: PRAWOMOCNOSC | okno {od}..{do} (filtr: prawomocne, tylko listy) ===")

    for podatek, symbol in cbosa.SYMBOLE_PODATKOW.items():
        try:
            lista, total = cbosa.szukaj(sesja, formularz, symbol, od, do,
                                        tylko_prawomocne=True, log_fn=log)
        except cbosa.BladCBOSA as e:
            log(f"[{podatek}] BLAD wyszukiwania: {e}")
            db_wyroki.zapisz_historie_sync_wyrokow(db, "PRAWOMOCNOSC", od, do, podatek,
                                                    0, 0, 0, "ERROR", str(e))
            continue
        ids = [i for i, _ in lista]
        zmienione = db_wyroki.oznacz_prawomocne(db, ids)
        log(f"[{podatek}] Prawomocnych w oknie: {len(ids)}, nowo oznaczonych w bazie: {zmienione}")
        db_wyroki.zapisz_historie_sync_wyrokow(
            db, "PRAWOMOCNOSC", od, do, podatek, len(ids), 0, zmienione, "OK", "")


# ---------------------------------------------------------------------------
# KALIBRACJA — pierwsze uruchomienie, zero zapisu do bazy
# ---------------------------------------------------------------------------
def kalibracja(log=print):
    log("=" * 70)
    log("TRYB KALIBRACJI — nic nie zapisuje do bazy")
    log("=" * 70)
    sesja = cbosa.nowa_sesja()

    log("\n1) Rozpoznawanie formularza...")
    formularz = cbosa.poznaj_formularz(sesja, log_fn=log)
    log("   Klucze pomocy z polami: " + ", ".join(sorted(formularz["pola"].keys())))
    for klucz in ("symbole", "data_orzeczenia", "z_uzasadnieniem", "s_prawomocne"):
        pola = formularz["pola"].get(klucz, [])
        log(f"   - {klucz}: " + (", ".join(f"{p['name']}({p['type']})" for p in pola) or "NIE ZNALEZIONO!"))

    od, do = _okno(7)
    log(f"\n2) Testowe zapytanie: VAT (6110), okno {od}..{do}...")
    lista, total = cbosa.szukaj(sesja, formularz, "6110", od, do, log_fn=log)
    log(f"   Wynikow: {len(lista)}" + (f", CBOSA deklaruje: {total}" if total is not None else ", (nie odczytano licznika)"))
    for did, tytul in lista[:5]:
        log(f"   - {did}: {tytul}")

    if lista:
        log("\n3) Parsowanie 2 przykladowych dokumentow...")
        for did, _ in lista[:2]:
            w = cbosa.pobierz_szczegoly(sesja, did, log_fn=log)
            log(f"\n   /doc/{did}:")
            for pole in ("sygnatura", "rodzaj", "sad", "data_orzeczenia", "podatek",
                          "symbole", "prawomocny", "status_tresci", "tresc_wyniku"):
                log(f"     {pole}: {w.get(pole)}")
            log(f"     sentencja: {len(w.get('sentencja',''))} znakow | "
                f"uzasadnienie: {len(w.get('uzasadnienie',''))} znakow")
            log(f"     poczatek sentencji: {w.get('sentencja','')[:180]}")
    log("\nKALIBRACJA ZAKONCZONA. Sprawdz powyzsze dane — jesli sygnatury, daty,")
    log("podatek i dlugosci tekstow wygladaja sensownie, mozna uruchomic tryb pelny.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kalibracja", action="store_true",
                        help="Tylko test parsera na zywym CBOSA, bez zapisu do bazy")
    parser.add_argument("--strumienie", default="1,2,3",
                        help="Ktore strumienie uruchomic, np. '1,2' (dom. wszystkie)")
    args = parser.parse_args()

    if args.kalibracja:
        kalibracja()
        return

    print("=" * 70)
    print("PickPivot — Synchronizacja Wyrokow CBOSA")
    print("=" * 70)

    db = db_core.SupabaseDB(_wczytaj_config_supabase())
    db_wyroki.inicjalizuj_schemat_wyrokow(db)
    print("Polaczenie z Supabase OK, schemat wyrokow gotowy.")

    sesja = cbosa.nowa_sesja()
    formularz = cbosa.poznaj_formularz(sesja, log_fn=print)

    wybrane = {s.strip() for s in args.strumienie.split(",")}
    try:
        if "1" in wybrane:
            strumien_metadane(db, sesja, formularz)
        if "2" in wybrane:
            strumien_uzasadnienia(db, sesja, formularz)
        if "3" in wybrane:
            strumien_prawomocnosc(db, sesja, formularz)
    except Exception as e:
        print(f"\nBLAD KRYTYCZNY: {e}")
        sys.exit(1)

    print("\n" + "=" * 70)
    stats = db_wyroki.statystyki_wyrokow(db)
    print(f"PODSUMOWANIE: {stats['total']} wyrokow w bazie")
    for p in stats["per_podatek"]:
        print(f"  {p['podatek'] or '(bez podatku)'}: {p['liczba']} "
              f"(kompletne: {p['kompletne']}, oczekujace: {p['oczekujace']}, "
              f"prawomocne: {p['prawomocne']})")
    print("=" * 70)


if __name__ == "__main__":
    main()
