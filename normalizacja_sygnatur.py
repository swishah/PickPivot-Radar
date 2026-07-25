# -*- coding: utf-8 -*-
"""
normalizacja_sygnatur.py — wspólna normalizacja sygnatur dokumentów.

PO CO TO ISTNIEJE
  Ta sama sygnatura zapisana w Eurece i wyświetlona w LEX to bardzo często
  DWA RÓŻNE ŁAŃCUCHY ZNAKÓW: inny myślnik, twarda spacja, miękki dywiz wstawiony
  przez przeglądarkę przy łamaniu wiersza, inna wielkość liter. Porównanie
  wprost daje wtedy CICHE PUDŁO — wtyczka powie „nie ma w archiwum”, choć jest.

  Ten moduł sprowadza sygnaturę do postaci kanonicznej, po której da się
  porównywać i indeksować.

ZASADA NADRZĘDNA
  Normalizacja może tylko USUWAĆ różnice zapisu. Nigdy nie wolno jej usuwać
  informacji ODRÓŻNIAJĄCEJ dokumenty. Dlatego np. numer wersji w sygnaturze
  KIS (…2026.**1**.AS) zostaje — dokument „.1.” i „.2.” to dwa różne pisma.

BLIŹNIACZA IMPLEMENTACJA
  Dokładnie ta sama logika istnieje w JavaScripcie (sygnatury.js) na potrzeby
  wtyczki. Rozjazd między nimi jest głównym ryzykiem tego rozwiązania, dlatego
  pilnuje go test parytetu (test_parytet.py), który przepuszcza ten sam korpus
  przez oba języki i porównuje wyniki znak po znaku.
  ZMIENIASZ TUTAJ — ZMIEŃ TAM I URUCHOM TEST PARYTETU.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# ZNAKI DO UJEDNOLICENIA
# ---------------------------------------------------------------------------

# Znaki niewidoczne: miękki dywiz (wstawiany przy łamaniu wiersza), spacje
# zerowej szerokości, znacznik kolejności bajtów. Wszystkie do usunięcia.
NIEWIDOCZNE = "\u00ad\u200b\u200c\u200d\u2060\ufeff"

# Warianty myślnika sprowadzane do zwykłego "-". Kolejno: dywiz, dywiz
# niełamliwy, kreska figurowa, półpauza, pauza, kreska pozioma, minus
# matematyczny, oraz warianty prezentacyjne i pełnej szerokości.
MYSLNIKI = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"

# Znaki interpunkcyjne obcinane z początku i końca (LEX bywa, że dokleja
# kropkę albo sygnaturę w cudzysłowie).
OBCINANE = ".,;:()[]{}\"'«»„”“’ \t"


# ---------------------------------------------------------------------------
# NORMALIZACJA GŁÓWNA
# ---------------------------------------------------------------------------
def normalizuj_sygnature(surowa: str | None) -> str:
    """
    Sprowadza sygnaturę do postaci kanonicznej.

    Kroki (kolejność ma znaczenie):
      1. NFKC — ujednolica warianty zgodności (pełna szerokość, ligatury).
      2. Usunięcie znaków niewidocznych.
      3. Ujednolicenie myślników do "-".
      4. Usunięcie WSZYSTKICH białych znaków (twarde spacje już po NFKC
         są zwykłymi spacjami).
      5. Wielkie litery.
      6. Obcięcie interpunkcji z brzegów.

    Usuwamy całą białą spację, a nie tylko ją redukujemy, bo „I FSK 1234/23”
    i „I FSK  1234/23” muszą dać ten sam klucz, a odstępy nie niosą tu żadnej
    informacji rozróżniającej.

    >>> normalizuj_sygnature("0114-KDIP2-2.4010.123.2026.1.AS")
    '0114-KDIP2-2.4010.123.2026.1.AS'
    >>> normalizuj_sygnature("  i fsk 1234/23.  ")
    'IFSK1234/23'
    """
    if not surowa:
        return ""

    t = unicodedata.normalize("NFKC", str(surowa))

    for ch in NIEWIDOCZNE:
        t = t.replace(ch, "")

    for ch in MYSLNIKI:
        t = t.replace(ch, "-")

    t = re.sub(r"\s+", "", t)
    t = t.upper()
    t = t.strip(OBCINANE)

    return t


# ---------------------------------------------------------------------------
# KLUCZ POMOCNICZY — BEZ INICJAŁÓW PRACOWNIKA KIS
# ---------------------------------------------------------------------------

# Sygnatura KIS kończy się inicjałami osoby prowadzącej sprawę, np. „.AS”,
# „.MR”, „.KO”. Bywa, że w jednym źródle ich brak albo różnią się przy
# sprostowaniu, mimo że dokument jest ten sam.
#
# Budowa sygnatury KIS — SZEŚĆ członów rozdzielonych kropkami:
#   0114-KDIP2-2 . 4010 . 123  . 2026 . 1      . AS
#   └ prefiks     └ dział └ nr    └ rok  └ wersja └ inicjały
# Prefiks zawiera myślniki, ale NIE zawiera kropek — stąd [A-Z0-9\-]+ bez kropki.
_KIS_INICJALY = re.compile(
    r"^(\d{4}-[A-Z0-9\-]+\.\d{3,4}\.\d+\.\d{4}\.\d+)\.[A-Z]{2,4}$"
)


def klucz_bez_inicjalow(znormalizowana: str) -> str:
    """
    Dla sygnatury KIS zwraca ją bez końcowych inicjałów; dla pozostałych
    zwraca wejście bez zmian.

    UWAGA: to klucz DRUGIEGO wyboru, do użycia dopiero gdy dopasowanie pełne
    zawiedzie, i zawsze z oznaczeniem, że trafienie jest przybliżone. Numer
    wersji (…2026.**1**.AS) celowo ZOSTAJE — „.1.” i „.2.” to różne pisma
    i sklejenie ich byłoby błędem merytorycznym, nie uproszczeniem.

    >>> klucz_bez_inicjalow("0114-KDIP2-2.4010.123.2026.1.AS")
    '0114-KDIP2-2.4010.123.2026.1'
    >>> klucz_bez_inicjalow("IFSK1234/23")
    'IFSK1234/23'
    """
    if not znormalizowana:
        return ""
    m = _KIS_INICJALY.match(znormalizowana)
    return m.group(1) if m else znormalizowana


# ---------------------------------------------------------------------------
# ROZPOZNANIE RODZAJU (diagnostyka, nie logika dopasowania)
# ---------------------------------------------------------------------------
_WZ_KIS = re.compile(r"^\d{4}-[A-Z]{2,6}[0-9.\-]*\.\d{3,4}\.\d+\.\d{4}\.\d+\.[A-Z]{2,4}$")
_WZ_KIS_LUZNO = re.compile(r"^\d{4}-[A-Z]")
_WZ_SAD = re.compile(r"^[IVX]{1,4}[A-ZŁŚŻĆŃÓĄĘ]{1,6}(/[A-ZŁŚŻĆŃÓĄĘ]{1,3})?\d+/\d{2,4}$")
_WZ_IZBA = re.compile(r"^[A-Z]{2,6}\d?/\d{3,4}-\d+/\d{2}(-\d+)?/[A-Z]{2,3}$")


def rodzaj_sygnatury(znormalizowana: str) -> str:
    """Zwraca 'KIS' / 'KIS?' / 'SAD' / 'IZBA' / 'NIEZNANY'. Służy wyłącznie
    do raportowania w diagnostyce — dopasowanie działa na pełnym kluczu
    niezależnie od rozpoznanego rodzaju."""
    if not znormalizowana:
        return "NIEZNANY"
    if _WZ_KIS.match(znormalizowana):
        return "KIS"
    if _WZ_SAD.match(znormalizowana):
        return "SAD"
    if _WZ_IZBA.match(znormalizowana):
        return "IZBA"
    if _WZ_KIS_LUZNO.match(znormalizowana):
        return "KIS?"
    return "NIEZNANY"


if __name__ == "__main__":
    import doctest
    wynik = doctest.testmod(verbose=False)
    print(f"doctest: {wynik.attempted} testów, niepowodzeń: {wynik.failed}")
