# -*- coding: utf-8 -*-
"""
MODUŁ 6: Zestawienie Tygodniowe — AUTOMAT (wersja próbna)
===============================================================================
Równoległy do modułu 5. Zamiast wgrywać plik z GPT, streszcza interpretacje
WPROST Z BAZY przez OpenRouter (darmowe modele) i renderuje TĘ SAMĄ tabelę
(L.p. | Sygnatura | Data wydania | Temat | Streszczenie), z zielonym
oznaczeniem publikacji opóźnionych.

Cel: porównać jakość darmowego streszczania z dotychczasowym obiegiem DOCX.

Źródło danych: tabela `dokumenty` (ta sama, którą zasila synchronizacja
dzienna). Nowe interpretacje pojawiają się tu automatycznie — wystarczy je
streścić (przycisk „Streść brakujące”). Wyniki trafiają do `streszczenia_auto`
i nie są liczone ponownie (oszczędza darmowy limit).

WYMAGA: sekcji [openrouter] w Streamlit Secrets z kluczem api_key
oraz kolumny dokumenty.pobrano_at (migracja_pobrano_at.sql).
Renderer i logika tygodni pn–nd są współdzielone z modułem 5.

STRESZCZENIA Z WTYCZKI PRZEGLĄDARKOWEJ
  Wtyczka Skaner Doradca zapisuje streszczenia do TEJ SAMEJ tabeli i pod TYM
  SAMYM modelem co automat (kolumna `zrodlo` mówi, kto je zrobił). Dzięki temu
  pojawiają się tutaj bez żadnych zmian w zapytaniach, a automat ich nie
  powtarza.

  Różnica jest jedna: wtyczka zapisuje dodatkowo PEŁNY układ z sekcjami
  (kolumna `streszczenie_pelne`), którego automat nie generuje — jego prompt
  prosi wyłącznie o zwięzłą prozę. Pozycje z pełną wersją są oznaczone rombem
  i można je rozwinąć pod tabelą.

  UWAGA na listę rozwijaną modelu: filtruje ona wiersze po kolumnie `model`.
  Wybranie modelu innego niż kanoniczny ukryje streszczenia z wtyczki —
  moduł ostrzega o tym wprost.
===============================================================================
"""

from __future__ import annotations

import datetime as dt
import time

import streamlit as st

import archiwum_supabase
import utils
import pdf_zestawienie
import streszczacz_openrouter as sopen
from zestawienie_tygodniowe import (_pasek_sortowania, _pasek_stron,
                                    _tabela_html, _zapytaj_cache)

PODATKI = ["PIT", "CIT", "VAT", "AKCYZA", "PCC"]
MAKS_TYGODNI = 104
BATCH_MAKS = 15  # ile interpretacji streścić za jednym kliknięciem (limit darmowy)
PRZERWA_S = 3.5  # odstęp między zapytaniami (limit ~20/min darmowej puli)

# Próg daty wydania — interpretacje wcześniejsze są pomijane. Start „od teraz”,
# potem wszystko na bieżąco. Format YYYY-MM-DD; porównanie łańcuchowe jest
# poprawne, bo data_wyd jest przechowywana jako tekst ISO.
#
# PRÓG JEST RÓŻNY DLA RÓŻNYCH PODATKÓW. PIT/CIT/VAT/AKCYZA startują 15.07.2026,
# PCC dopiero 03.08.2026. Dlatego zapytania wołają utils.data_start(podatek)
# zamiast jednej stałej — inaczej zakładka PCC pokazywałaby „0 braków” za okres,
# w którym nic jeszcze nie było zbierane, co wygląda jak działający automat,
# a jest brakiem danych.
#
# Źródłem prawdy jest utils.DATY_START_PODATKU. Poniższa stała to wyłącznie
# wartość domyślna dla miejsc bez kontekstu podatku.
DATA_START = utils.DATA_START_DOMYSLNA

# Model, pod którym zapisuje zarówno automat, jak i wtyczka przeglądarkowa.
# Musi się zgadzać ze zmienną MODEL_ZESTAWIENIA funkcji wtyczka-zapisz
# w Supabase — przy rozjeździe powstałyby dwa wiersze na dokument i moduł
# pokazywałby tylko jeden z nich.
MODEL_KANONICZNY = getattr(sopen, "MODEL_DOMYSLNY", "openrouter/free")

# ---------------------------------------------------------------------------
# STRESZCZANIE PRZEZ OPENROUTER — WYŁĄCZONE
# ---------------------------------------------------------------------------
# Streszczenia powstają obecnie WYŁĄCZNIE przez moduł ChatGPT (Scheduled Tasks
# + MCP) i przez wtyczkę przeglądarkową. Ścieżka OpenRouter jest wygaszona,
# bo generowała egress: przycisk „Streść brakujące" dociągał pełne teksty
# interpretacji, a sama metryka „Bez streszczenia" skanowała przy każdym
# renderze wszystkie dokumenty od DATA_START — razy cztery zakładki podatkowe.
#
# Kod NIE został usunięty, tylko odcięty jedną flagą. Powrót = zmiana na True;
# wtedy wracają: lista wyboru modelu, metryka braków i przycisk streszczania.
# Nic poza tą stałą nie trzeba ruszać.
POKAZ_STRESZCZANIE = False


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
    # Pełny układ z sekcjami — zapisuje go wyłącznie wtyczka przeglądarkowa.
    # Automat zostawia tu pustkę, bo jego prompt prosi tylko o zwięzłą prozę.
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS "
        "streszczenie_pelne TEXT DEFAULT ''"
    )
    # Kto utworzył wpis: 'automat' albo 'wtyczka:<profil>'.
    _wykonaj(
        "ALTER TABLE streszczenia_auto ADD COLUMN IF NOT EXISTS zrodlo TEXT DEFAULT ''"
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
    najstarsza = _poniedzialek(dt.date.fromisoformat(utils.data_start(podatek)))
    try:
        w = _zapytaj(
            "SELECT MIN(NULLIF(data_wyd,'')) AS m FROM dokumenty "
            "WHERE podatek=%s AND data_wyd >= %s",
            (podatek, utils.data_start(podatek)),
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


def _policz(podatek: str, model: str = "", tylko_pelne: bool = False) -> int:
    """Liczba pozycji do stronicowania. Przy włączonym filtrze liczymy tylko
    te z pełnym streszczeniem — inaczej paginacja pokazywałaby strony,
    na których po odfiltrowaniu nic nie zostaje."""
    if not tylko_pelne:
        r = _zapytaj_cache(
            "SELECT COUNT(*) AS n FROM dokumenty WHERE podatek=%s AND data_wyd >= %s",
            (podatek, utils.data_start(podatek)))
        return int(r[0]["n"]) if r else 0

    r = _zapytaj_cache(
        """SELECT COUNT(*) AS n
           FROM dokumenty d
           JOIN streszczenia_auto s
             ON s.dokument_id = d.id AND s.model = %s
           WHERE d.podatek = %s AND d.data_wyd >= %s
             AND COALESCE(s.streszczenie_pelne, '') <> ''""",
        (model, podatek, utils.data_start(podatek)))
    return int(r[0]["n"]) if r else 0


# Znacznik przy pozycjach, które mają pełne streszczenie. Doklejamy go do
# tematu, bo renderer tabeli (_tabela_html) jest WSPÓŁDZIELONY z modułem 5
# i dołożenie tam kolumny zmieniłoby wygląd także tamtego zestawienia.
ZNACZNIK_PELNEGO = "◆ "


def _wiersze(podatek: str, model: str, sort_kol: str, malejaco: bool,
             offset: int = 0, limit: int = 50,
             tylko_pelne: bool = False) -> list[dict]:
    """Strona wierszy do wyświetlenia (bez pełnych tekstów), z sortowaniem.
    Data publikacji = pobrano_at (data dogrania do bazy). Cache'owane."""
    kol = SORT_KOLUMNY.get(sort_kol, "d.data_wyd")
    kier = "DESC" if malejaco else "ASC"

    # Filtr wymusza złączenie wewnętrzne — interesują nas wyłącznie dokumenty
    # ze streszczeniem, więc LEFT JOIN nic by tu nie wniósł.
    zlaczenie = "JOIN" if tylko_pelne else "LEFT JOIN"
    warunek_pelne = ("AND COALESCE(s.streszczenie_pelne, '') <> ''"
                     if tylko_pelne else "")

    rows = _zapytaj_cache(
        f"""
        SELECT d.id, d.sygnatura, d.data_wyd, d.pobrano_at,
               s.temat AS s_temat, s.streszczenie AS s_streszcz,
               COALESCE(s.branze, '') AS s_branze,
               COALESCE(s.przedmiot, '') AS s_przedmiot,
               COALESCE(s.streszczenie_pelne, '') AS s_pelne,
               COALESCE(s.zrodlo, '') AS s_zrodlo
        FROM dokumenty d
        {zlaczenie} streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.data_wyd >= %s
          {warunek_pelne}
        ORDER BY {kol} {kier} NULLS LAST, d.sygnatura
        LIMIT {int(limit)} OFFSET {int(offset)}
        """,
        (model, podatek, utils.data_start(podatek)),
    )
    rows = [dict(r) for r in rows]  # kopia — nie modyfikujemy obiektu w cache
    for r in rows:
        r["_ma"] = _sensowne(r.get("s_streszcz"))
        r["pelne"] = (r.get("s_pelne") or "").strip()
        r["zrodlo"] = (r.get("s_zrodlo") or "").strip()
        r["_ma_pelne"] = bool(r["pelne"])

        temat = (r.get("s_temat") or "") if r["_ma"] else ""
        # Romb sygnalizuje, że pod tabelą jest co rozwinąć.
        r["temat"] = (ZNACZNIK_PELNEGO + temat) if r["_ma_pelne"] and temat else temat

        r["branza"] = (r.get("s_branze") or "") if r["_ma"] else ""
        r["przedmiot"] = (r.get("s_przedmiot") or "") if r["_ma"] else ""
        r["streszczenie"] = r.get("s_streszcz") if r["_ma"] else "— (brak streszczenia)"
        r["data_publikacji"] = r.get("pobrano_at")
    return rows


# Próg z kontroli jakości w streszczacz_openrouter. Powtórzony tutaj świadomie:
# zapytanie SQL musi znać tę liczbę, a import stałej prywatnej wiązałby moduł
# z wewnętrznym szczegółem tamtego pliku.
MIN_DLUGOSC_STRESZCZENIA = getattr(sopen, "_MIN_DLUGOSC", 120)


def _policz_brakujace(podatek: str, model: str) -> int:
    """
    Liczba interpretacji bez sensownego streszczenia — SAMA LICZBA, liczona
    po stronie bazy.

    DLACZEGO NIE len(_brakujace())
      _brakujace() pobiera wiersze wszystkich dokumentów podatku od DATA_START
      i odsiewa je w Pythonie. Do wyświetlenia jednej metryki to znaczy: cztery
      zakładki × komplet rekordów przy każdym renderze strony. Tu leci jeden
      integer.

    CZEGO TA LICZBA NIE ŁAPIE
      Pełna kontrola jakości (streszczenie_wadliwe) odrzuca dodatkowo etykiety
      moderacji, odmowy modelu i odpowiedzi po angielsku — to wymaga wyrażeń
      regularnych, więc w SQL tego nie odtwarzamy. Były to artefakty darmowych
      modeli OpenRoutera; przy streszczeniach z ChatGPT i z wtyczki praktycznie
      nie występują. Licznik może więc zaniżyć wynik o pojedyncze sztuki —
      przy pełnym przebiegu streszczania (gdy wróci) i tak zostaną wyłapane.
    """
    r = _zapytaj_cache(
        """
        SELECT COUNT(*) AS n
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.data_wyd >= %s
          AND (
                s.streszczenie IS NULL
             OR length(btrim(s.streszczenie)) < %s
             OR btrim(s.streszczenie) LIKE '{%%'
             OR s.streszczenie LIKE '%%"streszczenie"%%'
             OR s.streszczenie LIKE '%%"temat"%%'
          )
        """,
        (model, podatek, utils.data_start(podatek), MIN_DLUGOSC_STRESZCZENIA),
    )
    return int(r[0]["n"]) if r else 0


def _brakujace(podatek: str, model: str) -> list[dict]:
    """Interpretacje bez sensownego streszczenia (do przycisku i licznika).
    Lekko — bez pełnych tekstów; tekst dobierany dopiero dla wsadu."""
    rows = _zapytaj_cache(
        """
        SELECT d.id, d.sygnatura, d.data_wyd, s.streszczenie AS s_streszcz
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.data_wyd >= %s
        ORDER BY d.data_wyd DESC
        """,
        (model, podatek, utils.data_start(podatek)),
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
# PEŁNE STRESZCZENIA
# ---------------------------------------------------------------------------
def _zrodlo_opisowo(zrodlo: str) -> str:
    z = (zrodlo or "").strip()
    if z.startswith("wtyczka"):
        profil = z.split(":", 1)[1] if ":" in z else ""
        return f"wtyczka ({profil})" if profil else "wtyczka"
    return "automat"


def _pelne_streszczenia(rekordy: list[dict]) -> None:
    """
    Rozwijane pełne streszczenia pod tabelą.

    DLACZEGO POD TABELĄ, A NIE W NIEJ
      Renderer _tabela_html jest współdzielony z modułem 5. Dołożenie tam
      kolumny albo rozwijania zmieniłoby wygląd także tamtego zestawienia,
      a ono ma pozostać nietknięte. Stąd znacznik w tabeli i treść poniżej.

    Rozwijane pokazujemy WYŁĄCZNIE dla pozycji, które faktycznie mają pełną
    wersję. Puste rozwijane przy każdym wierszu byłyby szumem — po włączeniu
    automatu większość wpisów pełnej wersji mieć nie będzie, bo jego prompt
    prosi tylko o zwięzłą prozę.
    """
    z_pelnym = [r for r in rekordy if r.get("_ma_pelne")]
    if not z_pelnym:
        return

    st.markdown("---")
    st.markdown(f"#### Pełne streszczenia ({len(z_pelnym)})")
    st.caption(
        "Powstają przy ręcznym streszczaniu z wtyczki przeglądarkowej. "
        "Automat generuje wyłącznie wersję zwięzłą, widoczną w tabeli powyżej."
    )

    for r in z_pelnym:
        temat = (r.get("temat") or "").replace(ZNACZNIK_PELNEGO, "").strip()
        naglowek = f"{r['sygnatura']} — {temat}" if temat else str(r["sygnatura"])
        with st.expander(naglowek):
            st.caption(
                f"Data wydania: {r.get('data_wyd') or '—'} · "
                f"Źródło: {_zrodlo_opisowo(r.get('zrodlo'))}"
            )
            # Treść jest markdownem z nagłówkami sekcji w gwiazdkach —
            # Streamlit wyrenderuje je pogrubieniem bez naszego udziału.
            st.markdown(r["pelne"])


# ---------------------------------------------------------------------------
# ZESTAWIENIE TYGODNIOWE PDF
# ---------------------------------------------------------------------------
def _rekordy_tygodnia(pon: dt.date, model: str, podatek: str) -> list[dict]:
    """
    Interpretacje, które POJAWIŁY SIĘ W BAZIE w danym tygodniu.

    Warunek stoi na pobrano_at, nie na data_wyd — i to jest cała istota tego
    zestawienia. MF publikuje z opóźnieniem, więc wybór po dacie wydania
    zostawiałby dziurę: interpretacja z 20 lipca dograna 5 sierpnia nie trafiłaby
    do żadnego raportu. Raport za tydzień jej wydania powstał, zanim się
    pojawiła, a późniejsze już jej nie obejmują.

    Po dacie publikacji każda trafia do dokładnie jednego zestawienia.
    """
    od, do = pdf_zestawienie.granice_tygodnia(pon)
    return _zapytaj_cache(
        """
        SELECT d.sygnatura, d.podatek, d.data_wyd, d.pobrano_at, d.link,
               COALESCE(s.temat, '')              AS temat,
               COALESCE(s.streszczenie, '')       AS streszczenie,
               COALESCE(s.streszczenie_pelne, '') AS streszczenie_pelne,
               COALESCE(s.zrodlo, '')             AS zrodlo
        FROM dokumenty d
        LEFT JOIN streszczenia_auto s
               ON s.dokument_id = d.id AND s.model = %s
        WHERE d.podatek = %s AND d.pobrano_at >= %s AND d.pobrano_at <= %s
        ORDER BY d.data_wyd DESC, d.sygnatura
        """,
        (model, podatek, od, do),
    )


def _tygodnie_do_wyboru(ile: int = 12) -> list[dt.date]:
    """Poniedziałki ostatnich tygodni, od najnowszego. Bieżący pomijamy —
    jeszcze się nie skończył, więc zestawienie byłoby niepełne."""
    biezacy = pdf_zestawienie.poniedzialek(dt.date.today())
    return [biezacy - dt.timedelta(weeks=i) for i in range(1, ile + 1)]


def _pasek_pdf(podatek: str, model: str) -> None:
    """
    Przycisk generowania zestawienia tygodniowego — NAD tabelą, w obrębie
    zakładki danego podatku.

    Świadomie osobno dla każdego podatku, a nie jedną sekcją na dole strony:
    tak wygląda praca z tym modułem. Patrzysz na PIT — chcesz zestawienie PIT,
    bez przewijania na dół i wybierania podatku po raz drugi.

    LENIWE POBIERANIE (egress)
      Wcześniej _rekordy_tygodnia() wykonywało się przy KAŻDYM renderze
      zakładki, żeby wpisać liczbę pozycji na przycisku i policzyć podpis pod
      nim. To zapytanie ciągnie kolumnę streszczenie_pelne dla całego tygodnia,
      a zakładki są cztery — więc samo wejście do modułu kosztowało cztery
      pobrania pełnych streszczeń, nawet gdy nikt PDF-a nie chciał.

      Teraz zapytanie startuje DOPIERO po kliknięciu. Cena przeglądania
      tabeli: zero. Cena PDF-a: dokładnie jedno zapytanie, to samo co dawniej.
      Skutek uboczny: liczby pozycji nie ma na przycisku przed kliknięciem —
      nie da się jej znać, nie pytając bazy. Pojawia się po wygenerowaniu.
    """
    tygodnie = _tygodnie_do_wyboru()
    etykiety = {pdf_zestawienie.opis_tygodnia(p): p for p in tygodnie}

    _, srodek, _ = st.columns([1, 2, 1])
    with srodek:
        wybrana = st.selectbox(
            "Tydzień zestawienia", options=list(etykiety.keys()), index=0,
            key=f"pdf_tydzien_{podatek}", label_visibility="collapsed",
            help="Domyślnie ostatni zakończony tydzień. Bieżący pominięty — "
                 "jeszcze trwa, więc zestawienie byłoby niepełne.")
        pon = etykiety[wybrana]

        # Klucze zawierają tydzień, więc zmiana tygodnia w liście automatycznie
        # zeruje stan — bez ręcznego czyszczenia session_state.
        klucz_pliku = f"pdf_dane_{podatek}_{pon.isoformat()}"
        klucz_opisu = f"pdf_opis_{podatek}_{pon.isoformat()}"

        if st.session_state.get(klucz_pliku):
            # Po wygenerowaniu pokazujemy pobieranie zamiast przycisku —
            # inaczej łatwo kliknąć drugi raz i czekać na to samo.
            st.download_button(
                f"Pobierz zestawienie {podatek}",
                data=st.session_state[klucz_pliku],
                file_name=f"zestawienie_{podatek}_{pon.isoformat()}.pdf",
                mime="application/pdf",
                use_container_width=True, type="primary",
                key=f"pdf_pobierz_{podatek}",
            )
        elif st.session_state.get(klucz_opisu) == "PUSTO":
            st.button(f"Brak interpretacji {podatek} w tym tygodniu",
                      disabled=True, use_container_width=True,
                      key=f"pdf_pusty_{podatek}")
        else:
            if st.button(f"Przygotuj zestawienie PDF — {podatek}",
                         use_container_width=True,
                         key=f"pdf_generuj_{podatek}",
                         help="Dane do zestawienia pobierane są z bazy dopiero "
                              "teraz — samo przeglądanie tabeli nic nie kosztuje."):
                with st.spinner("Pobieram dane tygodnia i składam PDF…"):
                    rekordy = _rekordy_tygodnia(pon, model, podatek)
                    if not rekordy:
                        st.session_state[klucz_opisu] = "PUSTO"
                        st.rerun()
                    _archiwum_font_ostrzezenie()
                    try:
                        st.session_state[klucz_pliku] = pdf_zestawienie.generuj(
                            [dict(r) for r in rekordy], pon, podatek=podatek)
                        st.session_state[klucz_opisu] = _opis_tygodnia_pdf(
                            rekordy, pon)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Nie udało się wygenerować PDF: {e}")

        # Podpis pod przyciskiem — dwie liczby, które realnie coś znaczą.
        # Liczony raz, przy generowaniu, z rekordów które i tak mamy w ręku.
        opis = st.session_state.get(klucz_opisu)
        if opis and opis != "PUSTO":
            st.caption(opis)


def _opis_tygodnia_pdf(rekordy: list[dict], pon: dt.date) -> str:
    """Podpis pod przyciskiem PDF: ile pozycji, ile bez streszczenia i ile
    zostałoby pominiętych przy wyborze po dacie wydania zamiast publikacji.
    Ta ostatnia liczba jest miarą tego, ile ratuje mechanizm oparty na
    pobrano_at — warto ją widzieć."""
    bez = sum(1 for r in rekordy if not _sensowne(r.get("streszczenie")))
    wczesniejsze = sum(1 for r in rekordy
                       if str(r.get("data_wyd") or "")[:10] < pon.isoformat())
    uwagi = [f"{len(rekordy)} poz."]
    if bez:
        uwagi.append(f"{bez} bez streszczenia")
    if wczesniejsze:
        uwagi.append(f"{wczesniejsze} wydanych wcześniej")
    return " · ".join(uwagi)


def _archiwum_font_ostrzezenie() -> None:
    """Brak fontu z polskimi znakami psuje PDF po cichu — font spada na
    Helvetica, która nie ma ą/ć/ę. Lepiej powiedzieć o tym przed pobraniem."""
    try:
        import eksplorator_archiwum as ea
        ea._zarejestruj_fonty()
        if not ea._font_polski_ok:
            st.warning(
                "Nie znaleziono fontu z polskimi znakami — w PDF mogą zniknąć "
                "ą, ć, ę, ł, ń, ó, ś, ź, ż. Dodaj folder `fonts/` z plikami "
                "DejaVuSans.ttf i DejaVuSans-Bold.ttf do repozytorium.",
                icon="⚠️")
    except Exception:
        pass


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
    sort_kol, malejaco = _pasek_sortowania(
        f"auto_{podatek}", list(SORT_KOLUMNY.keys()), "Data wydania")

    tylko_pelne = st.checkbox(
        "Pokaż tylko pozycje z pełnym streszczeniem",
        key=f"auto_filtr_{podatek}",
        help="Pełne streszczenia powstają przy ręcznym streszczaniu z wtyczki. "
             "Filtr pokazuje więc sprawy, którym ktoś się faktycznie przyjrzał.",
    )

    total = _policz(podatek, model, tylko_pelne)
    if total == 0:
        if tylko_pelne:
            st.info("Żadna pozycja nie ma jeszcze pełnego streszczenia. "
                    "Powstają one przy streszczaniu z wtyczki przeglądarkowej.")
        else:
            st.info("Brak interpretacji w bazie dla tego podatku "
                    f"(od {utils.data_start(podatek)}).")
        return

    k1, k2 = st.columns(2)
    k1.metric("Interpretacji (wszystkie)", total)

    if not POKAZ_STRESZCZANIE:
        # Ta sama informacja co dawniej, ale liczona zapytaniem COUNT zamiast
        # sciaganiem rekordow. Lista braków nie jest tu do niczego potrzebna —
        # przycisk streszczania jest wygaszony.
        brak = []
        k2.metric("Bez streszczenia", _policz_brakujace(podatek, model),
                  help="Interpretacje, które czekają na streszczenie z modułu "
                       "ChatGPT albo z wtyczki przeglądarkowej.")
    else:
        brak = _brakujace(podatek, model)
        k2.metric("Bez streszczenia", len(brak))

    if POKAZ_STRESZCZANIE and brak:
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
                st.cache_data.clear()  # świeże streszczenia mają być widoczne
                st.rerun()

    _pasek_pdf(podatek, model)
    st.markdown("")

    offset = _pasek_stron(f"auto_{podatek}", total, LIMIT_WIERSZY)
    rekordy = _wiersze(podatek, model, sort_kol, malejaco, offset,
                       LIMIT_WIERSZY, tylko_pelne)
    st.markdown(_tabela_html(rekordy), unsafe_allow_html=True)

    if any(r.get("_ma_pelne") for r in rekordy):
        st.caption(f"Znacznik **{ZNACZNIK_PELNEGO.strip()}** przy temacie oznacza, "
                   "że pozycja ma pełne streszczenie — do rozwinięcia pod tabelą.")

    _pelne_streszczenia(rekordy)

    ogon = (" Streszczenia generowane modelem "
            f"`{model}` (OpenRouter) — zawsze weryfikuj przed użyciem."
            if POKAZ_STRESZCZANIE else
            " Streszczenia pochodzą z modułu ChatGPT i z wtyczki "
            "przeglądarkowej — zawsze weryfikuj przed użyciem.")
    st.caption(
        "„Data publikacji” = data dogrania do bazy (pobrania). Sortuj po niej, "
        "aby nic nie umknęło przy publikacjach opóźnionych." + ogon
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
    st.header("📑 Zestawienie interpretacji")
    st.caption(
        "Interpretacje indywidualne z bazy wraz ze streszczeniami. "
        "Streszczenia powstają w module ChatGPT i we wtyczce przeglądarkowej."
    )
    # Daty startowe różnią się między podatkami, więc podpis wymienia je
    # wprost zamiast podawać jedną liczbę, która byłaby nieprawdziwa dla PCC.
    _od_domysl = dt.date.fromisoformat(utils.DATA_START_DOMYSLNA)
    _wyjatki = ", ".join(
        f"{p} od {dt.date.fromisoformat(d):%d.%m.%Y}"
        for p, d in sorted(utils.DATY_START_PODATKU.items())
    )
    st.caption(
        f"Zakres: interpretacje wydane od **{_od_domysl:%d.%m.%Y}** włącznie"
        + (f" ({_wyjatki})." if _wyjatki else ".")
        + " Wcześniejsze są pomijane."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabeli streszczeń: {e}")
        return

    if POKAZ_STRESZCZANIE:
        klucz_api = _api_key()

        c1, c2 = st.columns([2, 3])
        with c1:
            model = st.selectbox("Model (OpenRouter)",
                                 options=sopen.MODELE_DO_WYBORU,
                                 index=0, key="auto_model")
        with c2:
            st.caption(
                "Domyślnie `openrouter/free` (auto-router darmowych modeli — odporny "
                "na rotację oferty). Limit darmowy: ~20 zapytań/min, 50/dobę "
                "(≥10 kredytów podnosi do ~1000/dobę)."
            )

        # Lista rozwijana filtruje wiersze po kolumnie `model`. Wybranie modelu
        # innego niż kanoniczny ukrywa wszystko, co zapisała wtyczka — a to
        # wygląda jak zniknięcie danych, nie jak zmiana filtra.
        if model != MODEL_KANONICZNY:
            st.warning(
                f"Wybrany model `{model}` różni się od kanonicznego "
                f"`{MODEL_KANONICZNY}`. Streszczenia zapisane przez wtyczkę "
                "przeglądarkową są przypisane do modelu kanonicznego, więc "
                "**nie będą tu widoczne**, dopóki nie wrócisz na niego w liście "
                "powyżej."
            )
    else:
        # Bez wyboru modelu tabela zawsze filtruje po modelu kanonicznym —
        # czyli po tym, którym zapisuje ChatGPT i wtyczka. To jedyny model,
        # pod którym cokolwiek w bazie jest.
        klucz_api = None
        model = MODEL_KANONICZNY

    for zakladka_ui, podatek in zip(st.tabs(PODATKI), PODATKI):
        with zakladka_ui:
            _zakladka(podatek, model, klucz_api)


if __name__ == "__main__":
    st.set_page_config(page_title="Zestawienie automat", layout="wide")
    pokaz_zestawienie_automat()
