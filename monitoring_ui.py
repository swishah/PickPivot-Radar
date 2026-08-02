# -*- coding: utf-8 -*-
"""
MODUŁ 7: Monitoring i Powiadomienia — Skaner Doradca (UI).
Jeden moduł, trzy kanały powiadomień e-mail (zakładki):

  • FRAZY      — dosłowny fragment tekstu w nowych interpretacjach (ILIKE),
  • BRANŻE     — kto pyta (działalność wnioskodawcy; klasyfikacja modelu
                 przy streszczaniu, zamknięta taksonomia),
  • PRZEDMIOTY — czego dotyczy merytorycznie (np. estoński CIT, WHT, KSeF;
                 klasyfikacja modelu, taksonomia per podatek).

Wysyłką zajmuje się skrypt monitoring_fraz.py w GitHub Actions (wspólny
harmonogram i SMTP dla wszystkich trzech kanałów). Ten moduł tylko zarządza
subskrypcjami i pokazuje historię.

ZMIANA WOBEC POPRZEDNIEJ WERSJI — ŹRÓDŁO TAKSONOMII
    Było: `from streszczacz_openrouter import BRANZE, PRZEDMIOTY`.
    Streszczanie przeszło na moduł ChatGPT, który klasyfikuje wyłącznie
    z list zwracanych przez Edge Function gpt-tresci — a te pochodzą
    z tabel taksonomia_branze / taksonomia_przedmiotow. Stałe w module
    OpenRoutera przestały być źródłem prawdy.

    Skutek starego importu był cichy i realny: użytkownik mógł zasubskrybować
    branżę, której model już nie nadaje, i nigdy nie dostać powiadomienia —
    a lista nie pokazywała branż dodanych do taksonomii po zamrożeniu stałych.
    Nic się nie wysypywało, po prostu alerty nie przychodziły.

    Jest: odczyt z tych samych tabel, z których czyta gpt-tresci. Jedno
    źródło prawdy dla klasyfikatora, walidatora zapisu i tego UI.
"""

from __future__ import annotations

import datetime as dt
import re

import streamlit as st

import archiwum_supabase
import auth
import monitoring_fraz as mfr

PODATKI_FRAZY = ["(wszystkie)", "PIT", "CIT", "VAT", "AKCYZA", "PCC"]


# ---------------------------------------------------------------------------
# WSPÓLNE
# ---------------------------------------------------------------------------
def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


def _wykonaj(sql: str, p: tuple | None = None) -> int:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=False)


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    mfr.zapewnij_tabele(archiwum_supabase._get_db())
    return True


# ---------------------------------------------------------------------------
# TAKSONOMIA — jedno źródło prawdy (te same tabele, co gpt-tresci)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _branze() -> list[str]:
    """Aktywne branże, w kolejności z taksonomii.

    TTL godzinny, a nie cache_resource bez wygasania: taksonomia zmienia się
    rzadko, ale gdy już się zmieni, nikt nie będzie restartował aplikacji,
    żeby zobaczyć nową pozycję.
    """
    w = _zapytaj(
        "SELECT branza FROM taksonomia_branze WHERE aktywna ORDER BY kolejnosc"
    )
    return [r["branza"] for r in w]


@st.cache_data(ttl=3600, show_spinner=False)
def _przedmioty() -> dict[str, list[str]]:
    """Aktywne przedmioty pogrupowane po podatku, w kolejności z taksonomii."""
    w = _zapytaj(
        "SELECT podatek, przedmiot FROM taksonomia_przedmiotow "
        "WHERE aktywny ORDER BY podatek, kolejnosc"
    )
    out: dict[str, list[str]] = {}
    for r in w:
        out.setdefault((r["podatek"] or "").upper(), []).append(r["przedmiot"])
    out.pop("", None)
    return out


def _email_poprawny(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", e or ""))


def _ja() -> str:
    return st.session_state.get("user_email") or "DORADCA"


def _admin() -> bool:
    return (st.session_state.get("superadmin")
            or st.session_state.get("rola") == "admin")


def _filtr_wlasciciela(alias: str = "") -> tuple[str, tuple]:
    """Zwraca (fragment WHERE, parametry). Admin widzi wszystko; user tylko
    swoje (po koncie twórcy)."""
    kol = (alias + "." if alias else "") + "wlasciciel"
    if _admin():
        return "", ()
    return f" AND {kol} = %s", (_ja(),)


def _teraz() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _historia(sql: str) -> None:
    with st.expander("Historia wysłanych powiadomień (ostatnie 30)"):
        hist = _zapytaj(sql)
        if not hist:
            st.caption("Jeszcze nic nie wysłano.")
        for h in hist:
            st.caption(f"{str(h['wyslano'])[:16].replace('T',' ')} — "
                       f"{h['co']} → {h['email']} (dok. {h['dokument_id']})")


# ---------------------------------------------------------------------------
# ZAKŁADKA: FRAZY
# ---------------------------------------------------------------------------
def _zakladka_frazy() -> None:
    st.caption(
        "Dosłowne dopasowanie fragmentu tekstu (bez wielkości liter). Dla "
        "polskich odmian wpisuj rdzeń: „ciepłownictw” złapie „ciepłownictwa”, "
        "„ciepłownictwem”, „ciepłownictwie”."
    )
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        fraza = st.text_input("Fraza", key="mf_fraza")
    with c2:
        email = st.text_input("E-mail do powiadomień", key="mf_email")
    with c3:
        podatek = st.selectbox("Podatek", PODATKI_FRAZY, key="mf_podatek")

    if st.button("➕ Dodaj", type="primary", key="mf_dodaj"):
        f = (fraza or "").strip()
        e = (email or "").strip().lower()
        p = "" if podatek == "(wszystkie)" else podatek
        if len(f) < 4:
            st.error("Fraza musi mieć co najmniej 4 znaki (krótsze dają lawinę "
                     "przypadkowych trafień).")
        elif not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            _wykonaj(
                """INSERT INTO obserwowane_frazy
                     (fraza, email, podatek, aktywna, utworzono, wlasciciel)
                   VALUES (%s,%s,%s,TRUE,%s,%s)
                   ON CONFLICT (fraza, email, podatek)
                   DO UPDATE SET aktywna = TRUE""",
                (f, e, p, _teraz(), _ja()),
            )
            st.success(f"Obserwuję frazę „{f}” ({podatek}) → {e}")
            st.rerun()

    st.divider()
    _w, _p = _filtr_wlasciciela()
    frazy = _zapytaj(
        "SELECT id, fraza, email, podatek, wlasciciel FROM obserwowane_frazy "
        "WHERE aktywna = TRUE" + _w + " ORDER BY email, fraza", _p or None
    )
    if not frazy:
        st.info("Brak aktywnych fraz.")
    for f in frazy:
        c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
        c1.markdown(f"**„{f['fraza']}”**")
        c2.markdown(f"{f['email']}"
                    + (f"  ·  _twórca: {f['wlasciciel']}_" if _admin() else ""))
        c3.markdown(f"`{f['podatek'] or 'wszystkie'}`")
        if c4.button("🗑️", key=f"mf_usun_{f['id']}", help="Przestań obserwować"):
            _wykonaj("UPDATE obserwowane_frazy SET aktywna=FALSE WHERE id=%s",
                     (f["id"],))
            st.rerun()

    _historia(
        """SELECT w.wyslano, ('„' || f.fraza || '”') AS co, f.email, w.dokument_id
           FROM monitoring_wyslane w
           JOIN obserwowane_frazy f ON f.id = w.fraza_id
           ORDER BY w.wyslano DESC LIMIT 30"""
    )


# ---------------------------------------------------------------------------
# ZAKŁADKA: BRANŻE
# ---------------------------------------------------------------------------
def _zakladka_branze() -> None:
    st.caption(
        "Model przy streszczaniu ocenia PO TREŚCI, jakiej branży dotyczy "
        "działalność wnioskodawcy (nie po słowach kluczowych)."
    )

    branze = _branze()
    if not branze:
        st.error(
            "Taksonomia branż jest pusta — sprawdź tabelę `taksonomia_branze` "
            "(kolumna `aktywna`). Bez niej nie da się dodać subskrypcji."
        )
        return

    c1, c2 = st.columns([2, 2])
    with c1:
        branza = st.selectbox("Branża", branze, key="mb_branza")
    with c2:
        email = st.text_input("E-mail do powiadomień", key="mb_email")

    if st.button("➕ Subskrybuj", type="primary", key="mb_dodaj"):
        e = (email or "").strip().lower()
        if not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            _wykonaj(
                """INSERT INTO obserwowane_branze
                     (branza, email, aktywna, utworzono, wlasciciel)
                   VALUES (%s,%s,TRUE,%s,%s)
                   ON CONFLICT (branza, email) DO UPDATE SET aktywna = TRUE""",
                (branza, e, _teraz(), _ja()),
            )
            st.success(f"Subskrypcja: branża {branza} → {e}")
            st.rerun()

    st.divider()
    _w, _p = _filtr_wlasciciela()
    subs = _zapytaj(
        "SELECT id, branza, email, wlasciciel FROM obserwowane_branze "
        "WHERE aktywna = TRUE" + _w + " ORDER BY email, branza", _p or None
    )
    if not subs:
        st.info("Brak subskrypcji.")

    # Subskrypcja spoza aktualnej taksonomii nigdy nie dostanie powiadomienia —
    # model nie ma z czego nadać takiej branży. Oznaczamy to wprost, bo inaczej
    # objawem jest wyłącznie cisza.
    zbior = set(branze)
    for s in subs:
        c1, c2, c3 = st.columns([3, 3, 1])
        martwa = s["branza"] not in zbior
        c1.markdown(f"**{s['branza']}**" + (" ⚠️" if martwa else ""))
        c2.markdown(f"{s['email']}")
        if martwa:
            c1.caption("Poza aktualną taksonomią — nie będzie trafień.")
        if c3.button("🗑️", key=f"mb_usun_{s['id']}", help="Anuluj subskrypcję"):
            _wykonaj("UPDATE obserwowane_branze SET aktywna=FALSE WHERE id=%s",
                     (s["id"],))
            st.rerun()

    with st.expander("Rozkład branż w dotychczasowych streszczeniach"):
        try:
            rozkl = _zapytaj(
                """SELECT TRIM(x.b) AS k, COUNT(*) AS n
                   FROM streszczenia_auto s,
                        LATERAL unnest(string_to_array(s.branze, ',')) AS x(b)
                   WHERE COALESCE(s.branze,'') <> ''
                   GROUP BY TRIM(x.b) ORDER BY n DESC"""
            )
            if not rozkl:
                st.caption("Brak sklasyfikowanych streszczeń (branże nadawane "
                           "są nowym streszczeniom).")
            for r in rozkl:
                st.caption(f"{r['k']}: {r['n']}")
        except Exception as e:
            st.caption(f"Nie udało się policzyć rozkładu: {e}")

    _historia(
        """SELECT w.wyslano, b.branza AS co, b.email, w.dokument_id
           FROM monitoring_branze_wyslane w
           JOIN obserwowane_branze b ON b.id = w.sub_id
           ORDER BY w.wyslano DESC LIMIT 30"""
    )


# ---------------------------------------------------------------------------
# ZAKŁADKA: PRZEDMIOTY
# ---------------------------------------------------------------------------
def _zakladka_przedmioty() -> None:
    st.caption(
        "Obszar merytoryczny (np. estoński CIT, WHT, KSeF) nadawany przez "
        "model przy streszczaniu — taksonomia właściwa dla podatku."
    )

    przedmioty = _przedmioty()
    podatki = sorted(przedmioty.keys())
    if not podatki:
        st.error(
            "Taksonomia przedmiotów jest pusta — sprawdź tabelę "
            "`taksonomia_przedmiotow` (kolumna `aktywny`)."
        )
        return

    c1, c2, c3 = st.columns([1, 3, 2])
    with c1:
        podatek = st.selectbox("Podatek", podatki, key="mp_podatek")
    with c2:
        przedmiot = st.selectbox(
            "Przedmiot (obszar merytoryczny)",
            przedmioty[podatek],
            key=f"mp_przedmiot_{podatek}",
        )
    with c3:
        email = st.text_input("E-mail do powiadomień", key="mp_email")

    if st.button("➕ Subskrybuj", type="primary", key="mp_dodaj"):
        e = (email or "").strip().lower()
        if not _email_poprawny(e):
            st.error("Podaj poprawny adres e-mail.")
        else:
            _wykonaj(
                """INSERT INTO obserwowane_przedmioty
                     (przedmiot, podatek, email, aktywna, utworzono, wlasciciel)
                   VALUES (%s,%s,%s,TRUE,%s,%s)
                   ON CONFLICT (przedmiot, email) DO UPDATE SET aktywna = TRUE""",
                (przedmiot, podatek, e, _teraz(), _ja()),
            )
            st.success(f"Subskrypcja: {przedmiot} ({podatek}) → {e}")
            st.rerun()

    st.divider()
    _w, _p = _filtr_wlasciciela()
    subs = _zapytaj(
        "SELECT id, przedmiot, podatek, email, wlasciciel FROM obserwowane_przedmioty "
        "WHERE aktywna = TRUE" + _w + " ORDER BY email, podatek, przedmiot", _p or None
    )
    if not subs:
        st.info("Brak subskrypcji.")

    wszystkie_przedmioty = {p for lista in przedmioty.values() for p in lista}
    for s in subs:
        c1, c2, c3, c4 = st.columns([3, 1, 3, 1])
        martwy = s["przedmiot"] not in wszystkie_przedmioty
        c1.markdown(f"**{s['przedmiot']}**" + (" ⚠️" if martwy else ""))
        c2.markdown(f"`{s['podatek']}`")
        c3.markdown(f"{s['email']}")
        if martwy:
            c1.caption("Poza aktualną taksonomią — nie będzie trafień.")
        if c4.button("🗑️", key=f"mp_usun_{s['id']}", help="Anuluj subskrypcję"):
            _wykonaj("UPDATE obserwowane_przedmioty SET aktywna=FALSE WHERE id=%s",
                     (s["id"],))
            st.rerun()

    with st.expander("Rozkład przedmiotów w dotychczasowych streszczeniach"):
        try:
            rozkl = _zapytaj(
                """SELECT TRIM(x.p) AS k, COUNT(*) AS n
                   FROM streszczenia_auto s,
                        LATERAL unnest(string_to_array(s.przedmiot, ';')) AS x(p)
                   WHERE COALESCE(s.przedmiot,'') <> ''
                   GROUP BY TRIM(x.p) ORDER BY n DESC"""
            )
            if not rozkl:
                st.caption("Brak sklasyfikowanych streszczeń (przedmiot nadawany "
                           "jest nowym streszczeniom).")
            for r in rozkl:
                st.caption(f"{r['k']}: {r['n']}")
        except Exception as e:
            st.caption(f"Nie udało się policzyć rozkładu: {e}")

    _historia(
        """SELECT w.wyslano, p.przedmiot AS co, p.email, w.dokument_id
           FROM monitoring_przedmioty_wyslane w
           JOIN obserwowane_przedmioty p ON p.id = w.sub_id
           ORDER BY w.wyslano DESC LIMIT 30"""
    )


# ---------------------------------------------------------------------------
# WEJŚCIE
# ---------------------------------------------------------------------------
def pokaz_monitoring() -> None:
    st.header("🔔 Monitoring i Powiadomienia")
    st.caption(
        "Trzy kanały powiadomień e-mail o nowych interpretacjach: dosłowne "
        "frazy, branża wnioskodawcy i przedmiot (obszar merytoryczny). "
        "Sprawdzanie odbywa się automatycznie po synchronizacjach."
    )

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować tabel: {e}")
        return

    z1, z2, z3 = st.tabs(["🔤 Frazy", "🏭 Branże", "📚 Przedmioty"])
    with z1:
        _zakladka_frazy()
    with z2:
        _zakladka_branze()
    with z3:
        _zakladka_przedmioty()


if __name__ == "__main__":
    st.set_page_config(page_title="Monitoring i Powiadomienia", layout="wide")
    pokaz_monitoring()
