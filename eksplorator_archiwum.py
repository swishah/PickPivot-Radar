"""
eksplorator_archiwum.py — Modul: Archiwum Interpretacji.

Samodzielny modul informacyjny (bez akcji pobierania - to robi "Sciagacz
Interpretacji"). Odpowiada na pytanie: ile jest interpretacji w bazie,
z kiedy i z jakiego podatku.

Trzy sekcje:
  1. Podsumowanie - liczba dokumentow i zakres dat (najstarsza/najnowsza)
     per podatek, widoczne od razu bez klikania czegokolwiek.
  2. Rozklad w czasie - tabela liczby dokumentow per miesiac, z filtrem
     na podatek.
  3. Przegladaj szczegolowo - filtr rok/miesiac/podatek + lista sygnatur
     pasujacych dokumentow (dawny "Explorer Archiwum").
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
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


def _wykryj_archiwum():
    try:
        if "supabase" in st.secrets:
            s = st.secrets["supabase"]
            ma_host = bool(s.get("host","")) and bool(s.get("user","")) and bool(s.get("password",""))
            ma_url  = str(s.get("url","")).startswith("postgresql")
            if ma_host or ma_url:
                import archiwum_supabase as _arch
                return _arch
    except Exception:
        pass
    return None


MIESIACE_PL = ["Styczen","Luty","Marzec","Kwiecien","Maj","Czerwiec",
               "Lipiec","Sierpien","Wrzesien","Pazdziernik","Listopad","Grudzien"]


def _formatuj_date(data_str: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY, bezpiecznie obsluguje None/puste."""
    if not data_str:
        return "—"
    try:
        return datetime.strptime(str(data_str)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return str(data_str)


# =============================================================================
# GENEROWANIE PDF — pojedyncza interpretacja
# =============================================================================
_FONT_REGULAR = "Helvetica"
_FONT_BOLD    = "Helvetica-Bold"
_fonty_zarejestrowane = False
_font_polski_ok = False   # True tylko gdy realnie zaladowano font z polskimi znakami


def _sciezki_kandydatow_fontu():
    """
    Kolejnosc szukania fontu z pelnym wsparciem Unicode (polskie znaki
    diakrytyczne). NA PIERWSZYM MIEJSCU font dolaczony do repozytorium
    (folder 'fonts/' obok tego pliku) — to gwarantuje dzialanie niezaleznie
    od tego, co ma zainstalowane konkretne srodowisko hostingowe (Streamlit
    Community Cloud domyslnie NIE ma DejaVu Sans, stad wczesniejszy problem
    z brakiem polskich znakow — font cicho spadal na Helvetica, ktora ich
    nie obsluguje). Dopiero potem probujemy typowych sciezek systemowych
    jako dodatkowy fallback.
    """
    tu = os.path.dirname(os.path.abspath(__file__))
    return [
        (os.path.join(tu, "fonts", "DejaVuSans.ttf"),
         os.path.join(tu, "fonts", "DejaVuSans-Bold.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
         "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"),
    ]


def _zarejestruj_fonty():
    """
    Rejestruje font z pelnym wsparciem Unicode. W razie niepowodzenia
    NIE MILCZY — ustawia _font_polski_ok=False, co run_module() zamienia
    na widoczne ostrzezenie w aplikacji. Wczesniej brak fontu byl cichy,
    przez co wadliwe PDF-y (bez polskich znakow) moglo pobrac wiele osob,
    zanim ktokolwiek to zauwazyl.
    """
    global _FONT_REGULAR, _FONT_BOLD, _fonty_zarejestrowane, _font_polski_ok
    if _fonty_zarejestrowane:
        return

    for regularny, pogrubiony in _sciezki_kandydatow_fontu():
        try:
            if os.path.exists(regularny):
                pdfmetrics.registerFont(TTFont("DejaVuSans", regularny))
                _FONT_REGULAR = "DejaVuSans"
                if pogrubiony and os.path.exists(pogrubiony):
                    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", pogrubiony))
                    _FONT_BOLD = "DejaVuSans-Bold"
                else:
                    _FONT_BOLD = "DejaVuSans"
                _font_polski_ok = True
                break
        except Exception:
            continue

    _fonty_zarejestrowane = True


def _oczysc_do_pdf(tekst: str) -> list:
    """
    Dzieli surowy tekst interpretacji na akapity (po pustych liniach)
    i eskejpuje znaki specjalne wymagane przez mini-jezyk znacznikowy
    Paragraph z reportlab (<, >, &).
    """
    if not tekst or not str(tekst).strip():
        return ["<i>Brak treści dokumentu w bazie.</i>"]

    bloki = re.split(r"\n\s*\n", str(tekst).strip())
    akapity = []
    for blok in bloki:
        linia = " ".join(l.strip() for l in blok.splitlines() if l.strip())
        if not linia:
            continue
        linia = (linia.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;"))
        akapity.append(linia)
    return akapity or ["<i>Brak treści dokumentu w bazie.</i>"]


def _generuj_pdf_interpretacji(sygnatura: str, podatek: str, data_wyd: str,
                               tekst: str, link: str) -> bytes:
    """Buduje ladnie sformatowany PDF pojedynczej interpretacji. Zwraca bajty."""
    _zarejestruj_fonty()

    granatowy = colors.HexColor("#1B2A4A")
    szary     = colors.HexColor("#5A6472")
    linia_szr = colors.HexColor("#C7CCD6")

    bufor = io.BytesIO()
    doc = SimpleDocTemplate(
        bufor, pagesize=A4,
        topMargin=2.2 * cm, bottomMargin=2 * cm,
        leftMargin=2 * cm, rightMargin=2 * cm,
        title=f"Interpretacja {sygnatura}",
    )

    styl_marka = ParagraphStyle(
        "MarkaNaglowek", fontName=_FONT_REGULAR, fontSize=9,
        textColor=szary, spaceAfter=2,
    )
    styl_tytul = ParagraphStyle(
        "Tytul", fontName=_FONT_BOLD, fontSize=18,
        textColor=granatowy, spaceAfter=6, leading=22,
    )
    styl_meta = ParagraphStyle(
        "Meta", fontName=_FONT_REGULAR, fontSize=10.5,
        textColor=szary, spaceAfter=14, leading=15,
    )
    styl_sekcja = ParagraphStyle(
        "Sekcja", fontName=_FONT_BOLD, fontSize=12,
        textColor=granatowy, spaceBefore=6, spaceAfter=10,
    )
    styl_tresc = ParagraphStyle(
        "Tresc", fontName=_FONT_REGULAR, fontSize=10.3,
        leading=15.5, spaceAfter=9, alignment=4,  # 4 = justowanie
        textColor=colors.HexColor("#1A1A1A"),
    )
    styl_stopka_notka = ParagraphStyle(
        "StopkaNotka", fontName=_FONT_REGULAR, fontSize=8.3,
        textColor=szary, spaceBefore=4, leading=11,
    )

    elementy = []
    elementy.append(Paragraph("PickPivot — Archiwum Interpretacji Indywidualnych", styl_marka))
    elementy.append(HRFlowable(width="100%", thickness=1.1, color=granatowy, spaceAfter=10))
    elementy.append(Paragraph(sygnatura or "—", styl_tytul))

    data_fmt = _formatuj_date(data_wyd)
    meta_linia = (f"Podatek: <b>{podatek or '—'}</b>"
                  f"&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;"
                  f"Data wydania: <b>{data_fmt}</b>")
    elementy.append(Paragraph(meta_linia, styl_meta))

    elementy.append(Paragraph("Treść interpretacji", styl_sekcja))
    for akapit in _oczysc_do_pdf(tekst):
        elementy.append(Paragraph(akapit, styl_tresc))

    elementy.append(Spacer(1, 10))
    elementy.append(HRFlowable(width="100%", thickness=0.6, color=linia_szr, spaceAfter=6))
    if link:
        link_bezp = str(link).replace("&", "&amp;")
        elementy.append(Paragraph(f"Źródło: {link_bezp}", styl_stopka_notka))
    elementy.append(Paragraph(
        "Dokument pobrany z bazy PickPivot. Przed wykorzystaniem zweryfikuj "
        "aktualność interpretacji (możliwe uchylenie lub zmiana).",
        styl_stopka_notka,
    ))

    def _stopka(canvas, doc_):
        canvas.saveState()
        canvas.setFont(_FONT_REGULAR, 8)
        canvas.setFillColor(szary)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Strona {doc_.page}")
        canvas.drawString(2 * cm, 1.2 * cm,
                          f"Wygenerowano: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        canvas.restoreState()

    doc.build(elementy, onFirstPage=_stopka, onLaterPages=_stopka)
    return bufor.getvalue()


@st.cache_data(show_spinner=False, max_entries=300)
def _generuj_pdf_interpretacji_cached(doc_id: str, sygnatura: str, podatek: str,
                                      data_wyd: str, tekst: str, link: str) -> bytes:
    """
    Wersja cachowana — przy kazdym rerunie Streamlit (np. zmiana innego
    filtra na stronie) wszystkie widoczne przyciski download_button musza
    ponownie obliczyc swoje dane; cache_data sprawia, ze PDF liczy sie
    naprawde tylko raz na dokument, a kolejne rerendery uzywaja wyniku
    z pamieci zamiast generowac PDF od nowa.
    """
    return _generuj_pdf_interpretacji(sygnatura, podatek, data_wyd, tekst, link)


def _nazwa_pliku_pdf(sygnatura: str, doc_id: str) -> str:
    baza = sygnatura or doc_id or "interpretacja"
    bezpieczna = re.sub(r"[^A-Za-z0-9._-]", "_", baza)
    return f"{bezpieczna}.pdf"


# =============================================================================
# SEKCJA 1: PODSUMOWANIE — widoczne od razu
# =============================================================================
def _renderuj_podsumowanie(arch):
    st.markdown("### 📊 Podsumowanie bazy")

    with st.spinner("Wczytuję statystyki..."):
        stats = arch.statystyki_szczegolowe()

    if stats["total"] == 0:
        st.info("Baza jest pusta — brak interpretacji do wyświetlenia. Użyj modułu \"Ściągacz Interpretacji\", żeby pobrać dane.")
        return

    st.metric("Łącznie interpretacji w bazie", f"{stats['total']:,}".replace(",", " "))

    st.markdown("**Podział na podatki:**")

    # Tabela: podatek | liczba | najstarsza | najnowsza
    wiersze = []
    for p in stats["per_podatek"]:
        wiersze.append({
            "Podatek":            p["podatek"],
            "Liczba interpretacji": p["liczba"],
            "Najstarsza (data wydania)": _formatuj_date(p["najstarsza"]),
            "Najnowsza (data wydania)":  _formatuj_date(p["najnowsza"]),
        })

    df = pd.DataFrame(wiersze)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Cztery metryki obok siebie dla szybkiego przegladu
    cols = st.columns(4)
    mapa_podatkow = {p["podatek"]: p["liczba"] for p in stats["per_podatek"]}
    for i, pod in enumerate(["PIT", "CIT", "VAT", "AKCYZA"]):
        with cols[i]:
            st.metric(pod, mapa_podatkow.get(pod, 0))


# =============================================================================
# SEKCJA 2: ROZKLAD W CZASIE
# =============================================================================
def _renderuj_rozklad_czasowy(arch):
    st.markdown("### 📈 Rozkład w czasie")
    st.caption("Liczba interpretacji per miesiąc — pomaga zobaczyć, które okresy są już dobrze pokryte, a które wymagają uzupełnienia.")

    filtr_podatek = st.selectbox(
        "Filtruj wg podatku:", ["Wszystkie", "PIT", "CIT", "VAT", "AKCYZA"],
        key="arch_rozklad_podatek"
    )
    podatek_param = None if filtr_podatek == "Wszystkie" else filtr_podatek

    with st.spinner("Wczytuję rozkład..."):
        dane = arch.rozklad_miesieczny(podatek=podatek_param)

    if not dane:
        st.info("Brak danych do pokazania rozkładu.")
        return

    df = pd.DataFrame(dane)
    df = df.rename(columns={"podatek": "Podatek", "rok_miesiac": "Miesiąc", "liczba": "Liczba"})

    if filtr_podatek == "Wszystkie":
        # Pivot: wiersze = miesiac, kolumny = podatek, wartosci = liczba
        pivot = df.pivot_table(index="Miesiąc", columns="Podatek", values="Liczba", fill_value=0)
        pivot = pivot.sort_index(ascending=False)
        st.dataframe(pivot, use_container_width=True)

        # Prosty wykres slupkowy sumarycznej liczby per miesiac
        suma_per_miesiac = df.groupby("Miesiąc")["Liczba"].sum().sort_index()
        st.bar_chart(suma_per_miesiac)
    else:
        df_sorted = df.sort_values("Miesiąc", ascending=False)[["Miesiąc", "Liczba"]]
        st.dataframe(df_sorted, use_container_width=True, hide_index=True)
        st.bar_chart(df_sorted.set_index("Miesiąc")["Liczba"])


# =============================================================================
# SEKCJA 3: PRZEGLADAJ SZCZEGOLOWO (drill-down, dawny Explorer Archiwum)
# =============================================================================
def _renderuj_przegladarke(arch):
    st.markdown("### 🔍 Przeglądaj szczegółowo")
    st.caption("Wybierz rok, miesiąc i/lub podatek, żeby zobaczyć listę konkretnych sygnatur.")

    col_f1, col_f2, col_f3, col_btn = st.columns([2, 2, 2, 1])
    with col_f1:
        filtr_pod = st.multiselect(
            "Podatek:", ["PIT", "CIT", "VAT", "AKCYZA"],
            key="arch_pod", placeholder="Wszystkie"
        )
    with col_f2:
        filtr_rok = st.selectbox(
            "Rok:", [None, 2024, 2025, 2026],
            format_func=lambda x: "Wszystkie" if x is None else str(x),
            key="arch_rok"
        )
    with col_f3:
        filtr_mies = st.selectbox(
            "Miesiąc:", [None] + list(range(1, 13)),
            format_func=lambda x: "Wszystkie" if x is None else MIESIACE_PL[x - 1],
            key="arch_mies"
        )
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        szukaj = st.button("🔍 Szukaj", use_container_width=True, type="primary")

    if szukaj:
        with st.spinner("Przeszukuję archiwum..."):
            wyniki = []
            for pod in (filtr_pod or [None]):
                wyniki += arch.pobierz_rekordy_z_archiwum(
                    podatek=pod, rok=filtr_rok, miesiac=filtr_mies)
            seen, unikalne = set(), []
            for r in wyniki:
                rid = arch._id_z_rekordu(r)
                if rid not in seen:
                    seen.add(rid)
                    unikalne.append(r)
        st.session_state["arch_podglad"]       = unikalne
        st.session_state["arch_podglad_filtr"] = {
            "pod": filtr_pod, "rok": filtr_rok, "mies": filtr_mies
        }

    podglad = st.session_state.get("arch_podglad", [])
    filtr_info = st.session_state.get("arch_podglad_filtr", {})

    if podglad:
        pod_str  = "/".join(filtr_info.get("pod") or ["wszystkie podatki"])
        rok_str  = str(filtr_info["rok"]) if filtr_info.get("rok") else "wszystkie lata"
        mies_str = MIESIACE_PL[filtr_info["mies"] - 1] if filtr_info.get("mies") else "wszystkie miesiące"
        st.success(f"📄 Znaleziono **{len(podglad)}** interpretacji — {pod_str} / {rok_str} / {mies_str}")

        grupy = {}
        for r in podglad:
            grupy.setdefault(r["Podatek"], []).append(r)

        for podatek, rekordy in sorted(grupy.items()):
            st.markdown(f"**{podatek}** — {len(rekordy)} interpretacji")
            rekordy_sort = sorted(rekordy, key=lambda x: x["Data"], reverse=True)

            # Proporcje kolumn: # | Sygnatura | Data wydania | (przycisk PDF)
            proporcje = [0.6, 6, 1.7, 1.6]

            naglowek = st.columns(proporcje)
            for col, etykieta in zip(naglowek, ["#", "Sygnatura", "Data wydania", ""]):
                col.markdown(
                    f"<div style='font-weight:600;color:#8892A6;font-size:0.82rem;"
                    f"padding-bottom:6px;border-bottom:1px solid #333;'>{etykieta}</div>",
                    unsafe_allow_html=True,
                )

            for i, r in enumerate(rekordy_sort):
                c_num, c_syg, c_data, c_btn = st.columns(proporcje)

                styl_kom = "padding-top:8px;padding-bottom:8px;"
                c_num.markdown(f"<div style='{styl_kom}color:#8892A6;'>{i + 1}</div>",
                                unsafe_allow_html=True)
                c_syg.markdown(f"<div style='{styl_kom}'>{r['Sygnatura']}</div>",
                                unsafe_allow_html=True)
                c_data.markdown(f"<div style='{styl_kom}'>{r['Data']}</div>",
                                unsafe_allow_html=True)

                doc_id = arch._id_z_rekordu(r)
                try:
                    pdf_bytes = _generuj_pdf_interpretacji_cached(
                        doc_id,
                        r.get("Sygnatura", ""), r.get("Podatek", ""),
                        r.get("Data", ""), r.get("Tekst", ""), r.get("Link", ""),
                    )
                    c_btn.download_button(
                        "📄 PDF",
                        data=pdf_bytes,
                        file_name=_nazwa_pliku_pdf(r.get("Sygnatura", ""), doc_id),
                        mime="application/pdf",
                        key=f"pdf_{podatek}_{doc_id}_{i}",
                        use_container_width=True,
                    )
                except Exception:
                    c_btn.caption("⚠️ Błąd PDF")
    elif szukaj:
        st.warning("Brak interpretacji w archiwum dla wybranych kryteriów.")


# =============================================================================
# GLOWNY MODUL
# =============================================================================
def run_module():
    st.title("Archiwum Interpretacji")
    st.caption("Podgląd zawartości bazy — bez pobierania nowych danych (do tego służy moduł \"Ściągacz Interpretacji\").")

    arch = _wykryj_archiwum()
    if arch is None:
        st.warning(
            "Archiwum Supabase nie jest skonfigurowane. "
            "Ten modul wymaga polaczenia z baza danych - "
            "skonfiguruj sekcje [supabase] w Streamlit Secrets."
        )
        return

    _zarejestruj_fonty()
    if not _font_polski_ok:
        st.warning(
            "⚠️ Nie znaleziono fontu z polskimi znakami diakrytycznymi — "
            "wygenerowane PDF-y mogą wyświetlać ą/ć/ę/ł/ń/ó/ś/ź/ż niepoprawnie. "
            "Dodaj folder `fonts/` z plikami DejaVuSans.ttf i DejaVuSans-Bold.ttf "
            "obok pliku eksplorator_archiwum.py w repozytorium.",
            icon="⚠️",
        )

    _renderuj_podsumowanie(arch)
    st.markdown("---")
    _renderuj_rozklad_czasowy(arch)
    st.markdown("---")
    _renderuj_przegladarke(arch)
