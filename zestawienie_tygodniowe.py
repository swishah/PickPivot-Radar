# -*- coding: utf-8 -*-
"""
MODUŁ: Zestawienie Tygodniowe — Skaner Doradca
===============================================================================
Cztery zakładki (PIT / CIT / VAT / AKCYZA), każda z listą tygodni (pn–pt).

Dla wybranego tygodnia i podatku wgrywasz JEDEN plik ze streszczeniem
wygenerowany przez GPT "Tygodniowy Research" (PDF lub DOCX). Moduł zapisuje
plik w bazie (przetrwa odświeżenie i restart) i wyświetla jego treść —
to jest właściwe zestawienie tygodnia. Surowe metadane (sygnatura/data/link)
NIE są już głównym widokiem; pozostają jedynie jako opcjonalna kontrola
kompletności (ile interpretacji baza ma dla tego tygodnia — do sprawdzenia,
czy research je wszystkie objął).

DANE:
  * Streszczenia: tabela streszczenia_tygodniowe (tworzona automatycznie).
  * Kontrola kompletności korzysta z kolumny dokumenty.pobrano_at
    (patrz migracja_pobrano_at.sql) do rozróżnienia publikacji wstecznych.
===============================================================================
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io

import streamlit as st

import archiwum_supabase

# ---------------------------------------------------------------------------
# KONFIGURACJA
# ---------------------------------------------------------------------------
PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]

ZIELEN_GLOWNA = "#386520"
ZIELEN_TLO = "#dcefd8"
ZIELEN_TEKST = "#1b3d0f"

MAKS_TYGODNI_NA_LISCIE = 104  # 2 lata wstecz; zwiększ, gdy backfill urośnie

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ---------------------------------------------------------------------------
# POŁĄCZENIE Z BAZĄ
# ---------------------------------------------------------------------------
# To samo połączenie, którego używa reszta aplikacji: archiwum_supabase._get_db()
# zwraca cache'owany db_core.SupabaseDB (psycopg2, session pooler, SSL).
# wykonaj(sql, params, fetch=True) -> lista słowników; fetch=False -> rowcount.
def _zapytaj(sql: str, parametry: tuple | None = None) -> list[dict]:
    db = archiwum_supabase._get_db()
    return db.wykonaj(sql, parametry, fetch=True)


def _wykonaj(sql: str, parametry: tuple | None = None) -> int:
    db = archiwum_supabase._get_db()
    return db.wykonaj(sql, parametry, fetch=False)


# ---------------------------------------------------------------------------
# TABELA STRESZCZEŃ — tworzona automatycznie (raz na sesję)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    """CREATE TABLE IF NOT EXISTS — analogicznie do db_core.inicjalizuj_schemat.
    Klucz (tydzien_klucz, podatek) jest UNIQUE, żeby upsert nadpisywał
    poprzedni plik dla tego samego tygodnia i podatku zamiast go duplikować."""
    _wykonaj(
        """
        CREATE TABLE IF NOT EXISTS streszczenia_tygodniowe (
            id            SERIAL PRIMARY KEY,
            tydzien_klucz TEXT NOT NULL,
            podatek       TEXT NOT NULL,
            data_od       TEXT NOT NULL,
            data_do       TEXT NOT NULL,
            nazwa_pliku   TEXT NOT NULL,
            typ_pliku     TEXT NOT NULL,
            plik          BYTEA NOT NULL,
            tekst         TEXT DEFAULT '',
            wgrano        TEXT NOT NULL,
            UNIQUE (tydzien_klucz, podatek)
        )
        """
    )
    return True


# ---------------------------------------------------------------------------
# TYGODNIE (pn–pt)
# ---------------------------------------------------------------------------
def _poniedzialek(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _granice_tygodnia(pon: dt.date) -> tuple[dt.date, dt.date, dt.date]:
    """(poniedziałek, piątek, niedziela). Etykieta pn–pt, ale technicznie
    kubełek domyka niedziela, żeby nic z weekendową datą nie wypadło."""
    return pon, pon + dt.timedelta(days=4), pon + dt.timedelta(days=6)


def _klucz_tygodnia(pon: dt.date) -> str:
    rok, nr, _ = pon.isocalendar()
    return f"{rok}-W{nr:02d}"


def _etykieta_tygodnia(pon: dt.date) -> str:
    _, pt, _ = _granice_tygodnia(pon)
    nr = pon.isocalendar()[1]
    return f"Tydzień {nr:02d}/{pon.year}  ·  {pon:%d.%m} – {pt:%d.%m.%Y} (pn–pt)"


@st.cache_data(ttl=3600, show_spinner=False)
def _lista_tygodni() -> list[dt.date]:
    """Poniedziałki od bieżącego tygodnia wstecz do najstarszego dokumentu."""
    dzis = dt.date.today()
    biezacy = _poniedzialek(dzis)

    najstarsza = biezacy
    try:
        w = _zapytaj(
            "SELECT MIN(NULLIF(data_wyd, '')) AS m FROM dokumenty "
            "WHERE podatek = ANY(%s)",
            (PODATKI,),
        )
        if w and w[0].get("m"):
            najstarsza = _poniedzialek(dt.date.fromisoformat(str(w[0]["m"])[:10]))
    except Exception:
        pass

    tygodnie: list[dt.date] = []
    pon = biezacy
    while pon >= najstarsza and len(tygodnie) < MAKS_TYGODNI_NA_LISCIE:
        tygodnie.append(pon)
        pon -= dt.timedelta(days=7)
    return tygodnie


# ---------------------------------------------------------------------------
# KONTROLA KOMPLETNOŚCI (opcjonalna)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _licznik_tygodnia(podatek: str, pon_iso: str, nie_iso: str) -> tuple[int, int]:
    """(wydane_w_tygodniu, dograne_w_tygodniu_ale_starsze)."""
    wydane = _zapytaj(
        "SELECT COUNT(*) AS n FROM dokumenty "
        "WHERE podatek = %s AND data_wyd >= %s AND data_wyd <= %s",
        (podatek, pon_iso, nie_iso),
    )
    dograne = _zapytaj(
        "SELECT COUNT(*) AS n FROM dokumenty "
        "WHERE podatek = %s AND pobrano_at >= %s::date "
        "AND pobrano_at < (%s::date + 1) AND data_wyd < %s",
        (podatek, pon_iso, nie_iso, pon_iso),
    )
    return (int(wydane[0]["n"]) if wydane else 0,
            int(dograne[0]["n"]) if dograne else 0)


# ---------------------------------------------------------------------------
# STRESZCZENIA — odczyt / zapis / usunięcie / ekstrakcja tekstu
# ---------------------------------------------------------------------------
def _pobierz_streszczenie(podatek: str, klucz: str) -> dict | None:
    rows = _zapytaj(
        "SELECT tydzien_klucz, podatek, data_od, data_do, nazwa_pliku, "
        "typ_pliku, plik, tekst, wgrano "
        "FROM streszczenia_tygodniowe WHERE tydzien_klucz = %s AND podatek = %s",
        (klucz, podatek),
    )
    return rows[0] if rows else None


def _zapisz_streszczenie(podatek: str, klucz: str, data_od: str, data_do: str,
                         nazwa: str, typ: str, bajty: bytes, tekst: str) -> None:
    import psycopg2  # dla poprawnego zapisu BYTEA (Binary)

    _wykonaj(
        """
        INSERT INTO streszczenia_tygodniowe
            (tydzien_klucz, podatek, data_od, data_do, nazwa_pliku,
             typ_pliku, plik, tekst, wgrano)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tydzien_klucz, podatek) DO UPDATE SET
            data_od     = EXCLUDED.data_od,
            data_do     = EXCLUDED.data_do,
            nazwa_pliku = EXCLUDED.nazwa_pliku,
            typ_pliku   = EXCLUDED.typ_pliku,
            plik        = EXCLUDED.plik,
            tekst       = EXCLUDED.tekst,
            wgrano      = EXCLUDED.wgrano
        """,
        (klucz, podatek, data_od, data_do, nazwa, typ,
         psycopg2.Binary(bajty), tekst,
         dt.datetime.now().isoformat(timespec="seconds")),
    )


def _usun_streszczenie(podatek: str, klucz: str) -> None:
    _wykonaj(
        "DELETE FROM streszczenia_tygodniowe WHERE tydzien_klucz = %s AND podatek = %s",
        (klucz, podatek),
    )


def _ekstrakt_tekst(nazwa: str, bajty: bytes) -> str:
    """Wyodrębnia tekst do wyświetlenia. DOCX parsuje się pewniej niż PDF —
    dlatego przy generowaniu w GPT zalecany jest DOCX."""
    n = nazwa.lower()
    try:
        if n.endswith(".docx"):
            from docx import Document
            doc = Document(io.BytesIO(bajty))
            czesci = []
            for p in doc.paragraphs:
                if p.text and p.text.strip():
                    czesci.append(p.text.rstrip())
            for tab in doc.tables:
                for wiersz in tab.rows:
                    komorki = [c.text.strip() for c in wiersz.cells if c.text.strip()]
                    if komorki:
                        czesci.append(" | ".join(komorki))
            return "\n\n".join(czesci).strip() or "(Plik DOCX nie zawiera tekstu.)"
        if n.endswith(".pdf"):
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(bajty))
            strony = [(s.extract_text() or "").strip() for s in r.pages]
            tekst = "\n\n".join(s for s in strony if s).strip()
            return tekst or "(Nie udało się wyodrębnić tekstu z PDF — pobierz plik.)"
    except Exception as e:
        return f"(Nie udało się wyodrębnić tekstu: {e}. Plik jest zapisany — pobierz go poniżej.)"
    return "(Nieobsługiwany format — dostępny tylko przycisk pobrania.)"


# ---------------------------------------------------------------------------
# PROMPT DO GPT "TYGODNIOWY RESEARCH"
# ---------------------------------------------------------------------------
def _prompt_research(podatek: str, pon: dt.date) -> str:
    pon_d, pt_d, _ = _granice_tygodnia(pon)
    return (
        f"Wygeneruj tygodniowy research dla {podatek} za tydzień "
        f"{pon_d:%d.%m.%Y}–{pt_d:%d.%m.%Y} (poniedziałek–piątek). "
        f"Uwzględnij WSZYSTKIE interpretacje z bazy Skaner Doradca dla {podatek} "
        f"z tego okresu (data wydania od {pon_d.isoformat()} do {pt_d.isoformat()}). "
        f"Na końcu wygeneruj plik DOCX (oraz PDF) ze streszczeniami."
    )


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
    klucz = _klucz_tygodnia(pon_d)

    with st.expander("🔎 Kontrola kompletności (baza)", expanded=False):
        try:
            n_wyd, n_dograne = _licznik_tygodnia(
                podatek, pon_d.isoformat(), nie_d.isoformat()
            )
            k1, k2 = st.columns(2)
            k1.metric(f"Wydane {pon_d:%d.%m}–{pt_d:%d.%m}", n_wyd)
            k2.metric("Dograne w tym tygodniu (starsze)", n_dograne)
            if n_dograne:
                st.caption(
                    f"🟢 {n_dograne} interpretacji z wcześniejszą datą wydania "
                    f"trafiło do bazy dopiero w tym tygodniu (publikacja wsteczna MF). "
                    f"Upewnij się, że tygodniowy research je objął."
                )
            st.caption(
                f"Baza ma {n_wyd} interpretacji {podatek} wydanych w tym tygodniu — "
                f"tyle powinno znaleźć się w streszczeniu."
            )
        except Exception as e:
            if "pobrano_at" in str(e):
                st.warning(
                    "Kontrola wstecznych publikacji wymaga kolumny `pobrano_at` "
                    "(uruchom `migracja_pobrano_at.sql`). Reszta modułu działa bez niej."
                )
            else:
                st.caption(f"Nie udało się policzyć interpretacji: {e}")

    st.divider()

    istniejace = _pobierz_streszczenie(podatek, klucz)
    if istniejace:
        _pokaz_streszczenie(podatek, klucz, pon_d, istniejace)
    else:
        _pokaz_wgrywanie(podatek, klucz, pon_d, pt_d, nowe=True)


def _pokaz_streszczenie(podatek: str, klucz: str, pon_d: dt.date,
                        rekord: dict) -> None:
    wgrano = str(rekord.get("wgrano", ""))[:16].replace("T", " ")
    st.caption(
        f"📄 Streszczenie wgrane: **{rekord['nazwa_pliku']}** "
        f"({rekord['typ_pliku'].upper()}, {wgrano})"
    )

    tekst = rekord.get("tekst") or "(Brak wyodrębnionego tekstu — pobierz plik.)"
    with st.container(border=True):
        st.markdown(tekst)

    bajty = bytes(rekord["plik"]) if rekord.get("plik") is not None else b""
    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            "⬇️ Pobierz oryginalny plik",
            data=bajty,
            file_name=rekord["nazwa_pliku"],
            mime=MIME.get(rekord["typ_pliku"], "application/octet-stream"),
            key=f"dl_{podatek}_{klucz}",
            use_container_width=True,
        )
    with c2:
        if st.button("🗑️ Usuń i wgraj inne", key=f"del_{podatek}_{klucz}",
                     use_container_width=True):
            _usun_streszczenie(podatek, klucz)
            st.session_state.pop(f"hash_{podatek}_{klucz}", None)
            st.rerun()

    with st.expander("Zamień plik bez usuwania", expanded=False):
        _pokaz_wgrywanie(podatek, klucz, pon_d,
                         _granice_tygodnia(pon_d)[1], nowe=False)


def _pokaz_wgrywanie(podatek: str, klucz: str, pon_d: dt.date, pt_d: dt.date,
                     nowe: bool) -> None:
    if nowe:
        with st.expander("① Jak wygenerować streszczenie (prompt do GPT)",
                         expanded=False):
            st.caption(
                "Wklej do GPT „Tygodniowy Research”, pobierz wygenerowany plik "
                "(najlepiej DOCX) i wgraj go poniżej."
            )
            st.code(_prompt_research(podatek, pon_d), language=None)
        etykieta = "② Wgraj plik ze streszczeniem (PDF lub DOCX)"
    else:
        etykieta = "Wgraj nowy plik (zastąpi obecny)"

    plik = st.file_uploader(
        etykieta,
        type=["pdf", "docx"],
        key=f"up_{podatek}_{klucz}",
        help="DOCX jest zalecany — jego treść wyświetla się pewniej niż z PDF.",
    )

    if plik is None:
        return

    bajty = plik.getvalue()
    h = hashlib.md5(bajty).hexdigest()
    if st.session_state.get(f"hash_{podatek}_{klucz}") == h:
        return

    typ = "docx" if plik.name.lower().endswith(".docx") else "pdf"
    with st.spinner("Zapisuję streszczenie…"):
        tekst = _ekstrakt_tekst(plik.name, bajty)
        _zapisz_streszczenie(
            podatek, klucz, pon_d.isoformat(), pt_d.isoformat(),
            plik.name, typ, bajty, tekst,
        )
    st.session_state[f"hash_{podatek}_{klucz}"] = h
    st.success("Zapisano streszczenie.")
    st.rerun()


# ---------------------------------------------------------------------------
# PUNKT WEJŚCIA MODUŁU (wywoływany z app.py)
# ---------------------------------------------------------------------------
def pokaz_zestawienie_tygodniowe() -> None:
    st.header("📅 Zestawienie tygodniowe")
    st.caption(
        "Wgraj streszczenie z GPT „Tygodniowy Research” dla wybranego podatku "
        "i tygodnia (pn–pt). Plik zapisuje się w bazie i wyświetla poniżej."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabeli streszczeń: {e}")
        return

    tygodnie = _lista_tygodni()
    zakladki = st.tabs(PODATKI)
    for zakladka_ui, podatek in zip(zakladki, PODATKI):
        with zakladka_ui:
            _zakladka(podatek, tygodnie)


if __name__ == "__main__":
    st.set_page_config(page_title="Zestawienie tygodniowe", layout="wide")
    pokaz_zestawienie_tygodniowe()
