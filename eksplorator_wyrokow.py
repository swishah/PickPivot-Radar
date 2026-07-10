"""
eksplorator_wyrokow.py — Modul 4: Wyroki Sadow Administracyjnych (CBOSA).

Podglad archiwum wyrokow WSA/NSA dla spraw podatkowych (symbole 611x),
zasilanego przez synchronizacja_wyrokow.py (GitHub Actions, niedziela).

Wyroki sa dokumentami ZYWYMI (uzasadnienie dochodzi po tygodniach,
prawomocnosc po miesiacach) — dlatego kazdy rekord ma status tresci
i modul pokazuje DWIE miary naraz (wszystkie / kompletne), zeby
"oczekujace na uzasadnienie" nie wygladaly jak brak w bazie.
"""

import io
import os
import re
import streamlit as st
import pandas as pd
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

# Wspolna maszyneria fontow (polskie znaki) — z modulu Archiwum
import eksplorator_archiwum as _arch_mod


MIESIACE_PL = ["Styczen","Luty","Marzec","Kwiecien","Maj","Czerwiec",
               "Lipiec","Sierpien","Wrzesien","Pazdziernik","Listopad","Grudzien"]

ETYKIETY_STATUSU = {
    "KOMPLETNY":                "✅ kompletny",
    "OCZEKUJE_NA_UZASADNIENIE": "⏳ czeka na uzasadnienie",
    "BEZ_UZASADNIENIA_TRWALE":  "∅ bez uzasadnienia",
}


def _wykryj_baze():
    try:
        if "supabase" in st.secrets:
            s = st.secrets["supabase"]
            if (s.get("host") and s.get("user") and s.get("password")) or \
               str(s.get("url", "")).startswith("postgresql"):
                import db_core
                import db_wyroki
                cfg = dict(s)
                db = db_core.SupabaseDB(cfg)
                db_wyroki.inicjalizuj_schemat_wyrokow(db)
                return db, db_wyroki
    except Exception as e:
        st.error(f"Blad polaczenia z baza: {e}")
    return None, None


# =============================================================================
# PDF pojedynczego wyroku
# =============================================================================
def _generuj_pdf_wyroku(w: dict) -> bytes:
    _arch_mod._zarejestruj_fonty()
    F_REG, F_BOLD = _arch_mod._FONT_REGULAR, _arch_mod._FONT_BOLD

    granatowy = colors.HexColor("#1B2A4A")
    szary     = colors.HexColor("#5A6472")
    linia_szr = colors.HexColor("#C7CCD6")

    bufor = io.BytesIO()
    doc = SimpleDocTemplate(bufor, pagesize=A4,
                            topMargin=2.2*cm, bottomMargin=2*cm,
                            leftMargin=2*cm, rightMargin=2*cm,
                            title=f"Wyrok {w.get('sygnatura','')}")

    s_marka  = ParagraphStyle("m",  fontName=F_REG,  fontSize=9,    textColor=szary, spaceAfter=2)
    s_tytul  = ParagraphStyle("t",  fontName=F_BOLD, fontSize=17,   textColor=granatowy, spaceAfter=6, leading=21)
    s_meta   = ParagraphStyle("me", fontName=F_REG,  fontSize=10.5, textColor=szary, spaceAfter=12, leading=15)
    s_sekcja = ParagraphStyle("s",  fontName=F_BOLD, fontSize=12,   textColor=granatowy, spaceBefore=8, spaceAfter=8)
    s_tresc  = ParagraphStyle("tr", fontName=F_REG,  fontSize=10.3, leading=15.5, spaceAfter=9,
                              alignment=4, textColor=colors.HexColor("#1A1A1A"))
    s_stopka = ParagraphStyle("st", fontName=F_REG,  fontSize=8.3,  textColor=szary, spaceBefore=4, leading=11)

    el = []
    el.append(Paragraph("PickPivot — Archiwum Wyroków Sądów Administracyjnych", s_marka))
    el.append(HRFlowable(width="100%", thickness=1.1, color=granatowy, spaceAfter=10))

    naglowek = f"{w.get('sygnatura','—')} — {w.get('rodzaj','')} {(''+w.get('sad','')) if w.get('sad') else ''}"
    el.append(Paragraph(naglowek.strip(), s_tytul))

    data_fmt = _arch_mod._formatuj_date(w.get("data_orzeczenia", ""))
    praw = "prawomocne" if w.get("prawomocny") else "nieprawomocne"
    meta = (f"Data orzeczenia: <b>{data_fmt}</b>"
            f"&nbsp;&nbsp;•&nbsp;&nbsp;Podatek: <b>{w.get('podatek') or '—'}</b>"
            f"&nbsp;&nbsp;•&nbsp;&nbsp;Status: <b>{praw}</b>")
    el.append(Paragraph(meta, s_meta))
    if w.get("tresc_wyniku"):
        el.append(Paragraph(f"Rozstrzygnięcie: <b>{w['tresc_wyniku']}</b>", s_meta))

    def _akapity(tekst):
        return _arch_mod._oczysc_do_pdf(tekst)

    el.append(Paragraph("Sentencja", s_sekcja))
    for a in _akapity(w.get("sentencja", "")):
        el.append(Paragraph(a, s_tresc))

    if w.get("uzasadnienie"):
        el.append(Paragraph("Uzasadnienie", s_sekcja))
        for a in _akapity(w.get("uzasadnienie", "")):
            el.append(Paragraph(a, s_tresc))
    else:
        el.append(Paragraph("Uzasadnienie", s_sekcja))
        el.append(Paragraph("<i>Uzasadnienie nie zostało jeszcze opublikowane w CBOSA "
                            "(publikacja następuje często 2–3 miesiące po wyroku) "
                            "lub nie zostało sporządzone.</i>", s_tresc))

    if w.get("przepisy"):
        el.append(Paragraph("Powołane przepisy", s_sekcja))
        for a in _akapity(w.get("przepisy", "")):
            el.append(Paragraph(a, s_tresc))

    el.append(Spacer(1, 10))
    el.append(HRFlowable(width="100%", thickness=0.6, color=linia_szr, spaceAfter=6))
    if w.get("link"):
        el.append(Paragraph(f"Źródło: {w['link']}", s_stopka))
    el.append(Paragraph(
        "Dokument z bazy PickPivot (źródło: CBOSA — baza informacyjna, nie zbiór urzędowy). "
        "Przed wykorzystaniem zweryfikuj prawomocność i aktualność orzeczenia.",
        s_stopka))

    def _stopka(canvas, d):
        canvas.saveState()
        canvas.setFont(F_REG, 8)
        canvas.setFillColor(szary)
        canvas.drawRightString(A4[0]-2*cm, 1.2*cm, f"Strona {d.page}")
        canvas.drawString(2*cm, 1.2*cm, f"Wygenerowano: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        canvas.restoreState()

    doc.build(el, onFirstPage=_stopka, onLaterPages=_stopka)
    return bufor.getvalue()


@st.cache_data(show_spinner=False, max_entries=300)
def _pdf_wyroku_cached(doc_id: str, dane_json: str) -> bytes:
    import json
    return _generuj_pdf_wyroku(json.loads(dane_json))


# =============================================================================
# SEKCJE UI
# =============================================================================
def _renderuj_podsumowanie(db, dbw):
    st.markdown("### 📊 Podsumowanie archiwum wyroków")
    with st.spinner("Wczytuję statystyki..."):
        stats = dbw.statystyki_wyrokow(db)

    if stats["total"] == 0:
        st.info("Archiwum wyroków jest puste. Dane zasila cotygodniowa synchronizacja "
                "(GitHub Actions, niedziela) — możesz też uruchomić ją ręcznie "
                "z zakładki Actions (workflow Synchronizacja wyrokow CBOSA).")
        return

    st.metric("Łącznie wyroków w bazie", f"{stats['total']:,}".replace(",", " "))
    wiersze = []
    for p in stats["per_podatek"]:
        wiersze.append({
            "Podatek":      p["podatek"] or "(inny)",
            "Wyroków":      p["liczba"],
            "Z uzasadnieniem": p["kompletne"],
            "Czeka na uzasadnienie": p["oczekujace"],
            "Prawomocnych": p["prawomocne"],
            "Najstarszy":   _arch_mod._formatuj_date(p["najstarszy"]),
            "Najnowszy":    _arch_mod._formatuj_date(p["najnowszy"]),
        })
    st.dataframe(pd.DataFrame(wiersze), use_container_width=True, hide_index=True)
    st.caption("Status 'czeka na uzasadnienie' to normalny stan — sądy publikują uzasadnienia "
               "często 2–3 miesiące po wyroku; synchronizacja dociąga je automatycznie.")


def _renderuj_przegladarke(db, dbw):
    st.markdown("### 🔍 Przeglądaj wyroki")

    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2.4, 1])
    with c1:
        f_pod = st.selectbox("Podatek:", [None, "PIT", "CIT", "VAT", "AKCYZA"],
                             format_func=lambda x: "Wszystkie" if x is None else x,
                             key="wyr_pod")
    with c2:
        f_rok = st.selectbox("Rok:", [None, 2024, 2025, 2026],
                             format_func=lambda x: "Wszystkie" if x is None else str(x),
                             key="wyr_rok")
    with c3:
        f_mies = st.selectbox("Miesiąc:", [None] + list(range(1, 13)),
                              format_func=lambda x: "Wszystkie" if x is None else MIESIACE_PL[x-1],
                              key="wyr_mies")
    with c4:
        f_status = st.selectbox("Treść:", [None, "KOMPLETNY", "OCZEKUJE_NA_UZASADNIENIE",
                                            "BEZ_UZASADNIENIA_TRWALE"],
                                format_func=lambda x: "Wszystkie" if x is None else ETYKIETY_STATUSU[x],
                                key="wyr_status")
    with c5:
        st.markdown("<br>", unsafe_allow_html=True)
        szukaj = st.button("🔍 Szukaj", use_container_width=True, type="primary", key="wyr_szukaj")

    if szukaj:
        with st.spinner("Przeszukuję archiwum wyroków..."):
            wyniki = dbw.pobierz_wyroki(db, podatek=f_pod, rok=f_rok,
                                        miesiac=f_mies, status_tresci=f_status)
        st.session_state["wyr_wyniki"] = wyniki

    wyniki = st.session_state.get("wyr_wyniki", [])
    if not wyniki:
        if szukaj:
            st.warning("Brak wyroków dla wybranych kryteriów.")
        return

    st.success(f"⚖️ Znaleziono **{len(wyniki)}** wyroków")

    proporcje = [0.5, 3.2, 1.4, 1.3, 2.2, 1.4]
    naglowki = ["#", "Sygnatura / sąd", "Data", "Podatek", "Treść", ""]
    kol = st.columns(proporcje)
    for c, e in zip(kol, naglowki):
        c.markdown(f"<div style='font-weight:600;color:#8892A6;font-size:0.82rem;"
                   f"padding-bottom:6px;border-bottom:1px solid #333;'>{e}</div>",
                   unsafe_allow_html=True)

    import json
    for i, w in enumerate(wyniki):
        c_num, c_syg, c_data, c_pod, c_st, c_btn = st.columns(proporcje)
        styl = "padding-top:8px;padding-bottom:8px;"
        praw = " 🟢" if w.get("prawomocny") else ""
        c_num.markdown(f"<div style='{styl}color:#8892A6;'>{i+1}</div>", unsafe_allow_html=True)
        c_syg.markdown(f"<div style='{styl}'><b>{w['sygnatura']}</b><br>"
                       f"<span style='color:#8892A6;font-size:0.85rem;'>{w.get('rodzaj','')} · {w.get('sad','')}{praw}</span></div>",
                       unsafe_allow_html=True)
        c_data.markdown(f"<div style='{styl}'>{w['data_orzeczenia']}</div>", unsafe_allow_html=True)
        c_pod.markdown(f"<div style='{styl}'>{w.get('podatek') or '—'}</div>", unsafe_allow_html=True)
        c_st.markdown(f"<div style='{styl}'>{ETYKIETY_STATUSU.get(w.get('status_tresci'),'')}</div>",
                      unsafe_allow_html=True)
        try:
            dane = {k: w.get(k) for k in ("sygnatura","rodzaj","sad","data_orzeczenia",
                                           "podatek","prawomocny","tresc_wyniku",
                                           "sentencja","uzasadnienie","przepisy","link")}
            pdf = _pdf_wyroku_cached(w["id"], json.dumps(dane, ensure_ascii=False))
            nazwa = re.sub(r"[^A-Za-z0-9._-]", "_", w["sygnatura"] or w["id"]) + ".pdf"
            c_btn.download_button("📄 PDF", data=pdf, file_name=nazwa,
                                  mime="application/pdf",
                                  key=f"wpdf_{w['id']}_{i}", use_container_width=True)
        except Exception:
            c_btn.caption("⚠️ Błąd PDF")


def _renderuj_historie(db, dbw):
    with st.expander("🕘 Historia synchronizacji wyroków"):
        hist = dbw.pobierz_historie_sync_wyrokow(db, limit=40)
        if not hist:
            st.caption("Brak wpisów — synchronizacja jeszcze nie działała.")
            return
        df = pd.DataFrame(hist)
        df = df.rename(columns={
            "uruchomiono": "Kiedy", "strumien": "Strumień", "okno_od": "Od",
            "okno_do": "Do", "podatek": "Podatek", "znaleziono": "Znaleziono",
            "nowych": "Nowych", "zaktualizowanych": "Zaktualiz.", "status": "Status",
        })
        st.dataframe(df[["Kiedy","Strumień","Od","Do","Podatek","Znaleziono",
                          "Nowych","Zaktualiz.","Status"]],
                     use_container_width=True, hide_index=True)


# =============================================================================
def run_module():
    st.title("Wyroki Sądów Administracyjnych")
    st.caption("Archiwum orzeczeń WSA/NSA w sprawach podatkowych (źródło: CBOSA). "
               "Zasilane automatycznie w każdą niedzielę.")

    db, dbw = _wykryj_baze()
    if db is None:
        st.warning("Baza Supabase nie jest skonfigurowana — moduł wymaga sekcji "
                   "[supabase] w Streamlit Secrets.")
        return

    _arch_mod._zarejestruj_fonty()
    if not _arch_mod._font_polski_ok:
        st.warning("⚠️ Brak fontu z polskimi znakami — PDF-y mogą wyświetlać "
                   "znaki diakrytyczne niepoprawnie (folder fonts/ w repozytorium).")

    _renderuj_podsumowanie(db, dbw)
    st.markdown("---")
    _renderuj_przegladarke(db, dbw)
    st.markdown("---")
    _renderuj_historie(db, dbw)
