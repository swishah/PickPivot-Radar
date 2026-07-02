"""
raporty.py — Modul 2: Sciagacz Interpretacji.

Dwie sekcje:
  1. Raporty automatyczne (cotygodniowe, generowane przez cron w GitHub Actions)
     - NIEZMIENIONE: nadal generuja i przechowuja gotowe pliki Word.
  2. Pobieranie na zadanie (rok/miesiac/podatek wybrany recznie) - dwa tryby:
     a) "Pobierz teraz" - liczy sie w samej aplikacji Streamlit (czekasz na wynik)
     b) "Pobierz w tle" - wysyla zadanie do GitHub Actions (mozesz zamknac przegladarke)
     W obu trybach: interpretacje trafiaja do bazy danych, NIE generuje sie
     scalonego pliku Word - po zakonczeniu wysylane jest powiadomienie mailowe
     z podsumowaniem (liczba dokumentow, status weryfikacji).
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
# SEKCJA 1: RAPORTY AUTOMATYCZNE (TYGODNIOWE) — bez zmian
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
# SEKCJA 2: POBIERANIE NA ZADANIE (bez generowania pliku Word)
# =============================================================================
def _wyslij_github_dispatch(rok: int, miesiac: int, podatek: str) -> tuple:
    """
    Wysyla repository_dispatch event do GitHub Actions - uruchamia workflow w tle.
    Wymaga GITHUB_TOKEN i GITHUB_REPO w Streamlit Secrets.
    Zwraca (sukces: bool, komunikat: str).
    """
    try:
        token = str(st.secrets["github"]["token"]).strip()
        repo  = str(st.secrets["github"]["repo"]).strip()
    except Exception:
        return False, (
            "Brak konfiguracji GitHub w Secrets. Dodaj sekcje:\n"
            "[github]\ntoken = \"ghp_...\"\nrepo = \"uzytkownik/nazwa-repo\""
        )

    if repo.startswith("http") or "github.com" in repo:
        return False, (
            f"Pole 'repo' zawiera URL zamiast formatu 'login/nazwa-repo'. "
            f"Masz: \"{repo}\" — popraw na sam login i nazwe repozytorium, np. \"jankowalski/pickpivot\"."
        )
    if "/" not in repo:
        return False, (
            f"Pole 'repo' powinno miec format \"login/nazwa-repo\" (ze ukosnikiem). "
            f"Masz: \"{repo}\""
        )

    if not (token.startswith("github_pat_") or token.startswith("ghp_")):
        return False, (
            f"Token nie wyglada jak prawidlowy GitHub token (powinien zaczynac sie "
            f"od 'github_pat_' lub 'ghp_'). Sprawdz czy nie wkleiles przypadkiem "
            f"czegos innego. Pierwsze znaki Twojego tokena: \"{token[:15]}...\""
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
            return True, "Zadanie wyslane do GitHub Actions. Powiadomienie o wyniku przyjdzie mailem."
        elif r.status_code == 401:
            return False, (
                "GitHub odrzucil token (401 Bad credentials). Najczesciej oznacza to:\n"
                "1. Token zostal skopiowany z dodatkowa spacja/nowa linia (sprobuj wpisac na nowo, "
                "bez kopiowania calej linii z Secrets)\n"
                "2. Token wygasl lub zostal odwolany - sprawdz status 'Active' na "
                "github.com/settings/tokens?type=beta\n"
                "3. Token zostal wygenerowany ale GitHub jeszcze go nie aktywowal (rzadkie, "
                "poczekaj 1-2 minuty po wygenerowaniu)\n\n"
                f"Pierwsze 15 znakow uzytego tokena: \"{token[:15]}...\" (sprawdz czy sie zgadza)"
            )
        elif r.status_code == 404:
            return False, (
                f"GitHub zwrocilo 404 - nie znaleziono repozytorium \"{repo}\". "
                "Najczesciej oznacza to ze token nie ma dostepu do tego repo "
                "(sprawdz 'Repository access' przy generowaniu tokena) albo nazwa repo jest bledna."
            )
        elif r.status_code == 403:
            return False, (
                f"GitHub zwrocilo 403 - brak uprawnien. Sprawdz czy token ma "
                "uprawnienie 'Actions: Read and write' (Settings -> Permissions -> Actions)."
            )
        else:
            return False, f"GitHub API zwrocilo blad {r.status_code}: {r.text[:300]}"
    except Exception as e:
        return False, f"Blad polaczenia z GitHub API: {e}"


def _pobierz_na_zywo(rok: int, miesiac: int, podatek: str):
    """
    Pobiera interpretacje bezposrednio w aplikacji Streamlit, z paskiem
    postepu. NIE generuje pliku Word - tylko wgrywa dane do bazy i wysyla
    powiadomienie mailowe po zakonczeniu.
    """
    import raport_silnik as silnik
    import db_core

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
        wynik = silnik.generuj_raport_dla_podatku(
            db, pod, data_od, data_do, opis_okresu, log_fn=log_fn, generuj_plik=False
        )
        wyniki.append(wynik)
        pasek.progress((i + 1) / len(podatki))

    pasek.progress(1.0)
    log_kontener.empty()

    st.success(f"Gotowe! Interpretacje dla okresu {opis_okresu} zostaly wgrane do bazy.")

    for w in wyniki:
        wer = w.get("weryfikacja")
        wer_txt = ""
        if wer:
            mapa = {
                "OK": "✅ potwierdzona kompletność",
                "NIEZGODNOSC": f"⚠️ różnica: {wer['roznica']}",
                "WERYFIKACJA_NIEUDANA": "ℹ️ niepotwierdzone",
            }
            wer_txt = f" — {mapa.get(wer['status'], wer['status'])}"
        st.write(
            f"**{w['podatek']}**: {w['liczba_dok']} dokumentow w bazie "
            f"(nowo pobranych: {w['nowych_pobranych']}){wer_txt}"
        )

    # ── AUTOMATYCZNE POWIADOMIENIE MAILEM (bez zalacznika) ──────────────────
    gmail_adres = st.secrets.get("gmail", {}).get("adres", "")
    gmail_haslo = st.secrets.get("gmail", {}).get("haslo_aplikacji", "")
    odbiorca    = st.secrets.get("gmail", {}).get("odbiorca", gmail_adres)

    if gmail_adres and gmail_haslo:
        with st.spinner("Wysylam powiadomienie mailem..."):
            ok = silnik.wyslij_email_powiadomienie_pobrania(
                wyniki, opis_okresu, gmail_adres, gmail_haslo, odbiorca,
            )
        if ok:
            st.info(f"📧 Powiadomienie wyslane na {odbiorca}.")
        else:
            st.warning("Nie udalo sie wyslac powiadomienia mailem (pobieranie i tak sie powiodlo).")
    else:
        st.caption("Brak konfiguracji Gmail w Secrets — pomijam powiadomienie mailem.")

    # ── ZAPIS HISTORII (spojnosc z trybem "w tle") ──────────────────────────
    try:
        liczba_dok_lacznie = sum(w["liczba_dok"] for w in wyniki)
        statusy = [w["status"] for w in wyniki]
        if "ERROR" in statusy:
            status_koncowy = "ERROR"
        elif "NIEZGODNOSC" in statusy:
            status_koncowy = "NIEZGODNOSC"
        elif "WERYFIKACJA_NIEUDANA" in statusy:
            status_koncowy = "WERYFIKACJA_NIEUDANA"
        else:
            status_koncowy = "OK"
        db_core.zapisz_historie_raportu(
            db, rok=rok, miesiac=miesiac, podatek=podatek,
            liczba_dok=liczba_dok_lacznie, liczba_prob=1,
            status=status_koncowy, szczegoly="",
        )
        st.session_state.pop("historia_raportow_cache", None)  # wymus odswiezenie listy
    except Exception as e:
        st.caption(f"(historia nie zostala zapisana: {e})")


def _renderuj_pobieranie_na_zadanie():
    st.markdown("### 🎯 Pobieranie na żądanie")
    st.caption(
        "Wybierz rok, miesiąc i podatek — interpretacje zostaną pobrane i "
        "zapisane w bazie danych. Po zakończeniu dostaniesz powiadomienie "
        "mailem z podsumowaniem (bez załącznika)."
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

    st.markdown("**Tryb pobierania:**")
    tryb = st.radio(
        "Wybierz tryb:",
        ["⚡ Pobierz teraz (w aplikacji, czekasz na wynik)",
         "🌙 Pobierz w tle (GitHub Actions, powiadomienie mailem)"],
        label_visibility="collapsed",
        key="zad_tryb",
    )

    if st.button("🚀 Uruchom pobieranie", type="primary", use_container_width=True):
        if tryb.startswith("⚡"):
            _pobierz_na_zywo(rok, miesiac, podatek)
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

    st.markdown("---")
    _renderuj_historie_pobran()


# =============================================================================
# HISTORIA POBRAN — widoczna w aplikacji, oba tryby ("teraz" i "w tle")
# =============================================================================
def _renderuj_historie_pobran():
    """
    Pokazuje ostatnie uruchomienia pobierania na zadanie (oba tryby - zywy
    i w tle, oba zapisuja wpis do bazy) z wyraznym statusem weryfikacji
    kompletnosci.
    """
    st.markdown('### 📋 Historia pobrań')
    st.caption(
        "Status każdego pobrania — z informacją czy kompletność została "
        "potwierdzona drugą weryfikacją względem API Ministerstwa Finansów."
    )

    arch = _wykryj_archiwum()
    if arch is None:
        return

    if st.button("🔄 Odśwież historię", key="odswiez_historia"):
        st.session_state.pop("historia_raportow_cache", None)

    if "historia_raportow_cache" not in st.session_state:
        with st.spinner("Wczytuję historię..."):
            st.session_state["historia_raportow_cache"] = arch.pobierz_historie_raportow(limit=30)

    historia = st.session_state["historia_raportow_cache"]

    if not historia:
        st.info('Brak historii — uruchom pierwsze pobieranie na żądanie, żeby pojawił się tutaj wpis.')
        return

    miesiace_pl = ["Styczen","Luty","Marzec","Kwiecien","Maj","Czerwiec",
                   "Lipiec","Sierpien","Wrzesien","Pazdziernik","Listopad","Grudzien"]

    ikony_statusu = {
        "OK": "✅",
        "NIEZGODNOSC": "⚠️",
        "WERYFIKACJA_NIEUDANA": "ℹ️",
        "ERROR": "❌",
    }
    opisy_statusu = {
        "OK": "Potwierdzona kompletność",
        "NIEZGODNOSC": "Wykryto rozbieżność",
        "WERYFIKACJA_NIEUDANA": "Niepotwierdzone (MF niedostępne)",
        "ERROR": "Błąd pobierania",
    }

    for wpis in historia:
        ikona = ikony_statusu.get(wpis["status"], "❔")
        opis_status = opisy_statusu.get(wpis["status"], wpis["status"])
        okres_str = f"{miesiace_pl[wpis['miesiac']-1]} {wpis['rok']}"
        czas_str = wpis["uruchomiono"][:16].replace("T", " ")

        col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
        with col1:
            st.write(f"{ikona} **{wpis['podatek']}**")
        with col2:
            st.write(okres_str)
        with col3:
            st.write(f"{wpis['liczba_dok']} dok.")
        with col4:
            tekst_statusu = opis_status
            if wpis["liczba_prob"] > 1:
                tekst_statusu += f" (próby: {wpis['liczba_prob']})"
            st.write(tekst_statusu)

        if wpis["szczegoly"]:
            st.caption(f"　　Szczegóły: {wpis['szczegoly']}")
        st.caption(f"　　{czas_str}")


# =============================================================================
# GLOWNY MODUL
# =============================================================================
def run_module():
    st.title("Ściągacz Interpretacji")

    arch = _wykryj_archiwum()
    if arch is None:
        st.warning(
            "Archiwum Supabase nie jest skonfigurowane. "
            "Ten modul wymaga polaczenia z baza danych - "
            "skonfiguruj sekcje [supabase] w Streamlit Secrets."
        )
        return

    tab1, tab2 = st.tabs(["📅 Raporty automatyczne", "🎯 Pobieranie na żądanie"])

    with tab1:
        _renderuj_raporty_automatyczne(arch)

    with tab2:
        _renderuj_pobieranie_na_zadanie()
