# -*- coding: utf-8 -*-
"""
MODUŁ 10: Mój panel — Skaner Doradca.
Osobisty panel zalogowanego użytkownika:
  • HISTORIA TRAFIEŃ jego alertów (3 kanały scalone) — trwale, niezależnie od
    poczty (mail bywa w spamie);
  • odznaka „nowe od ostatniej wizyty” (także kropka przy module w menu);
  • statystyki osobiste;
  • eksport historii do CSV i PDF;
  • szybkie dodawanie subskrypcji;
  • zmiana własnego hasła (self-service).
"""

from __future__ import annotations

import csv
import datetime as dt
import html
import io

import streamlit as st

import archiwum_supabase
import auth
import monitoring_fraz as mfr
from streszczacz_openrouter import BRANZE, PRZEDMIOTY
from zestawienie_tygodniowe import _pasek_stron

NA_STRONIE = 50
LIMIT_POBRANIA = 2000
PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]


def _ja() -> str:
    return st.session_state.get("user_email") or "DORADCA"


def _superadmin() -> bool:
    return bool(st.session_state.get("superadmin"))


def _db():
    return archiwum_supabase._get_db()


def _wykonaj(sql: str, p: tuple | None = None) -> int:
    return _db().wykonaj(sql, p, fetch=False)


@st.cache_resource(show_spinner=False)
def _zapewnij_tabele() -> bool:
    db = _db()
    mfr.zapewnij_tabele(db)
    db.wykonaj("CREATE TABLE IF NOT EXISTS panel_wizyty "
               "(email TEXT PRIMARY KEY, ostatnio TEXT DEFAULT '')")
    return True


# ---------------------------------------------------------------------------
# HISTORIA TRAFIEŃ
# ---------------------------------------------------------------------------
@st.cache_data(ttl=90, show_spinner=False)
def _trafienia(wlasciciel: str) -> list[dict]:
    db = _db()

    def _q(sql: str) -> list[dict]:
        return db.wykonaj(sql, (wlasciciel,), fetch=True) or []

    frazy = _q(
        """
        SELECT w.wyslano, 'Fraza' AS kanal, f.fraza AS dopasowanie,
               d.sygnatura, d.podatek, d.data_wyd, d.link, s.temat, s.streszczenie
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
        SELECT w.wyslano, 'Branża' AS kanal, b.branza AS dopasowanie,
               d.sygnatura, d.podatek, d.data_wyd, d.link, s.temat, s.streszczenie
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
        SELECT w.wyslano, 'Przedmiot' AS kanal, p.przedmiot AS dopasowanie,
               d.sygnatura, d.podatek, d.data_wyd, d.link, s.temat, s.streszczenie
        FROM monitoring_przedmioty_wyslane w
        JOIN obserwowane_przedmioty p ON p.id = w.sub_id
        JOIN dokumenty d ON d.id = w.dokument_id
        LEFT JOIN LATERAL (
            SELECT temat, streszczenie FROM streszczenia_auto sa
            WHERE sa.dokument_id = d.id ORDER BY wygenerowano DESC LIMIT 1
        ) s ON TRUE
        WHERE p.wlasciciel = %s
        """)
    wszystkie = frazy + branze + przedm
    wszystkie.sort(key=lambda r: str(r.get("wyslano") or ""), reverse=True)
    return wszystkie[:LIMIT_POBRANIA]


# ---------------------------------------------------------------------------
# ODZNAKA „NOWE OD OSTATNIEJ WIZYTY”
# ---------------------------------------------------------------------------
def _ostatnia_wizyta(email: str) -> str:
    r = _db().wykonaj("SELECT ostatnio FROM panel_wizyty WHERE email=%s",
                      (email,), fetch=True)
    return (r[0]["ostatnio"] if r else "") or ""


def _zapisz_wizyte(email: str) -> None:
    _wykonaj(
        """INSERT INTO panel_wizyty (email, ostatnio) VALUES (%s,%s)
           ON CONFLICT (email) DO UPDATE SET ostatnio=EXCLUDED.ostatnio""",
        (email, dt.datetime.now().isoformat(timespec="seconds")))


@st.cache_data(ttl=60, show_spinner=False)
def liczba_nowych(email: str) -> int:
    """Ile trafień od ostatniej wizyty — lekki COUNT (dla odznaki w menu)."""
    try:
        db = _db()
        ost = _ostatnia_wizyta(email)
        r = db.wykonaj(
            """
            SELECT
              (SELECT COUNT(*) FROM monitoring_wyslane w
                 JOIN obserwowane_frazy f ON f.id=w.fraza_id
                 WHERE f.wlasciciel=%s AND w.wyslano > %s)
            + (SELECT COUNT(*) FROM monitoring_branze_wyslane w
                 JOIN obserwowane_branze b ON b.id=w.sub_id
                 WHERE b.wlasciciel=%s AND w.wyslano > %s)
            + (SELECT COUNT(*) FROM monitoring_przedmioty_wyslane w
                 JOIN obserwowane_przedmioty p ON p.id=w.sub_id
                 WHERE p.wlasciciel=%s AND w.wyslano > %s) AS n
            """,
            (email, ost, email, ost, email, ost), fetch=True)
        return int(r[0]["n"]) if r else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# EKSPORT
# ---------------------------------------------------------------------------
def _csv(trafienia: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data alertu", "Kanał", "Dopasowanie", "Podatek", "Sygnatura",
                "Data wydania", "Temat", "Streszczenie", "Link"])
    for t in trafienia:
        w.writerow([str(t.get("wyslano") or "")[:16].replace("T", " "),
                    t.get("kanal", ""), t.get("dopasowanie", ""),
                    t.get("podatek", ""), t.get("sygnatura", ""),
                    str(t.get("data_wyd") or "")[:10], t.get("temat") or "",
                    (t.get("streszczenie") or "").replace("\n", " "),
                    t.get("link") or ""])
    return buf.getvalue().encode("utf-8-sig")  # BOM — Excel czyta PL poprawnie


def _pdf(trafienia: list[dict], wlasciciel: str) -> bytes | None:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         HRFlowable)
        import eksplorator_archiwum as ea
        ea._zarejestruj_fonty()
        font = getattr(ea, "_FONT_REGULAR", None) or "Helvetica"
        font_b = getattr(ea, "_FONT_BOLD", None) or "Helvetica-Bold"

        def esc(s):
            return html.escape(str(s or ""))

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm,
                                bottomMargin=1.5 * cm, leftMargin=1.8 * cm,
                                rightMargin=1.8 * cm)
        h = ParagraphStyle("h", fontName=font_b, fontSize=14, spaceAfter=10)
        meta = ParagraphStyle("m", fontName=font_b, fontSize=10, spaceAfter=2)
        sub = ParagraphStyle("s", fontName=font, fontSize=8, textColor="#666",
                             spaceAfter=2)
        tre = ParagraphStyle("t", fontName=font, fontSize=9, spaceAfter=8,
                             leading=12)
        el = [Paragraph(f"Historia alertów — {esc(wlasciciel)}", h),
              Paragraph("Skaner Doradca · wygenerowano "
                        + dt.datetime.now().strftime("%Y-%m-%d %H:%M"), sub),
              Spacer(1, 8)]
        for t in trafienia[:500]:
            el.append(HRFlowable(width="100%", thickness=0.5, color="#ccc"))
            el.append(Paragraph(
                f"{esc(t.get('kanal'))}: {esc(t.get('dopasowanie'))} · "
                f"{str(t.get('wyslano') or '')[:16].replace('T', ' ')}", sub))
            el.append(Paragraph(
                f"[{esc(t.get('podatek'))}] {esc(t.get('sygnatura'))} — "
                f"wydana {str(t.get('data_wyd') or '')[:10]}", meta))
            if t.get("temat"):
                el.append(Paragraph("Temat: " + esc(t.get("temat")), sub))
            if (t.get("streszczenie") or "").strip():
                el.append(Paragraph(esc(t["streszczenie"]), tre))
        doc.build(el)
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SZYBKIE DODANIE SUBSKRYPCJI
# ---------------------------------------------------------------------------
def _dodaj_subskrypcje(kanal: str, wartosc: str, podatek: str, email: str) -> None:
    teraz = dt.datetime.now().isoformat(timespec="seconds")
    if kanal == "Fraza":
        _wykonaj(
            """INSERT INTO obserwowane_frazy
                 (fraza, email, podatek, aktywna, utworzono, wlasciciel)
               VALUES (%s,%s,%s,TRUE,%s,%s)
               ON CONFLICT (fraza, email, podatek) DO UPDATE SET aktywna=TRUE""",
            (wartosc, email, podatek or None, teraz, email))
    elif kanal == "Branża":
        _wykonaj(
            """INSERT INTO obserwowane_branze
                 (branza, email, aktywna, utworzono, wlasciciel)
               VALUES (%s,%s,TRUE,%s,%s)
               ON CONFLICT (branza, email) DO UPDATE SET aktywna=TRUE""",
            (wartosc, email, teraz, email))
    else:  # Przedmiot
        _wykonaj(
            """INSERT INTO obserwowane_przedmioty
                 (przedmiot, podatek, email, aktywna, utworzono, wlasciciel)
               VALUES (%s,%s,%s,TRUE,%s,%s)
               ON CONFLICT (przedmiot, email) DO UPDATE SET aktywna=TRUE""",
            (wartosc, podatek, email, teraz, email))


def _subskrypcje(wlasciciel: str) -> dict:
    db = _db()
    def _q(sql):
        return db.wykonaj(sql, (wlasciciel,), fetch=True) or []
    return {
        "frazy": _q("SELECT fraza, podatek FROM obserwowane_frazy "
                    "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY fraza"),
        "branze": _q("SELECT branza FROM obserwowane_branze "
                     "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY branza"),
        "przedmioty": _q("SELECT przedmiot, podatek FROM obserwowane_przedmioty "
                         "WHERE aktywna=TRUE AND wlasciciel=%s ORDER BY podatek, przedmiot"),
    }


# ---------------------------------------------------------------------------
# WIDOK
# ---------------------------------------------------------------------------
def pokaz_panel() -> None:
    ja = _ja()
    st.header("👤 Mój panel")

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

    # ── ODZNAKA „nowe od ostatniej wizyty” + zapis wizyty ───────────────────
    ost = _ostatnia_wizyta(ja)
    nowe = [t for t in trafienia if str(t.get("wyslano") or "") > ost] if ost else trafienia
    if ost and nowe:
        st.success(f"🔴 {len(nowe)} nowych trafień od ostatniej wizyty "
                   f"({ost[:16].replace('T', ' ')}).")
    _zapisz_wizyte(ja)
    liczba_nowych.clear()  # odznaka w menu ma się wyzerować po wejściu

    st.caption(f"Zalogowano jako **{ja}**. Historia alertów jest trwała — "
               "nie zależy od poczty (mail bywa w spamie).")

    # ── STATYSTYKI OSOBISTE ─────────────────────────────────────────────────
    tydz = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    w_tygodniu = sum(1 for t in trafienia if str(t.get("wyslano") or "")[:10] >= tydz)

    def _najczestsze(kanal):
        z = {}
        for t in trafienia:
            if t["kanal"] == kanal:
                z[t["dopasowanie"]] = z.get(t["dopasowanie"], 0) + 1
        return max(z, key=z.get) if z else "—"

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Trafień łącznie", len(trafienia))
    s2.metric("W ostatnim tygodniu", w_tygodniu)
    s3.metric("Najczęstsza branża", _najczestsze("Branża"))
    s4.metric("Najczęstszy przedmiot", _najczestsze("Przedmiot"))

    st.divider()

    # ── FILTRY + EKSPORT ────────────────────────────────────────────────────
    c1, c2 = st.columns([2, 2])
    with c1:
        kanaly = st.multiselect("Kanał", ["Fraza", "Branża", "Przedmiot"],
                                default=[], key="pu_kanaly")
    with c2:
        szukaj = st.text_input("Szukaj (sygnatura, temat, dopasowanie)",
                               key="pu_szukaj")

    widoczne = trafienia
    if kanaly:
        widoczne = [t for t in widoczne if t["kanal"] in kanaly]
    if szukaj.strip():
        q = szukaj.strip().lower()
        widoczne = [t for t in widoczne if q in (
            f"{t.get('sygnatura','')} {t.get('temat','') or ''} "
            f"{t.get('dopasowanie','')} {t.get('podatek','')}").lower()]

    e1, e2, e3 = st.columns([1, 1, 3])
    if widoczne:
        e1.download_button("⬇️ CSV", _csv(widoczne),
                           file_name="historia_alertow.csv", mime="text/csv",
                           use_container_width=True)
        pdf = _pdf(widoczne, ja)
        if pdf:
            e2.download_button("⬇️ PDF", pdf, file_name="historia_alertow.pdf",
                               mime="application/pdf", use_container_width=True)
        else:
            e2.caption("PDF niedostępny")
    e3.metric("Trafień w widoku", len(widoczne))

    if not widoczne:
        st.info("Brak trafień do pokazania. Gdy pojawi się interpretacja pasująca "
                "do Twojej subskrypcji, znajdziesz ją tutaj (i w mailu).")
    else:
        offset = _pasek_stron("pu", len(widoczne), NA_STRONIE)
        for t in widoczne[offset:offset + NA_STRONIE]:
            data_w = str(t.get("wyslano") or "")[:16].replace("T", " ")
            data_wyd = str(t.get("data_wyd") or "")[:10]
            znak = {"Fraza": "🔤", "Branża": "🏭", "Przedmiot": "📚"}.get(t["kanal"], "•")
            nowy = " · 🆕" if (ost and str(t.get("wyslano") or "") > ost) else ""
            with st.container(border=True):
                st.markdown(f"{znak} **{t['kanal']}: "
                            f"{html.escape(str(t['dopasowanie']))}** · {data_w}{nowy}")
                st.markdown(
                    f"**[{t.get('podatek','')}] {html.escape(t.get('sygnatura',''))}** "
                    f"— wydana {data_wyd}"
                    + (f" · [otwórz w Eurece]({t['link']})" if t.get("link") else ""))
                if t.get("temat"):
                    st.caption(f"Temat: {t['temat']}")
                tresc = (t.get("streszczenie") or "").strip()
                if tresc:
                    with st.expander("Streszczenie"):
                        st.write(tresc)
                else:
                    st.caption("Streszczenie: jeszcze niegotowe.")

    # ── SZYBKIE DODANIE SUBSKRYPCJI ─────────────────────────────────────────
    st.divider()
    with st.expander("➕ Szybko dodaj subskrypcję"):
        kanal = st.radio("Kanał", ["Fraza", "Branża", "Przedmiot"],
                         horizontal=True, key="pu_add_kanal")
        wartosc, podatek = "", ""
        if kanal == "Fraza":
            wartosc = st.text_input("Fraza do obserwowania", key="pu_add_fraza")
            podatek = st.selectbox("Podatek (opcjonalnie)",
                                   ["— wszystkie —"] + PODATKI, key="pu_add_fp")
            podatek = "" if podatek == "— wszystkie —" else podatek
        elif kanal == "Branża":
            wartosc = st.selectbox("Branża", BRANZE, key="pu_add_branza")
        else:
            podatek = st.selectbox("Podatek", PODATKI, key="pu_add_pp")
            wartosc = st.selectbox("Przedmiot", PRZEDMIOTY.get(podatek, []),
                                   key="pu_add_przedmiot")
        if st.button("Dodaj subskrypcję", type="primary", key="pu_add_btn"):
            if not (wartosc or "").strip():
                st.error("Podaj wartość subskrypcji.")
            else:
                try:
                    _dodaj_subskrypcje(kanal, wartosc.strip(), podatek, ja)
                    st.cache_data.clear()
                    st.success(f"Dodano: {kanal} — {wartosc}. Alerty będą "
                               f"wysyłane na {ja}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Nie udało się dodać: {e}")

    # ── MOJE SUBSKRYPCJE ────────────────────────────────────────────────────
    with st.expander("Moje subskrypcje — pełne zarządzanie w module „Monitoring”"):
        sub = _subskrypcje(ja)
        st.markdown("**Frazy**")
        for s in sub["frazy"]:
            st.caption(f"🔤 „{s['fraza']}” · {s['podatek'] or 'wszystkie'}")
        if not sub["frazy"]:
            st.caption("— brak")
        st.markdown("**Branże**")
        for s in sub["branze"]:
            st.caption(f"🏭 {s['branza']}")
        if not sub["branze"]:
            st.caption("— brak")
        st.markdown("**Przedmioty**")
        for s in sub["przedmioty"]:
            st.caption(f"📚 [{s['podatek']}] {s['przedmiot']}")
        if not sub["przedmioty"]:
            st.caption("— brak")

    # ── ZMIANA WŁASNEGO HASŁA (konta bazodanowe) ────────────────────────────
    st.divider()
    if _superadmin():
        st.caption("Konto administracyjne DORADCA — hasło zmienia się w Secrets, "
                   "nie tutaj.")
    else:
        with st.expander("🔑 Zmień hasło"):
            h_stare = st.text_input("Obecne hasło", type="password", key="pu_h0")
            h1 = st.text_input("Nowe hasło", type="password", key="pu_h1")
            h2 = st.text_input("Powtórz nowe hasło", type="password", key="pu_h2")
            st.caption("Hasło: min. 8 znaków, w tym cyfra i znak specjalny.")
            if st.button("Zmień hasło", type="primary", key="pu_h_btn"):
                if h1 != h2:
                    st.error("Nowe hasła nie są identyczne.")
                else:
                    try:
                        auth.zmien_haslo(ja, h_stare, h1)
                        st.success("Hasło zmienione.")
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Nie udało się zmienić hasła: {e}")


if __name__ == "__main__":
    st.set_page_config(page_title="Mój panel", layout="wide")
    pokaz_panel()
