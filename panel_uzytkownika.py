# -*- coding: utf-8 -*-
"""
MODUŁ 10: Mój panel — Skaner Doradca.
Osobisty panel zalogowanego użytkownika. Najważniejsze: HISTORIA TRAFIEŃ jego
alertów — te same powiadomienia, które szły mailem, ale trwale i w aplikacji
(mail bywa w spamie albo się gubi; tu nic nie przepada).

Trzy kanały (frazy / branże / przedmioty) scalone w jeden strumień, filtrowane
po właścicielu subskrypcji (koncie użytkownika), z pełnym streszczeniem,
sortowane od najnowszych, ze stronicowaniem.
"""

from __future__ import annotations

import html

import streamlit as st

import archiwum_supabase
import monitoring_fraz as mfr
from zestawienie_tygodniowe import _pasek_stron

NA_STRONIE = 50
LIMIT_POBRANIA = 1500  # bezpieczny sufit na scalanie kanałów


def _ja() -> str:
    return st.session_state.get("user_email") or "DORADCA"


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    mfr.zapewnij_tabele(archiwum_supabase._get_db())
    return True


@st.cache_data(ttl=90, show_spinner=False)
def _trafienia(wlasciciel: str) -> list[dict]:
    """Scalona historia trafień z 3 kanałów, dla danego konta (właściciela
    subskrypcji). Streszczenie dołączane najświeższe (LATERAL). Cache 90 s."""
    db = archiwum_supabase._get_db()

    def _q(sql: str) -> list[dict]:
        return db.wykonaj(sql, (wlasciciel,), fetch=True)

    frazy = _q(
        """
        SELECT w.wyslano, 'Fraza' AS kanal, f.fraza AS dopasowanie, f.email,
               d.sygnatura, d.podatek, d.data_wyd, d.link,
               s.temat, s.streszczenie
        FROM monitoring_wyslane w
        JOIN obserwowane_frazy f ON f.id = w.fraza_id
        JOIN dokumenty d ON d.id = w.dokument_id
        LEFT JOIN LATERAL (
            SELECT temat, streszczenie FROM streszczenia_auto sa
            WHERE sa.dokument_id = d.id ORDER BY wygenerowano DESC LIMIT 1
        ) s ON TRUE
        WHERE f.wlasciciel = %s
        """)
    branze = _q(
        """
        SELECT w.wyslano, 'Branża' AS kanal, b.branza AS dopasowanie, b.email,
               d.sygnatura, d.podatek, d.data_wyd, d.link,
               s.temat, s.streszczenie
        FROM monitoring_branze_wyslane w
        JOIN obserwowane_branze b ON b.id = w.sub_id
        JOIN dokumenty d ON d.id = w.dokument_id
        LEFT JOIN LATERAL (
            SELECT temat, streszczenie FROM streszczenia_auto sa
            WHERE sa.dokument_id = d.id ORDER BY wygenerowano DESC LIMIT 1
        ) s ON TRUE
        WHERE b.wlasciciel = %s
        """)
    przedm = _q(
        """
        SELECT w.wyslano, 'Przedmiot' AS kanal, p.przedmiot AS dopasowanie, p.email,
               d.sygnatura, d.podatek, d.data_wyd, d.link,
               s.temat, s.streszczenie
        FROM monitoring_przedmioty_wyslane w
        JOIN obserwowane_przedmioty p ON p.id = w.sub_id
        JOIN dokumenty d ON d.id = w.dokument_id
        LEFT JOIN LATERAL (
            SELECT temat, streszczenie FROM streszczenia_auto sa
            WHERE sa.dokument_id = d.id ORDER BY wygenerowano DESC LIMIT 1
        ) s ON TRUE
        WHERE p.wlasciciel = %s
        """)
    wszystkie = (frazy or []) + (branze or []) + (przedm or [])
    wszystkie.sort(key=lambda r: str(r.get("wyslano") or ""), reverse=True)
    return wszystkie[:LIMIT_POBRANIA]


def _subskrypcje(wlasciciel: str) -> dict:
    db = archiwum_supabase._get_db()
    def _q(sql):
        return db.wykonaj(sql, (wlasciciel,), fetch=True)
    return {
        "frazy": _q("SELECT fraza, podatek, email FROM obserwowane_frazy "
                    "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY fraza"),
        "branze": _q("SELECT branza, email FROM obserwowane_branze "
                     "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY branza"),
        "przedmioty": _q("SELECT przedmiot, podatek, email FROM obserwowane_przedmioty "
                         "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY podatek, przedmiot"),
    }


# ---------------------------------------------------------------------------
def pokaz_panel() -> None:
    ja = _ja()
    st.header("👤 Mój panel")
    st.caption(f"Zalogowano jako **{ja}**. Poniżej historia Twoich alertów — "
               "trwały zapis w aplikacji, niezależny od poczty.")

    try:
        _zapewnij_tabele()
    except Exception as e:
        st.error(f"Nie udało się przygotować danych: {e}")
        return

    try:
        trafienia = _trafienia(ja)
    except Exception as e:
        st.error(f"Nie udało się pobrać historii trafień: {e}")
        return

    # ── FILTRY ──────────────────────────────────────────────────────────────
    c1, c2 = st.columns([2, 2])
    with c1:
        kanaly = st.multiselect("Kanał", ["Fraza", "Branża", "Przedmiot"],
                                default=[], key="pu_kanaly")
    with c2:
        fraza_f = st.text_input("Szukaj w treści (sygnatura, temat, dopasowanie)",
                                key="pu_szukaj")

    widoczne = trafienia
    if kanaly:
        widoczne = [t for t in widoczne if t["kanal"] in kanaly]
    if fraza_f.strip():
        q = fraza_f.strip().lower()
        widoczne = [t for t in widoczne if q in (
            f"{t.get('sygnatura','')} {t.get('temat','') or ''} "
            f"{t.get('dopasowanie','')} {t.get('podatek','')}").lower()]

    st.metric("Trafień w historii", len(widoczne))
    if not widoczne:
        st.info("Brak trafień do pokazania. Gdy pojawi się interpretacja pasująca "
                "do Twojej subskrypcji, znajdziesz ją tutaj (i w mailu).")
    else:
        offset = _pasek_stron("pu", len(widoczne), NA_STRONIE)
        for t in widoczne[offset:offset + NA_STRONIE]:
            data_w = str(t.get("wyslano") or "")[:16].replace("T", " ")
            data_wyd = str(t.get("data_wyd") or "")[:10]
            znacznik = {"Fraza": "🔤", "Branża": "🏭", "Przedmiot": "📚"}.get(
                t["kanal"], "•")
            with st.container(border=True):
                st.markdown(
                    f"{znacznik} **{t['kanal']}: {html.escape(str(t['dopasowanie']))}** "
                    f"· {data_w}")
                st.markdown(
                    f"**[{t.get('podatek','')}] {html.escape(t.get('sygnatura',''))}** "
                    f"— wydana {data_wyd}"
                    + (f" · [otwórz w Eurece]({t['link']})" if t.get("link") else ""))
                if t.get("temat"):
                    st.caption(f"Temat: {t['temat']}")
                streszcz = (t.get("streszczenie") or "").strip()
                if streszcz:
                    with st.expander("Streszczenie"):
                        st.write(streszcz)
                else:
                    st.caption("Streszczenie: jeszcze niegotowe.")

    # ── MOJE SUBSKRYPCJE ────────────────────────────────────────────────────
    st.divider()
    with st.expander("Moje subskrypcje — zarządzasz nimi w module „Monitoring”"):
        sub = _subskrypcje(ja)
        st.markdown("**Frazy**")
        if sub["frazy"]:
            for s in sub["frazy"]:
                st.caption(f"🔤 „{s['fraza']}” · {s['podatek'] or 'wszystkie'} → {s['email']}")
        else:
            st.caption("— brak")
        st.markdown("**Branże**")
        if sub["branze"]:
            for s in sub["branze"]:
                st.caption(f"🏭 {s['branza']} → {s['email']}")
        else:
            st.caption("— brak")
        st.markdown("**Przedmioty**")
        if sub["przedmioty"]:
            for s in sub["przedmioty"]:
                st.caption(f"📚 [{s['podatek']}] {s['przedmiot']} → {s['email']}")
        else:
            st.caption("— brak")


if __name__ == "__main__":
    st.set_page_config(page_title="Mój panel", layout="wide")
    pokaz_panel()
