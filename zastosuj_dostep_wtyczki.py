# -*- coding: utf-8 -*-
"""
Dodaje moduł „Dostęp wtyczki” do aplikacji Skaner Doradca.

CO ROBI
    Nanosi trzy zmiany na Twoje pliki: pozycję w menu i wpis routingu
    w `app.py` oraz uprawnienie w `auth.py`. Reszta plików zostaje nietknięta.

DLACZEGO SKRYPT, A NIE GOTOWE PLIKI
    `app.py` zawiera logo zapisane w base64 — kilka tysięcy znaków, których
    przepisanie ręcznie prosi się o literówkę w miejscu, gdzie nikt jej nie
    zauważy. Skrypt zmienia wyłącznie te trzy fragmenty, o które chodzi.

KAŻDA ZMIANA JEST SPRAWDZANA
    Przed zapisem skrypt upewnia się, że znalazł miejsce wstawienia. Podmiana
    tekstu, która nie trafiła, przechodzi bez błędu i zostawia plik pozornie
    poprawny — ten sam mechanizm zjadł mi już raz cały blok kodu. Dlatego
    tutaj brak dopasowania zatrzymuje pracę i mówi, czego zabrakło.

URUCHOMIENIE
    Skopiuj do katalogu z `app.py`, potem:
        python zastosuj_dostep_wtyczki.py

    Kopie zapasowe trafiają do `app.py.przed_dostepem` i `auth.py.przed_dostepem`.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

NUMER = "13"
NAZWA = "Dostęp wtyczki"
PLIK_MODULU = "dostep_wtyczki"
FUNKCJA = "pokaz_dostep_wtyczki"


class Przerwij(Exception):
    """Zmiana nie mogła zostać naniesiona — plik zostaje bez zmian."""


def wstaw_po(tresc: str, kotwica: str, dodatek: str, opis: str) -> str:
    """Wstawia `dodatek` tuż po `kotwicy`. Brak kotwicy przerywa pracę."""
    if kotwica not in tresc:
        raise Przerwij(
            f"Nie znalazłem miejsca na {opis}.\n"
            f"Szukałem wiersza:\n    {kotwica.strip()}\n"
            "Plik wygląda inaczej, niż zakładam — nic nie zmieniam."
        )
    return tresc.replace(kotwica, kotwica + dodatek, 1)


def juz_jest(tresc: str, wzorzec: str) -> bool:
    return wzorzec in tresc


# ---------------------------------------------------------------------------
# APP.PY
# ---------------------------------------------------------------------------

def popraw_app(sciezka: Path) -> str:
    tresc = sciezka.read_text(encoding="utf-8")

    if juz_jest(tresc, f'("{NUMER}", "{NAZWA}")'):
        print("  app.py — pozycja w menu już jest, pomijam.")
    else:
        # Pozycja w menu. Kotwicą jest ostatni wpis listy `_MODULY`.
        tresc = wstaw_po(
            tresc,
            '    ("11", "Ustawienia Systemu"),\n',
            f'    ("{NUMER}", "{NAZWA}"),\n',
            "pozycję w menu (_MODULY)",
        )
        print("  app.py — dodano pozycję w menu.")

    if juz_jest(tresc, f'"{NUMER}": ("{PLIK_MODULU}"'):
        print("  app.py — wpis routingu już jest, pomijam.")
    else:
        # Routing. Moduł ładuje się leniwie, więc importu NIE dodajemy —
        # `_modul()` zrobi to dopiero przy wejściu w zakładkę.
        tresc = wstaw_po(
            tresc,
            '    "11": ("ustawienia_systemu", "pokaz_ustawienia"),\n',
            f'    "{NUMER}": ("{PLIK_MODULU}", "{FUNKCJA}"),\n',
            "wpis routingu (_WEJSCIA)",
        )
        print("  app.py — dodano wpis routingu.")

    return tresc


# ---------------------------------------------------------------------------
# AUTH.PY
# ---------------------------------------------------------------------------

def popraw_auth(sciezka: Path) -> str:
    tresc = sciezka.read_text(encoding="utf-8")

    if juz_jest(tresc, f'"{NUMER}":'):
        print("  auth.py — uprawnienie już jest, pomijam.")
        return tresc

    # Uprawnienie dla KAŻDEGO zalogowanego: każdy paruje własną wtyczkę.
    # Ograniczenie do administratora uczyniłoby moduł bezużytecznym dla osób,
    # które mają z wtyczki korzystać.
    dodatek = f'    "{NUMER}": {{"admin", "user"}},   # {NAZWA}\n'

    # Kotwicą jest ostatni wpis mapy uprawnień. Szukamy kilku możliwych
    # postaci, bo formatowanie tego wiersza mogło się różnić.
    for kotwica in (
        '    "11": {"admin"},',
        "    '11': {'admin'},",
        '    "11":{"admin"},',
    ):
        pozycja = tresc.find(kotwica)
        if pozycja < 0:
            continue
        koniec = tresc.find("\n", pozycja) + 1
        print("  auth.py — dodano uprawnienie.")
        return tresc[:koniec] + dodatek + tresc[koniec:]

    raise Przerwij(
        "Nie znalazłem mapy uprawnień w auth.py.\n"
        'Szukałem wiersza zaczynającego się od:  "11": {"admin"}\n'
        f'Dopisz ręcznie w słowniku UPRAWNIENIA:\n{dodatek}'
    )


# ---------------------------------------------------------------------------

def main() -> int:
    katalog = Path(__file__).resolve().parent
    app = katalog / "app.py"
    auth = katalog / "auth.py"

    for plik in (app, auth):
        if not plik.exists():
            print(f"Nie widzę pliku {plik.name} w tym katalogu.")
            print("Skopiuj skrypt tam, gdzie leży app.py, i uruchom ponownie.")
            return 1

    if not (katalog / f"{PLIK_MODULU}.py").exists():
        print(f"Uwaga: brakuje pliku {PLIK_MODULU}.py w tym katalogu.")
        print("Zmiany naniosę, ale moduł nie zadziała, dopóki go nie dodasz.")
        print()

    try:
        nowy_app = popraw_app(app)
        nowy_auth = popraw_auth(auth)
    except Przerwij as e:
        print()
        print("PRZERWANO — pliki są nietknięte.")
        print()
        print(e)
        return 1

    # Zapis dopiero po udanym naniesieniu OBU zmian. Gdyby druga zawiodła,
    # plik pierwszy zostałby zmieniony bez drugiego i aplikacja pokazałaby
    # pozycję w menu, w którą nie da się wejść.
    shutil.copy2(app, app.with_suffix(".py.przed_dostepem"))
    shutil.copy2(auth, auth.with_suffix(".py.przed_dostepem"))

    app.write_text(nowy_app, encoding="utf-8")
    auth.write_text(nowy_auth, encoding="utf-8")

    print()
    print("Gotowe. Kopie zapasowe: app.py.przed_dostepem, auth.py.przed_dostepem")
    print()
    print("Sprawdź jeszcze:")
    print("  python -c \"import ast;[ast.parse(open(f,encoding='utf-8').read())"
          " for f in ('app.py','auth.py')];print('składnia OK')\"")
    print()
    print("Potem commit i push — w menu pojawi się „Dostęp wtyczki”.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
