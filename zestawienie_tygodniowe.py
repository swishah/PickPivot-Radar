# -*- coding: utf-8 -*-
"""
MODUŁ: Zestawienie Tygodniowe — Skaner Doradca
===============================================================================
Cztery zakładki (PIT / CIT / VAT / AKCYZA), każda z listą tygodni (pn–pt).
Po wyborze tygodnia moduł pokazuje interpretacje z bazy oraz generuje gotowy
prompt do wklejenia w GPT "Zestawienie Tygodniowe" / DorAIdca 2.0.

LOGIKA PRZYPISANIA DO TYGODNIA:
  * Dokument należy PIERWOTNIE do tygodnia swojej daty wydania (data_wyd).
  * Jeżeli dokument został ŚCIĄGNIĘTY do bazy w innym (późniejszym) tygodniu
    niż tydzień wydania (typowy przypadek: MF publikuje wstecznie, a dzienna
    synchronizacja z oknem 10 dni dogania to np. we wtorek kolejnego tygodnia),
    to dokument pojawia się DODATKOWO w tygodniu ściągnięcia — z datą wydania
    oznaczoną na ZIELONO, jako sygnał: "to nie jest interpretacja z tego
    tygodnia, tylko dograna w tym tygodniu do bazy".
  * W tygodniu wydania taki dokument widnieje normalnie, bez oznaczenia.

WYMAGANIE: kolumna dokumenty.pobrano_at (patrz migracja_pobrano_at.sql).
===============================================================================
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pg8000.dbapi
import streamlit as st

# ---------------------------------------------------------------------------
# KONFIGURACJA
# ---------------------------------------------------------------------------
PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]

# Kolory spójne z paleta.py (zieleń logo #386520). Jeśli wolisz importować:
#   from paleta import ZIELEN_GLOWNA
ZIELEN_GLOWNA = "#386520"
ZIELEN_TLO = "#dcefd8"      # jasne tło komórki dla wiersza "spóźnionego"
ZIELEN_TEKST = "#1b3d0f"    # ciemna zieleń tekstu na jasnym tle

MAKS_TYGODNI_NA_LISCIE = 104  # 2 lata wstecz; zwiększ, gdy backfill urośnie


# ---------------------------------------------------------------------------
# POŁĄCZENIE Z BAZĄ
# ---------------------------------------------------------------------------
# Sekrety czytamy DOKŁADNIE tak, jak reszta aplikacji (archiwum_supabase.py):
# sekcja [supabase] z kluczami host / port / database / user / password.
# Host to session pooler Supabase (aws-...-pooler.supabase.com, port 5432),
# który wymaga SSL — dlatego łączymy się z kontekstem SSL, a gdyby dany
# endpoint go nie egzekwował, spadamy na połączenie bez SSL (fallback).
#
# To osobne, lekkie połączenie obok db_core.SupabaseDB — moduł potrzebuje
# własnego zapytania SQL (logika tygodni z pobrano_at), którego db_core
# nie udostępnia jako gotowej metody. Dodatkowe połączenie przez pooler
# jest bezpieczne (pooler obsługuje wiele równoległych sesji).
@st.cache_resource(show_spinner=False)
def _polacz():
    import ssl

    cfg = dict(st.secrets["supabase"])
    parametry = dict(
        host=cfg["host"],
        port=int(cfg.get("port", 5432)),
        database=cfg.get("database", cfg.get("dbname", "postgres")),
        user=cfg["user"],
        password=cfg["password"],
    )
    try:
        ctx = ssl.create_default_context()
        return pg8000.dbapi.connect(ssl_context=ctx, **parametry)
    except Exception:
        # Endpoint nie wymaga/nie akceptuje SSL — próba bez kontekstu.
        return pg8000.dbapi.connect(**parametry)


def _zapytaj(sql: str, parametry: tuple = ()) -> list[tuple]:
    """Wykonuje SELECT; przy zerwanym połączeniu czyści cache i ponawia raz."""
    for proba in (1, 2):
        conn = _polacz()
        try:
            kur = conn.cursor()
            kur.execute(sql, parametry)
            wynik = kur.fetchall()
            kur.close()
            return wynik
        except Exception:
            _polacz.clear()
            if proba == 2:
                raise
    return []


# ---------------------------------------------------------------------------
# TYGODNIE (pn–pt)
# ---------------------------------------------------------------------------
def _poniedzialek(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _granice_tygodnia(pon: dt.date) -> tuple[dt.date, dt.date, dt.date]:
    """Zwraca (poniedziałek, piątek, niedziela) danego tygodnia.

    Etykieta i zakres merytoryczny to pn–pt, ale technicznie tydzień
    domykamy niedzielą — żeby dokument z datą wydania przypadającą
    (wyjątkowo) na weekend nie wypadł z żadnego kubełka.
    """
    return pon, pon + dt.timedelta(days=4), pon + dt.timedelta(days=6)


def _etykieta_tygodnia(pon: dt.date) -> str:
    _, pt, _ = _granice_tygodnia(pon)
    nr = pon.isocalendar()[1]
    return f"Tydzień {nr:02d}/{pon.year}  ·  {pon:%d.%m} – {pt:%d.%m.%Y} (pn–pt)"


@st.cache_data(ttl=3600, show_spinner=False)
def _lista_tygodni() -> list[dt.date]:
    """Lista poniedziałków: od bieżącego tygodnia wstecz do najstarszego
    dokumentu w bazie (z limitem MAKS_TYGODNI_NA_LISCIE)."""
    dzis = dt.date.today()
    biezacy = _poniedzialek(dzis)

    najstarsza = biezacy
    try:
        w = _zapytaj(
            "SELECT MIN(NULLIF(data_wyd, '')) FROM dokumenty "
            "WHERE podatek = ANY(%s)",
            (PODATKI,),
        )
        if w and w[0][0]:
            najstarsza = _poniedzialek(dt.date.fromisoformat(str(w[0][0])[:10]))
    except Exception:
        pass  # brak danych — pokażemy sam bieżący tydzień

    tygodnie: list[dt.date] = []
    pon = biezacy
    while pon >= najstarsza and len(tygodnie) < MAKS_TYGODNI_NA_LISCIE:
        tygodnie.append(pon)
        pon -= dt.timedelta(days=7)
    return tygodnie


# ---------------------------------------------------------------------------
# DANE TYGODNIA
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _dokumenty_tygodnia(podatek: str, pon_iso: str, nie_iso: str) -> pd.DataFrame:
    """Zwraca dokumenty widoczne w danym tygodniu:

    A) wydane w tym tygodniu (data_wyd w [pon, nie])            — bez oznaczenia
    B) ściągnięte w tym tygodniu, ale wydane WCZEŚNIEJ           — oznaczone
       (pobrano_at w [pon, nie] AND data_wyd < pon)

    data_wyd jest TEXT-em ISO (YYYY-MM-DD), więc porównania tekstowe są
    poprawne leksykograficznie — ta sama konwencja co w search_interpretacje.
    """
    # Filtr po dacie pobrania używa PÓŁOTWARTEGO zakresu na kolumnie
    # timestamptz: pobrano_at >= poniedziałek AND pobrano_at < niedziela+1.
    # Dzięki temu korzysta z indeksu idx_d_pobrano (zwykła kolumna, bez
    # rzutowania ::date, które łamie IMMUTABLE) i obejmuje cały ostatni dzień.
    sql = """
        SELECT id,
               sygnatura,
               data_wyd,
               COALESCE(to_char(pobrano_at, 'YYYY-MM-DD'), data_wyd) AS pobrano_dnia,
               link
        FROM dokumenty
        WHERE podatek = %s
          AND NULLIF(data_wyd, '') IS NOT NULL
          AND (
                (data_wyd >= %s AND data_wyd <= %s)
             OR (pobrano_at >= %s::date
                 AND pobrano_at <  (%s::date + 1)
                 AND data_wyd < %s)
              )
        ORDER BY data_wyd, sygnatura
    """
    wiersze = _zapytaj(sql, (podatek, pon_iso, nie_iso, pon_iso, nie_iso, pon_iso))
    df = pd.DataFrame(
        wiersze, columns=["id", "sygnatura", "data_wyd", "pobrano_dnia", "link"]
    )
    if df.empty:
        df["spozniona"] = pd.Series(dtype=bool)
        return df

    # "Spóźniona" W TYM WIDOKU = wydana przed poniedziałkiem wybranego tygodnia
    # (czyli obecna tu wyłącznie dlatego, że w tym tygodniu trafiła do bazy).
    df["spozniona"] = df["data_wyd"] < pon_iso
    return df


# ---------------------------------------------------------------------------
# PROMPT DO GPT / DorAIdca
# ---------------------------------------------------------------------------
def _zbuduj_prompt(podatek: str, pon: dt.date, df: pd.DataFrame) -> str:
    _, pt, _ = _granice_tygodnia(pon)
    okres = f"{pon:%d.%m}–{pt:%d.%m.%Y}"

    pozycje = []
    for i, w in enumerate(df.itertuples(index=False), start=1):
        dopisek = ""
        if w.spozniona:
            dopisek = (
                f" | UWAGA: wydana {_fmt(w.data_wyd)}, do bazy trafiła "
                f"{_fmt(w.pobrano_dnia)} — w tabeli dodaj adnotację "
                f"„publikacja opóźniona”"
            )
        pozycje.append(
            f"{i}. Sygnatura: {w.sygnatura} | data wydania: {_fmt(w.data_wyd)} "
            f"| ID: {w.id}{dopisek}"
        )
    lista = "\n".join(pozycje) if pozycje else "(brak pozycji)"

    return f"""Przygotuj zestawienie tygodniowe interpretacji indywidualnych — {podatek}, okres {okres} (poniedziałek–piątek).

W bazie Skaner Doradca znajduje się dokładnie {len(df)} interpretacji przypisanych do tego okresu. Przetwórz WYŁĄCZNIE poniższe pozycje — nie wyszukuj niczego ponad tę listę i niczego z niej nie pomijaj:

{lista}

Dla KAŻDEJ pozycji pobierz pełną treść z bazy po podanym ID (akcja pobierz_pelny / funkcja pobierz_interpretacje_pelna).

Efektem ma być JEDNA tabela (bez wstępu, rekomendacji i listy źródeł) z kolumnami, w tej kolejności:
1. Podatek — {podatek}
2. Sygnatura (znak pisma)
3. Data wydania — DD.MM.RRRR (przy pozycjach z adnotacją „publikacja opóźniona” dopisz tę adnotację w nawiasie za datą)
4. Streszczenie — ciągła proza (NIE lista punktowana), maksymalnie 15 zdań: stan faktyczny lub zdarzenie przyszłe, pytanie podatnika, stanowisko podatnika, stanowisko organu z kluczowym uzasadnieniem.

Wiersze sortuj chronologicznie od najstarszej daty wydania. Pod tabelą jedno zdanie: „Zestawienie obejmuje {len(df)} interpretacji z bazy Skaner Doradca dla {podatek} w okresie {okres}.”

Zakazy: nie korzystaj z żadnego źródła poza bazą (ani internet, ani pamięć); nie streszczaj interpretacji, której pełnej treści nie udało się pobrać — zamiast tego wpisz w komórce „nie udało się pobrać treści — zweryfikuj ręcznie”."""


def _fmt(iso: str) -> str:
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%d.%m.%Y")
    except Exception:
        return str(iso)


# ---------------------------------------------------------------------------
# RENDER JEDNEJ ZAKŁADKI
# ---------------------------------------------------------------------------
def _zakladka(podatek: str, tygodnie: list[dt.date]) -> None:
    if not tygodnie:
        st.info("Brak danych w bazie — lista tygodni jest pusta.")
        return

    pon = st.selectbox(
        "Okres zestawienia",
        options=tygodnie,
        format_func=_etykieta_tygodnia,
        key=f"tydzien_{podatek}",
    )
    pon_d, pt_d, nie_d = _granice_tygodnia(pon)

    try:
        df = _dokumenty_tygodnia(podatek, pon_d.isoformat(), nie_d.isoformat())
    except Exception as e:
        if "pobrano_at" in str(e):
            st.error(
                "Brak kolumny **pobrano_at** w tabeli `dokumenty`. "
                "Uruchom najpierw plik `migracja_pobrano_at.sql` "
                "w Supabase → SQL Editor (patrz INSTRUKCJA.md)."
            )
        else:
            st.error(f"Błąd zapytania do bazy: {e}")
        return

    n_zwykle = int((~df["spozniona"]).sum()) if not df.empty else 0
    n_spoznione = int(df["spozniona"].sum()) if not df.empty else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("Razem w tym widoku", len(df))
    k2.metric(f"Wydane {pon_d:%d.%m}–{pt_d:%d.%m}", n_zwykle)
    k3.metric("Dograne w tym tygodniu (starsze)", n_spoznione)

    if df.empty:
        st.info("Brak interpretacji dla wybranego tygodnia.")
        return

    st.caption(
        f"🟢 Zielone wiersze = interpretacje **wydane wcześniej**, które "
        f"fizycznie trafiły do bazy dopiero w tygodniu {pon_d:%d.%m}–{pt_d:%d.%m} "
        f"(publikacja wsteczna MF). Znajdziesz je też — już bez oznaczenia — "
        f"w tygodniu ich daty wydania."
    )

    widok = pd.DataFrame(
        {
            "Sygnatura": df["sygnatura"],
            "Data wydania": df["data_wyd"].map(_fmt),
            "Pobrano do bazy": df["pobrano_dnia"].map(_fmt),
            "Link": df["link"],
        }
    )
    maska = df["spozniona"].tolist()

    def _styl(wiersz: pd.Series):
        if maska[widok.index.get_loc(wiersz.name)]:
            return [
                f"background-color: {ZIELEN_TLO}; color: {ZIELEN_TEKST}; "
                f"font-weight: 600"
            ] * len(wiersz)
        return [""] * len(wiersz)

    st.dataframe(
        widok.style.apply(_styl, axis=1),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="Eureka ↗")
        },
    )

    st.divider()
    st.subheader("Prompt do zestawienia")
    st.caption(
        "Skopiuj (ikona w prawym górnym rogu pola) i wklej do GPT "
        "„Zestawienie Tygodniowe” lub do projektu DorAIdca 2.0."
    )
    st.code(_zbuduj_prompt(podatek, pon_d, df), language=None)

    st.download_button(
        "⬇️ Pobierz prompt jako .txt",
        data=_zbuduj_prompt(podatek, pon_d, df).encode("utf-8"),
        file_name=f"prompt_{podatek}_{pon_d.isoformat()}.txt",
        mime="text/plain",
        key=f"pobierz_{podatek}",
    )


# ---------------------------------------------------------------------------
# PUNKT WEJŚCIA MODUŁU (wywołaj z app.py)
# ---------------------------------------------------------------------------
def pokaz_zestawienie_tygodniowe() -> None:
    st.header("📅 Zestawienie tygodniowe")
    st.caption(
        "Interpretacje pogrupowane w tygodnie pn–pt według daty wydania. "
        "Publikacje wsteczne (dograne później przez synchronizację dzienną) "
        "widoczne są w obu tygodniach: w tygodniu wydania normalnie, "
        "w tygodniu ściągnięcia — na zielono."
    )

    tygodnie = _lista_tygodni()
    zakladki = st.tabs(PODATKI)
    for zakladka_ui, podatek in zip(zakladki, PODATKI):
        with zakladka_ui:
            _zakladka(podatek, tygodnie)


if __name__ == "__main__":
    # Umożliwia szybki test lokalny: streamlit run zestawienie_tygodniowe.py
    st.set_page_config(page_title="Zestawienie tygodniowe", layout="wide")
    pokaz_zestawienie_tygodniowe()
