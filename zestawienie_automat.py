# -*- coding: utf-8 -*-
"""
MODUŁ 6: Zestawienie Tygodniowe — AUTOMAT (wersja próbna)
===============================================================================
Równoległy do modułu 5. Zamiast wgrywać plik z GPT, streszcza interpretacje
WPROST Z BAZY przez OpenRouter (darmowe modele) i renderuje TĘ SAMĄ tabelę
(L.p. | Sygnatura | Data wydania | Temat | Streszczenie), z zielonym
oznaczeniem publikacji opóźnionych (identyczna zasada jak w module 5).

Cel: porównać jakość darmowego streszczania z dotychczasowym obiegiem DOCX.

Źródło danych: tabela `dokumenty` (ta sama, którą zasila synchronizacja
dzienna). Nowe interpretacje pojawiają się tu automatycznie — wystarczy je
streścić (przycisk „Streść brakujące”). Wyniki trafiają do `streszczenia_auto`
i nie są liczone ponownie (oszczędza darmowy limit).

WYMAGA: sekcji [openrouter] w Streamlit Secrets z kluczem api_key
oraz kolumny dokumenty.pobrano_at (migracja_pobrano_at.sql).
Renderer i logika tygodni pn–nd są współdzielone z modułem 5.
===============================================================================
"""

from __future__ import annotations

import datetime as dt
import time

import streamlit as st

import archiwum_supabase
import streszczacz_openrouter as sopen
from zestawienie_tygodniowe import _etykieta_tygodnia, _klucz_tygodnia, _tabela_html

PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]
MAKS_TYGODNI = 104
BATCH_MAKS = 15  # ile interpretacji streścić za jednym kliknięciem (limit darmowy)
PRZERWA_S = 3.5  # odstęp między zapytaniami (limit ~20/min darmowej puli)

# Automat streszcza WYŁĄCZNIE interpretacje wydane od tej daty włącznie
# (data_wyd >= DATA_START). Przeszłe interpretacje są pomijane — start „od teraz”,
# potem wszystko na bieżąco. Format YYYY-MM-DD; porównanie łańcuchowe jest
# poprawne, bo data_wyd jest przechowywana jako tekst ISO.
DATA_START = "2026-07-15"


# ---------------------------------------------------------------------------
# BAZA
# ---------------------------------------------------------------------------
def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


def _wykonaj(sql: str, p: tuple | None = None) -> int:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=False)


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    _wykonaj(
        """
        CREATE TABLE IF NOT EXISTS streszczenia_auto (
            id           SERIAL PRIMARY KEY,
            dokument_id  TEXT NOT NULL,
            podatek      TEXT NOT NULL,
            model        TEXT NOT NULL,
            temat        TEXT DEFAULT '',
            streszczenie TEXT DEFAULT '',
            wygenerowano TEXT NOT NULL,
            UNIQUE (dokument_id, model)
        )
        """
    )
    # Kolumna branż (klasyfikacja treściowa) — dokładana bezpiecznie do
    # istniejącej tabeli; starsze wpisy mają '' (bez branży).
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS branze TEXT DEFAULT ''"
    )
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS przedmiot TEXT DEFAULT ''"
    )
    return True


# ---------------------------------------------------------------------------
# TYGODNIE
# ---------------------------------------------------------------------------
def _poniedzialek(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


@st.cache_data(ttl=1800, show_spinner=False)
def _lista_tygodni(podatek: str) -> list[str]:
    dzis = dt.date.today()
    biezacy = _poniedzialek(dzis)
    # Nie schodzimy poniżej tygodnia progu DATA_START.
    najstarsza = _poniedzialek(dt.date.fromisoformat(DATA_START))
    try:
        w = _zapytaj(
            "SELECT MIN(NULLIF(data_wyd,'')) AS m FROM dokumenty "
            "WHERE podatek=%s AND data_wyd >= %s",
            (podatek, DATA_START),
        )
        if w and w[0].get("m"):
            najstarsza = _poniedzialek(dt.date.fromisoformat(str(w[0]["m"])[:10]))
    except Exception:
        pass
    klucze, pon = [], biezacy
    while pon >= najstarsza and len(klucze) < MAKS_TYGODNI:
        klucze.append(_klucz_tygodnia(pon))
        pon -= dt.timedelta(days=7)
    return klucze


def _granice(klucz: str) -> tuple[dt.date, dt.date]:
    rok, wk = klucz.split("-W")
    pon = dt.date.fromisocalendar(int(rok), int(wk), 1)
    return pon, pon + dt.timedelta(days=6)


# ---------------------------------------------------------------------------
# ODCZYT INTERPRETACJI + STRESZCZEŃ
# ---------------------------------------------------------------------------
def _sensowne(s: str | None) -> bool:
    """Czy zapisane streszczenie nadaje się do pokazania. Korzysta ze wspólnej
    kontroli z klienta OpenRouter (odrzuca puste, surowy/ucięty JSON, zbyt
    krótkie, etykiety bezpieczeństwa/odmowy oraz angielski). Wadliwe rekordy
    są traktowane jak brakujące — można je wygenerować ponownie przyciskiem."""
    return not sopen.streszczenie_wadliwe(s)


LIMIT_WIERSZY = 50
SORT_KOLUMNY = {
    "Data wydania": "d.data_wyd",
    "Data publikacji": "d.pobrano_at",
    "Sygnatura": "d.sygnatura",
}


def _wiersze(podatek: str, model: str, sort_kol: str, malejaco: bool) -> list[dict]:
    """50 wierszy do wyświetlenia (bez pełnych tekstów), z wybranym sortowaniem.
    Data publikacji = pobrano_at (data dogrania do bazy)."""
    kol = SORT_KOLUMNY.get(sort_kol, "d.data_wyd")
    kier = "DESC" if malejaco else "ASC"
    rows = _zapytaj(
        f"""
        SELECT d.id, d.sygnatura, d.data_wyd, d.pobrano_at,
               s.temat AS s_temat, s.streszczenie AS s_streszcz,
               COALESCE(s.branze, '') AS s_branze,
               COALESCE(s.przedmiot, '') AS s_przedmiot
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.data_wyd >= %s
        ORDER BY {kol} {kier} NULLS LAST, d.sygnatura
        LIMIT {LIMIT_WIERSZY}
        """,
        (model, podatek, DATA_START),
    )
    for r in rows:
        r["_ma"] = _sensowne(r.get("s_streszcz"))
        r["temat"] = (r.get("s_temat") or "") if r["_ma"] else ""
        r["branza"] = (r.get("s_branze") or "") if r["_ma"] else ""
        r["przedmiot"] = (r.get("s_przedmiot") or "") if r["_ma"] else ""
        r["streszczenie"] = r.get("s_streszcz") if r["_ma"] else "— (brak streszczenia)"
        r["data_publikacji"] = r.get("pobrano_at")
    return rows


def _brakujace(podatek: str, model: str) -> list[dict]:
    """Interpretacje bez sensownego streszczenia (do przycisku i licznika).
    Lekko — bez pełnych tekstów; tekst dobierany dopiero dla wsadu."""
    rows = _zapytaj(
        """
        SELECT d.id, d.sygnatura, d.data_wyd, s.streszczenie AS s_streszcz
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.data_wyd >= %s
        ORDER BY d.data_wyd DESC
        """,
        (model, podatek, DATA_START),
    )
    return [r for r in rows if not _sensowne(r.get("s_streszcz"))]


def _tekst_dla(ids: list[str]) -> dict:
    if not ids:
        return {}
    rows = _zapytaj(
        "SELECT id, tekst FROM dokumenty WHERE id = ANY(%s)", (ids,))
    return {r["id"]: r.get("tekst") or "" for r in rows}


def _zapisz_streszczenie(dok_id: str, podatek: str, model: str,
                         temat: str, streszcz: str, branze: str = "",
                         przedmiot: str = "") -> None:
    _wykonaj(
        """
        INSERT INTO streszczenia_auto
            (dokument_id, podatek, model, temat, streszczenie, branze,
             przedmiot, wygenerowano)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dokument_id, model) DO UPDATE SET
            temat=EXCLUDED.temat, streszczenie=EXCLUDED.streszczenie,
            branze=EXCLUDED.branze, przedmiot=EXCLUDED.przedmiot,
            wygenerowano=EXCLUDED.wygenerowano
        """,
        (dok_id, podatek, model, temat, streszcz, branze, przedmiot,
         dt.datetime.now().isoformat(timespec="seconds")),
    )


# ---------------------------------------------------------------------------
# KLUCZ API
# ---------------------------------------------------------------------------
def _api_key() -> str | None:
    try:
        return st.secrets["openrouter"]["api_key"]
    except Exception:
        try:
            return st.secrets["OPENROUTER_API_KEY"]
        except Exception:
            return None


# ---------------------------------------------------------------------------
# ZAKŁADKA
# ---------------------------------------------------------------------------
def _zakladka(podatek: str, model: str, klucz_api: str | None) -> None:
    c1, c2 = st.columns([2, 1])
    with c1:
        sort_kol = st.selectbox(
            "Sortuj według", list(SORT_KOLUMNY.keys()),
            key=f"auto_sort_{podatek}")
    with c2:
        kierunek = st.selectbox(
            "Kolejność", ["malejąco", "rosnąco"], key=f"auto_kier_{podatek}")
    malejaco = kierunek == "malejąco"

    rekordy = _wiersze(podatek, model, sort_kol, malejaco)
    if not rekordy:
        st.info("Brak interpretacji w bazie dla tego podatku "
                f"(od {DATA_START}).")
        return

    brak = _brakujace(podatek, model)

    k1, k2 = st.columns(2)
    k1.metric(f"Pokazano (limit {LIMIT_WIERSZY})", len(rekordy))
    k2.metric("Bez streszczenia (wszystkie)", len(brak))

    if brak:
        if not klucz_api:
            st.warning(
                "Brak klucza OpenRouter — dodaj sekcję [openrouter] w Secrets, "
                "aby streszczać. Poniżej i tak zobaczysz tabelę."
            )
        else:
            do_zrobienia = min(len(brak), BATCH_MAKS)
            if st.button(
                f"🤖 Streść brakujące ({do_zrobienia} z {len(brak)}) — model: {model}",
                key=f"auto_btn_{podatek}", type="primary",
            ):
                wsad = brak[:BATCH_MAKS]
                teksty = _tekst_dla([r["id"] for r in wsad])
                for r in wsad:
                    r["tekst"] = teksty.get(r["id"], "")
                _streszczaj(wsad, podatek, model, klucz_api)
                st.rerun()

    st.markdown(_tabela_html(rekordy), unsafe_allow_html=True)
    st.caption(
        f"„Data publikacji” = data dogrania do bazy (pobrania). Sortuj po niej, "
        f"aby nic nie umknęło przy publikacjach opóźnionych. Streszczenia "
        f"generowane modelem `{model}` (OpenRouter) — zawsze weryfikuj przed użyciem."
    )


def _streszczaj(pozycje: list[dict], podatek: str, model: str, klucz_api: str) -> None:
    pasek = st.progress(0.0, text="Streszczam…")
    ok, bledy = 0, 0
    for i, r in enumerate(pozycje, start=1):
        try:
            wynik = sopen.streszcz_tekst(
                r.get("tekst") or "", r["sygnatura"], str(r["data_wyd"]),
                api_key=klucz_api, model=model, podatek=podatek,
            )
            _zapisz_streszczenie(r["id"], podatek, model,
                                 wynik["temat"], wynik["streszczenie"],
                                 ", ".join(wynik.get("branze") or []),
                                 "; ".join(wynik.get("przedmioty") or []))
            ok += 1
        except Exception as e:
            bledy += 1
            st.warning(f"{r['sygnatura']}: {e}")
            if "401" in str(e) or "402" in str(e) or "403" in str(e):
                break  # problem z kluczem/kredytami — nie ma sensu kontynuować
        pasek.progress(i / len(pozycje), text=f"Streszczam… {i}/{len(pozycje)}")
        if i < len(pozycje):
            time.sleep(PRZERWA_S)  # szacunek dla limitu ~20/min
    pasek.empty()
    if ok:
        st.success(f"Zapisano {ok} streszczeń.")
    if bledy:
        st.info(f"Nie udało się: {bledy}. Spróbuj ponownie później "
                f"(limit dzienny/na minutę) lub zmień model.")


# ---------------------------------------------------------------------------
# WEJŚCIE
# ---------------------------------------------------------------------------
def pokaz_zestawienie_automat() -> None:
    st.header("🤖 Zestawienie tygodniowe — Automat (wersja próbna)")
    st.caption(
        "Streszczenia generowane wprost z bazy przez OpenRouter (darmowe modele). "
        "Ta sama tabela co w module 5 — do porównania jakości z obiegiem DOCX."
    )
    st.caption(
        f"Zakres: interpretacje wydane od **{dt.date.fromisoformat(DATA_START):%d.%m.%Y}** "
        f"włącznie (wcześniejsze są pomijane)."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabeli streszczeń: {e}")
        return

    klucz_api = _api_key()

    c1, c2 = st.columns([2, 3])
    with c1:
        model = st.selectbox("Model (OpenRouter)", options=sopen.MODELE_DO_WYBORU,
                             index=0, key="auto_model")
    with c2:
        st.caption(
            "Domyślnie `openrouter/free` (auto-router darmowych modeli — odporny "
            "na rotację oferty). Limit darmowy: ~20 zapytań/min, 50/dobę "
            "(≥10 kredytów podnosi do ~1000/dobę)."
        )

    for zakladka_ui, podatek in zip(st.tabs(PODATKI), PODATKI):
        with zakladka_ui:
            _zakladka(podatek, model, klucz_api)


if __name__ == "__main__":
    st.set_page_config(page_title="Zestawienie automat", layout="wide")
    pokaz_zestawienie_automat()
