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

import streamlit as st
import pandas as pd
from datetime import datetime


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
            dane_tab = [
                {"#": i + 1, "Sygnatura": r["Sygnatura"], "Data wydania": r["Data"]}
                for i, r in enumerate(sorted(rekordy, key=lambda x: x["Data"], reverse=True))
            ]
            st.dataframe(
                pd.DataFrame(dane_tab),
                use_container_width=True,
                hide_index=True,
                height=min(400, 38 + len(dane_tab) * 35),
            )
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

    _renderuj_podsumowanie(arch)
    st.markdown("---")
    _renderuj_rozklad_czasowy(arch)
    st.markdown("---")
    _renderuj_przegladarke(arch)
