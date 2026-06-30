"""
raporty.py — Modul 5: Raporty Tygodniowe.

Wyswietla liste gotowych plikow Word wygenerowanych automatycznie
przez GitHub Actions (raport_tygodniowy.py) co weekend.
"""

import streamlit as st
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


def _formatuj_okres(data_od: str, data_do: str) -> str:
    try:
        d1 = datetime.strptime(data_od, "%Y-%m-%d")
        d2 = datetime.strptime(data_do, "%Y-%m-%d")
        return f"{d1.strftime('%d.%m')} — {d2.strftime('%d.%m.%Y')}"
    except Exception:
        return f"{data_od} — {data_do}"


def run_module():
    st.title("Raporty Tygodniowe")
    st.caption(
        "Co weekend (sobota-poniedzialek) system automatycznie pobiera nowe "
        "interpretacje z mijajacego tygodnia (poniedzialek-piatek) i przygotowuje "
        "gotowe pliki Word dla kazdego podatku. Pliki czekaja tutaj na pobranie."
    )

    arch = _wykryj_archiwum()
    if arch is None:
        st.warning(
            "Archiwum Supabase nie jest skonfigurowane. "
            "Raporty tygodniowe wymagaja polaczenia z baza danych - "
            "skonfiguruj sekcje [supabase] w Streamlit Secrets."
        )
        return

    with st.spinner("Wczytuje liste raportow..."):
        try:
            raporty = arch.pobierz_liste_raportow()
        except Exception as e:
            st.error(f"Blad pobierania listy raportow: {e}")
            return

    if not raporty:
        st.info(
            "Brak wygenerowanych raportow. Pierwszy raport pojawi sie automatycznie "
            "w najblizszy weekend (sobota 15:00 — poniedzialek 02:00)."
        )
        with st.expander("Jak dziala automatyzacja?"):
            st.markdown("""
**Harmonogram:** GitHub Actions uruchamia skrypt kilkukrotnie w oknie
sobota 15:00 — poniedzialek 02:00, zeby zminimalizowac ryzyko utraty
raportu przez chwilowa awarie API Ministerstwa Finansow.

**Co robi skrypt:**
1. Wyznacza zakres poprzedniego tygodnia (poniedzialek-piatek)
2. Dla kazdego podatku (PIT, CIT, VAT, AKCYZA) sprawdza i uzupelnia
   archiwum o nowe interpretacje z tego okresu
3. Generuje 4 osobne pliki Word (po jednym na podatek)
4. Zapisuje je w bazie Supabase
5. Wysyla e-mail z podsumowaniem

**Recznie:** Mozesz tez uruchomic generowanie raportu recznie z zakladki
**Actions** w repozytorium GitHub — przycisk "Run workflow".
            """)
        return

    # Grupuj raporty wg tygodnia
    tygodnie = {}
    for r in raporty:
        tygodnie.setdefault(r["tydzien_klucz"], []).append(r)

    # Sortuj tygodnie malejaco (najnowsze pierwsze)
    klucze_posortowane = sorted(tygodnie.keys(), reverse=True)

    st.markdown(f"### Dostepne raporty ({len(klucze_posortowane)} tygodni)")

    for klucz_tyg in klucze_posortowane:
        raporty_tyg = tygodnie[klucz_tyg]
        # Wszystkie raporty w tym tygodniu maja te same daty od/do
        okres = _formatuj_okres(raporty_tyg[0]["data_od"], raporty_tyg[0]["data_do"])
        suma_dok = sum(r["liczba_dok"] for r in raporty_tyg)

        with st.expander(f"📅 Tydzien {klucz_tyg} ({okres}) — {suma_dok} dokumentow lacznie", expanded=(klucz_tyg == klucze_posortowane[0])):

            cols = st.columns(len(raporty_tyg) if raporty_tyg else 1)

            for i, r in enumerate(sorted(raporty_tyg, key=lambda x: x["podatek"])):
                with cols[i % len(cols)]:
                    st.metric(r["podatek"], f"{r['liczba_dok']} dok.")

                    if r["liczba_dok"] > 0:
                        # Przycisk pobierania - lazy load pliku (dopiero przy kliknieciu)
                        klucz_btn = f"pobierz_{r['id']}"

                        if st.button(f"Pobierz {r['podatek']}", key=klucz_btn, use_container_width=True):
                            with st.spinner("Wczytuje plik..."):
                                plik_bytes, nazwa_pliku = arch.pobierz_plik_raportu(r["id"])

                            if plik_bytes:
                                st.session_state[f"plik_gotowy_{r['id']}"] = (plik_bytes, nazwa_pliku)
                            else:
                                st.error("Nie udalo sie wczytac pliku.")

                        # Jesli plik zostal juz wczytany - pokaz prawdziwy download_button
                        if f"plik_gotowy_{r['id']}" in st.session_state:
                            plik_bytes, nazwa_pliku = st.session_state[f"plik_gotowy_{r['id']}"]
                            st.download_button(
                                "💾 Zapisz na dysk",
                                data=plik_bytes,
                                file_name=nazwa_pliku,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_{r['id']}",
                                use_container_width=True,
                            )
                    else:
                        st.caption("Brak dokumentow w tym tygodniu")

            st.caption(f"Wygenerowano: {raporty_tyg[0]['wygenerowano'][:16].replace('T', ' ')}")
