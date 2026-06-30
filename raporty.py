"""
raporty.py — Modul 5: Raporty Tygodniowe + Raport na zadanie.

Dwie sekcje:
  1. Raporty automatyczne (cotygodniowe, generowane przez cron w GitHub Actions)
  2. Raport na zadanie (rok/miesiac/podatek wybrany recznie) - dwa tryby:
     a) "Generuj teraz" - liczy sie w samej aplikacji Streamlit (czekasz na wynik)
     b) "Generuj w tle" - wysyla zadanie do GitHub Actions (mozesz zamknac przegladarke,
        wynik przyjdzie mailem)
"""

import streamlit as st
import requests as http_requests
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


# =============================================================================
# SEKCJA 1: RAPORTY AUTOMATYCZNE (TYGODNIOWE)
# =============================================================================
def _renderuj_raporty_automatyczne(arch):
    st.markdown("### 📅 Raporty automatyczne (cotygodniowe)")
    st.caption(
        "Co weekend (sobota-poniedzialek) system automatycznie pobiera nowe "
        "interpretacje z mijajacego tygodnia i przygotowuje gotowe pliki Word."
    )

    with st.spinner("Wczytuje liste raportow..."):
        try:
            raporty = arch.pobierz_liste_raportow()
        except Exception as e:
            st.error(f"Blad pobierania listy raportow: {e}")
            return

    if not raporty:
        st.info(
            "Brak wygenerowanych raportow automatycznych. Pierwszy pojawi sie "
            "w najblizszy weekend (sobota 15:00 — poniedzialek 02:00)."
        )
        return

    tygodnie = {}
    for r in raporty:
        tygodnie.setdefault(r["tydzien_klucz"], []).append(r)
    klucze_posortowane = sorted(tygodnie.keys(), reverse=True)

    st.markdown(f"**Dostepne raporty:** {len(klucze_posortowane)} tygodni")

    for klucz_tyg in klucze_posortowane:
        raporty_tyg = tygodnie[klucz_tyg]
        okres = _formatuj_okres(raporty_tyg[0]["data_od"], raporty_tyg[0]["data_do"])
        suma_dok = sum(r["liczba_dok"] for r in raporty_tyg)

        with st.expander(f"📅 Tydzien {klucz_tyg} ({okres}) — {suma_dok} dokumentow", expanded=(klucz_tyg == klucze_posortowane[0])):
            cols = st.columns(len(raporty_tyg) if raporty_tyg else 1)
            for i, r in enumerate(sorted(raporty_tyg, key=lambda x: x["podatek"])):
                with cols[i % len(cols)]:
                    st.metric(r["podatek"], f"{r['liczba_dok']} dok.")
                    if r["liczba_dok"] > 0:
                        klucz_btn = f"pobierz_auto_{r['id']}"
                        if st.button(f"Pobierz {r['podatek']}", key=klucz_btn, use_container_width=True):
                            with st.spinner("Wczytuje plik..."):
                                plik_bytes, nazwa_pliku = arch.pobierz_plik_raportu(r["id"])
                            if plik_bytes:
                                st.session_state[f"plik_gotowy_{r['id']}"] = (plik_bytes, nazwa_pliku)
                            else:
                                st.error("Nie udalo sie wczytac pliku.")
                        if f"plik_gotowy_{r['id']}" in st.session_state:
                            plik_bytes, nazwa_pliku = st.session_state[f"plik_gotowy_{r['id']}"]
                            st.download_button(
                                "💾 Zapisz na dysk", data=plik_bytes, file_name=nazwa_pliku,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_auto_{r['id']}", use_container_width=True,
                            )
                    else:
                        st.caption("Brak dokumentow")
            st.caption(f"Wygenerowano: {raporty_tyg[0]['wygenerowano'][:16].replace('T',' ')}")


# =============================================================================
# SEKCJA 2: RAPORT NA ZADANIE
# =============================================================================
def _wyslij_github_dispatch(rok: int, miesiac: int, podatek: str) -> tuple:
    """
    Wysyla repository_dispatch event do GitHub Actions - uruchamia workflow w tle.
    Wymaga GITHUB_TOKEN i GITHUB_REPO w Streamlit Secrets.
    Zwraca (sukces: bool, komunikat: str).
    """
    try:
        token = st.secrets["github"]["token"]
        repo  = st.secrets["github"]["repo"]  # format: "uzytkownik/nazwa-repo"
    except Exception:
        return False, (
            "Brak konfiguracji GitHub w Secrets. Dodaj sekcje:\n"
            "[github]\ntoken = \"ghp_...\"\nrepo = \"uzytkownik/nazwa-repo\""
        )

    url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "event_type": "raport-na-zadanie",
        "client_payload": {
            "rok": str(rok),
            "miesiac": str(miesiac),
            "podatek": podatek,
        }
    }

    try:
        r = http_requests.post(url, headers=headers, json=payload, timeout=15)
        if r.status_code == 204:
            return True, "Zadanie wyslane do GitHub Actions. Wynik przyjdzie mailem za kilka minut."
        else:
            return False, f"GitHub API zwrocilo blad {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Blad polaczenia z GitHub API: {e}"


def _generuj_raport_na_zywo(rok: int, miesiac: int, podatek: str):
    """Generuje raport bezposrednio w aplikacji Streamlit, z paskiem postepu."""
    import raport_silnik as silnik
    import db_core

    # Pobierz polaczenie z baza (przez ten sam mechanizm co archiwum_supabase)
    try:
        s = dict(st.secrets["supabase"])
        db = db_core.SupabaseDB(s)
        db.inicjalizuj_schemat()
    except Exception as e:
        st.error(f"Blad polaczenia z baza: {e}")
        return

    data_od, data_do, opis_okresu = silnik.zakres_z_roku_miesiaca(rok, miesiac)
    podatki = silnik.PODATKI_WSZYSTKIE if podatek == "WSZYSTKIE" else [podatek]

    log_kontener = st.empty()
    log_lines = []

    def log_fn(msg):
        log_lines.append(msg)
        log_kontener.code("\n".join(log_lines[-15:]), language=None)

    pasek = st.progress(0)
    wyniki = []

    for i, pod in enumerate(podatki):
        st.write(f"**Przetwarzam {pod}...**")
        wynik = silnik.generuj_raport_dla_podatku(db, pod, data_od, data_do, opis_okresu, log_fn=log_fn)
        wyniki.append(wynik)
        pasek.progress((i + 1) / len(podatki))

    pasek.progress(1.0)
    log_kontener.empty()

    # Podsumowanie i przyciski pobierania
    st.success(f"Gotowe! Wygenerowano raport dla okresu: {opis_okresu}")

    for w in wyniki:
        col1, col2 = st.columns([3, 1])
        with col1:
            st.write(f"**{w['podatek']}**: {w['liczba_dok']} dokumentow")
        with col2:
            if w["plik_bytes"]:
                nazwa = f"Raport_{w['podatek']}_{opis_okresu.replace(' ', '_')}.docx"
                st.download_button(
                    "📥 Pobierz", data=w["plik_bytes"], file_name=nazwa,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"dl_zywo_{w['podatek']}",
                )

    # Opcjonalna wysylka mailem
    if any(w["plik_bytes"] for w in wyniki):
        if st.button("📧 Wyslij ten raport mailem", use_container_width=True):
            gmail_adres = st.secrets.get("gmail", {}).get("adres", "")
            gmail_haslo = st.secrets.get("gmail", {}).get("haslo_aplikacji", "")
            odbiorca    = st.secrets.get("gmail", {}).get("odbiorca", gmail_adres)

            if not gmail_adres or not gmail_haslo:
                st.error(
                    "Brak konfiguracji Gmail w Secrets. Dodaj sekcje:\n"
                    "[gmail]\nadres = \"twoj@gmail.com\"\nhaslo_aplikacji = \"...\"\nodbiorca = \"...\""
                )
            else:
                with st.spinner("Wysylam..."):
                    if len(wyniki) == 1 and wyniki[0]["plik_bytes"]:
                        w = wyniki[0]
                        nazwa = f"Raport_{w['podatek']}_{opis_okresu.replace(' ', '_')}.docx"
                        ok = silnik.wyslij_email_z_zalacznikiem(
                            w["plik_bytes"], nazwa, w["podatek"], opis_okresu, w["liczba_dok"],
                            gmail_adres, gmail_haslo, odbiorca,
                        )
                    else:
                        ok = silnik.wyslij_email_podsumowanie_wielu(
                            wyniki, opis_okresu, gmail_adres, gmail_haslo, odbiorca,
                        )
                if ok:
                    st.success(f"Wyslano na {odbiorca}!")
                else:
                    st.error("Blad wysylki — sprawdz konfiguracje Gmail.")


def _renderuj_raport_na_zadanie():
    st.markdown("### 🎯 Raport na żądanie")
    st.caption(
        "Wybierz rok, miesiąc i podatek — wygeneruj raport teraz w aplikacji "
        "lub zleć generowanie w tle przez GitHub Actions (wynik przyjdzie mailem)."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        rok = st.selectbox("Rok:", [2024, 2025, 2026], index=2, key="zad_rok")
    with col2:
        miesiace_pl = ["Styczen","Luty","Marzec","Kwiecien","Maj","Czerwiec",
                       "Lipiec","Sierpien","Wrzesien","Pazdziernik","Listopad","Grudzien"]
        miesiac_nazwa = st.selectbox("Miesiac:", miesiace_pl, key="zad_mies")
        miesiac = miesiace_pl.index(miesiac_nazwa) + 1
    with col3:
        podatek = st.selectbox(
            "Podatek:", ["VAT", "PIT", "CIT", "AKCYZA", "WSZYSTKIE"], key="zad_podatek"
        )

    st.markdown("**Tryb generowania:**")
    tryb = st.radio(
        "Wybierz tryb:",
        ["⚡ Generuj teraz (w aplikacji, czekasz na wynik)",
         "🌙 Generuj w tle (GitHub Actions, wynik mailem)"],
        label_visibility="collapsed",
        key="zad_tryb",
    )

    if st.button("🚀 Uruchom generowanie", type="primary", use_container_width=True):
        if tryb.startswith("⚡"):
            _generuj_raport_na_zywo(rok, miesiac, podatek)
        else:
            with st.spinner("Wysylam zadanie do GitHub Actions..."):
                sukces, komunikat = _wyslij_github_dispatch(rok, miesiac, podatek)
            if sukces:
                st.success(komunikat)
            else:
                st.error(komunikat)
                with st.expander("Jak skonfigurowac GitHub token?"):
                    st.markdown("""
1. Wejdz na [github.com/settings/tokens](https://github.com/settings/tokens?type=beta)
2. **Generate new token (fine-grained)**
3. Wybierz repozytorium z aplikacja PickPivot
4. Uprawnienia: **Actions** → Read and write
5. Skopiuj token i dodaj do Streamlit Secrets:
```toml
[github]
token = "github_pat_..."
repo = "twoj-login/nazwa-repo"
```
                    """)


# =============================================================================
# GLOWNY MODUL
# =============================================================================
def run_module():
    st.title("Raporty")

    arch = _wykryj_archiwum()
    if arch is None:
        st.warning(
            "Archiwum Supabase nie jest skonfigurowane. "
            "Raporty wymagaja polaczenia z baza danych - "
            "skonfiguruj sekcje [supabase] w Streamlit Secrets."
        )
        return

    tab1, tab2 = st.tabs(["📅 Raporty automatyczne", "🎯 Raport na żądanie"])

    with tab1:
        _renderuj_raporty_automatyczne(arch)

    with tab2:
        _renderuj_raport_na_zadanie()
