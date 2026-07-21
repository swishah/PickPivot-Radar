# -*- coding: utf-8 -*-
"""
MODUŁ 9: Aktywność systemu — Skaner Doradca.
Dostępny dla wszystkich zalogowanych. Dwie części:

  • HARMONOGRAMY — „następne uruchomienia” automatów, wyliczane z realnych
    plików .github/workflows/*.yml (parsowanie wpisów cron). Czasy w UTC
    przeliczane na czas polski (z uwzględnieniem zmiany czasu).
  • KRONIKA — oś czasu odtworzona z bazy: ile interpretacji dograło się per
    podatek (dokumenty.pobrano_at) i ile streszczeń powstało
    (streszczenia_auto.wygenerowano), dzień po dniu.

Uwaga: to rekonstrukcja z danych w bazie — pokazuje FAKTY (co dograło się,
co streszczono), ale nie błędy procesów (nieudana synchronizacja nie zostawia
śladu; widać ją jako „0 nowych”, nie jako błąd).

Powiadomienie: podsumowanie_dzis() zasila st.toast wywoływany w app.py.
"""

from __future__ import annotations

import datetime as dt
import glob
import os
import re

import streamlit as st

import archiwum_supabase

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover
    _TZ = None

PODATKI = ["PIT", "CIT", "VAT", "AKCYZA"]
DNI_KRONIKI = 14


def _zapytaj(sql: str, p: tuple | None = None) -> list[dict]:
    return archiwum_supabase._get_db().wykonaj(sql, p, fetch=True)


# ---------------------------------------------------------------------------
# HARMONOGRAMY (parsowanie cron z workflow)
# ---------------------------------------------------------------------------
def _katalog_workflow() -> str:
    baza = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(baza, ".github", "workflows")


def _rozwin_pole(pole: str, lo: int, hi: int) -> list[int]:
    """Rozwija pole cron: '*', 'a', 'a-b', 'a,b', '*/n', 'a-b/n'."""
    wynik: set[int] = set()
    for czesc in pole.split(","):
        krok = 1
        if "/" in czesc:
            czesc, k = czesc.split("/"); krok = int(k)
        if czesc == "*":
            a, b = lo, hi
        elif "-" in czesc:
            a, b = map(int, czesc.split("-"))
        else:
            a = b = int(czesc)
        wynik.update(range(a, b + 1, krok))
    return sorted(x for x in wynik if lo <= x <= hi)


def _nastepne_z_cron(cron: str, teraz_utc: dt.datetime) -> dt.datetime | None:
    """Najbliższe uruchomienie (UTC) dla wyrażenia 'M H D M W'.
    Obsługuje harmonogramy dzienne (D, M, W = '*')."""
    pola = cron.split()
    if len(pola) != 5:
        return None
    minuty = _rozwin_pole(pola[0], 0, 59)
    godziny = _rozwin_pole(pola[1], 0, 23)
    if not minuty or not godziny:
        return None
    czasy = sorted((h, m) for h in godziny for m in minuty)
    for dzien in range(0, 8):
        d = (teraz_utc + dt.timedelta(days=dzien)).date()
        for h, m in czasy:
            kand = dt.datetime(d.year, d.month, d.day, h, m,
                               tzinfo=dt.timezone.utc)
            if kand > teraz_utc:
                return kand
    return None


def _harmonogramy() -> list[dict]:
    """Lista {nazwa, nastepne_utc, crony} z plików workflow."""
    kat = _katalog_workflow()
    pliki = sorted(glob.glob(os.path.join(kat, "*.yml"))
                   + glob.glob(os.path.join(kat, "*.yaml")))
    teraz = dt.datetime.now(dt.timezone.utc)
    wynik = []
    for p in pliki:
        try:
            tekst = open(p, encoding="utf-8").read()
        except Exception:
            continue
        crony = re.findall(r"cron:\s*['\"]([^'\"]+)['\"]", tekst)
        # Workflow uruchamiane łańcuchowo: "workflows: [\"Nazwa rodzica\"]"
        m_run = re.search(r"workflow_run:.*?workflows:\s*\[([^\]]*)\]", tekst, re.S)
        rodzice = []
        if m_run:
            rodzice = [x.strip().strip("'\"") for x in m_run.group(1).split(",")
                       if x.strip()]
        if not crony and not rodzice:
            continue  # workflow tylko z ręcznym dispatch — bez harmonogramu

        m = re.search(r"^name:\s*(.+)$", tekst, re.M)
        nazwa = m.group(1).strip().strip("'\"") if m else os.path.basename(p)

        # Godziny (UTC) rozwinięte ze wszystkich wpisów cron -> (h, m)
        godziny = set()
        for c in crony:
            pola = c.split()
            if len(pola) == 5:
                for h in _rozwin_pole(pola[1], 0, 23):
                    for mi in _rozwin_pole(pola[0], 0, 59):
                        godziny.add((h, mi))
        nastepne = [x for x in (_nastepne_z_cron(c, teraz) for c in crony) if x]
        wynik.append({
            "nazwa": nazwa,
            "plik": os.path.basename(p),
            "godziny_utc": sorted(godziny),
            "rodzice": rodzice,
            "nastepne_utc": min(nastepne) if nastepne else None,
            "crony": crony,
        })
    # Kolejność: najpierw harmonogramy godzinowe (wg najbliższego), potem
    # łańcuchowe w kolejności zależności (rodzic przed dzieckiem).
    po_nazwie = {r["nazwa"]: r for r in wynik}

    def _glebokosc(r, _widziane=None):
        if r["godziny_utc"]:
            return 0
        _widziane = _widziane or set()
        if r["nazwa"] in _widziane:
            return 99
        _widziane.add(r["nazwa"])
        gl = [_glebokosc(po_nazwie[n], _widziane) for n in r["rodzice"]
              if n in po_nazwie]
        return 1 + (max(gl) if gl else 0)

    for r in wynik:
        r["_glebokosc"] = _glebokosc(r)
    wynik.sort(key=lambda r: (
        r["_glebokosc"],
        r["nastepne_utc"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
    return wynik


def _godziny_pl(godziny_utc: list) -> str:
    """Lista godzin UTC -> napis 'HH:MM, HH:MM…' w czasie polskim."""
    if not godziny_utc or not _TZ:
        if not godziny_utc:
            return "—"
    dzis = dt.datetime.now(dt.timezone.utc).date()
    czasy = []
    for h, mi in godziny_utc:
        u = dt.datetime(dzis.year, dzis.month, dzis.day, h, mi,
                        tzinfo=dt.timezone.utc)
        lok = u.astimezone(_TZ) if _TZ else u
        czasy.append(lok.strftime("%H:%M"))
    return ", ".join(sorted(set(czasy)))


def _na_czas_pl(u: dt.datetime | None) -> str:
    if not u:
        return "—"
    lokalny = u.astimezone(_TZ) if _TZ else u
    dni = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"]
    return f"{dni[lokalny.weekday()]} {lokalny:%d.%m, %H:%M}"


def _za_ile(u: dt.datetime | None) -> str:
    if not u:
        return ""
    delta = u - dt.datetime.now(dt.timezone.utc)
    sek = int(delta.total_seconds())
    if sek < 0:
        return ""
    h, m = sek // 3600, (sek % 3600) // 60
    if h >= 24:
        return f"za {h // 24} d {h % 24} h"
    if h:
        return f"za {h} h {m} min"
    return f"za {m} min"


# ---------------------------------------------------------------------------
# KRONIKA (z bazy)
# ---------------------------------------------------------------------------
def _polnoc_pl_iso() -> str:
    """Północ dzisiejszego dnia (czas polski) jako ISO — próg „dziś”."""
    teraz = dt.datetime.now(_TZ) if _TZ else dt.datetime.now()
    polnoc = teraz.replace(hour=0, minute=0, second=0, microsecond=0)
    return polnoc.isoformat()


@st.cache_data(ttl=300, show_spinner=False)
def _kronika_interpretacje() -> list[dict]:
    prog = (dt.datetime.now() - dt.timedelta(days=DNI_KRONIKI)).isoformat()
    return _zapytaj(
        """SELECT pobrano_at::date AS dzien, podatek, COUNT(*) AS n
           FROM dokumenty
           WHERE pobrano_at IS NOT NULL AND pobrano_at >= %s
           GROUP BY pobrano_at::date, podatek
           ORDER BY dzien DESC""",
        (prog,),
    )


@st.cache_data(ttl=300, show_spinner=False)
def _kronika_streszczenia() -> list[dict]:
    prog = (dt.datetime.now() - dt.timedelta(days=DNI_KRONIKI)).date().isoformat()
    return _zapytaj(
        """SELECT LEFT(wygenerowano, 10) AS dzien, podatek, COUNT(*) AS n
           FROM streszczenia_auto
           WHERE LEFT(wygenerowano, 10) >= %s
           GROUP BY LEFT(wygenerowano, 10), podatek
           ORDER BY dzien DESC""",
        (prog,),
    )


def _scal_po_dniach(interp: list[dict], stresz: list[dict]) -> list[dict]:
    dni: dict[str, dict] = {}
    for r in interp:
        d = str(r["dzien"])[:10]
        dni.setdefault(d, {"interp": {}, "stresz": {}})
        dni[d]["interp"][r["podatek"]] = int(r["n"])
    for r in stresz:
        d = str(r["dzien"])[:10]
        dni.setdefault(d, {"interp": {}, "stresz": {}})
        dni[d]["stresz"][r["podatek"]] = int(r["n"])
    return [{"dzien": d, **v} for d, v in sorted(dni.items(), reverse=True)]


# ---------------------------------------------------------------------------
# PODSUMOWANIE „DZIŚ” (dla toasta)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=180, show_spinner=False)
def podsumowanie_dzis() -> dict:
    """Zwraca {'interp': {podatek: n}, 'stresz_razem': n} od północy (PL)."""
    prog = _polnoc_pl_iso()
    prog_data = prog[:10]
    interp = _zapytaj(
        """SELECT podatek, COUNT(*) AS n FROM dokumenty
           WHERE pobrano_at >= %s GROUP BY podatek""",
        (prog,),
    )
    stresz = _zapytaj(
        "SELECT COUNT(*) AS n FROM streszczenia_auto WHERE LEFT(wygenerowano,10) >= %s",
        (prog_data,),
    )
    return {
        "interp": {r["podatek"]: int(r["n"]) for r in interp},
        "stresz_razem": int(stresz[0]["n"]) if stresz else 0,
    }


def tekst_toasta() -> str | None:
    try:
        p = podsumowanie_dzis()
    except Exception:
        return None
    czesci = []
    if p["interp"]:
        wg = ", ".join(f"{k} +{v}" for k, v in sorted(p["interp"].items()))
        czesci.append(f"pobrano: {wg}")
    if p["stresz_razem"]:
        czesci.append(f"streszczono +{p['stresz_razem']}")
    if not czesci:
        return "Dziś: brak nowych interpretacji (jeszcze)."
    return "Dziś — " + "; ".join(czesci) + "."


def toast_dzis() -> None:
    """Wywoływane z app.py — pokazuje toast RAZ na sesję."""
    if st.session_state.get("_toast_pokazany"):
        return
    tekst = tekst_toasta()
    if tekst:
        st.toast(tekst, icon="🔔")
    st.session_state["_toast_pokazany"] = True


# ---------------------------------------------------------------------------
# WIDOK MODUŁU
# ---------------------------------------------------------------------------
def _fmt_liczby(d: dict) -> str:
    if not d:
        return "—"
    return ", ".join(f"{k} +{v}" for k, v in sorted(d.items()))


def pokaz_aktywnosc() -> None:
    st.header("📡 Aktywność systemu")

    # ── DZIŚ (skrót) ────────────────────────────────────────────────────────
    try:
        p = podsumowanie_dzis()
        razem_i = sum(p["interp"].values())
        k1, k2 = st.columns(2)
        k1.metric("Interpretacje dziś (dograne do bazy)", razem_i)
        k2.metric("Streszczenia dziś", p["stresz_razem"])
        if p["interp"]:
            st.caption("Dziś per podatek: " + _fmt_liczby(p["interp"]))
    except Exception as e:
        st.caption(f"Nie udało się policzyć aktywności dziś: {e}")

    st.divider()

    # ── HARMONOGRAMY ────────────────────────────────────────────────────────
    st.subheader("Harmonogram automatów")
    harmo = _harmonogramy()
    if not harmo:
        st.caption("Nie znaleziono plików workflow z harmonogramem "
                   "(.github/workflows/*.yml).")
    else:
        wiersze = []
        for h in harmo:
            if h["godziny_utc"]:
                godziny = _godziny_pl(h["godziny_utc"])
            elif h["rodzice"]:
                godziny = "po zakończeniu: " + ", ".join(h["rodzice"])
            else:
                godziny = "—"
            nastepne = _na_czas_pl(h["nastepne_utc"]) if h["nastepne_utc"] else "—"
            za = _za_ile(h["nastepne_utc"]) if h["nastepne_utc"] else ""
            if za:
                nastepne = f"{nastepne} ({za})"
            wiersze.append((h["nazwa"], godziny, nastepne))

        nag = ("padding:8px 10px;border-bottom:2px solid #386520;color:#386520;"
               "text-align:left;font-size:0.85rem;font-weight:700;")
        kom = "padding:8px 10px;border-bottom:1px solid #ddd;vertical-align:top;"
        thtml = (
            f"<table style='width:100%;border-collapse:collapse;font-size:0.9rem;'>"
            f"<tr><th style='{nag}'>Czynność</th>"
            f"<th style='{nag}'>Godziny (czas polski)</th>"
            f"<th style='{nag}white-space:nowrap;'>Najbliższe uruchomienie</th></tr>"
        )
        for nazwa, godziny, nastepne in wiersze:
            thtml += (
                f"<tr><td style='{kom}'><b>{nazwa}</b></td>"
                f"<td style='{kom}'>{godziny}</td>"
                f"<td style='{kom}white-space:nowrap;'>{nastepne}</td></tr>"
            )
        thtml += "</table>"
        st.markdown(thtml, unsafe_allow_html=True)
        st.caption("Godziny w czasie polskim (przeliczone z UTC; zimą wypadną "
                   "o godzinę wcześniej). „Po zakończeniu…” — uruchamiane "
                   "łańcuchowo, zaraz po wskazanym automacie.")

    st.divider()

    # ── KRONIKA ─────────────────────────────────────────────────────────────
    st.subheader(f"Kronika ostatnich {DNI_KRONIKI} dni")
    try:
        dni = _scal_po_dniach(_kronika_interpretacje(), _kronika_streszczenia())
    except Exception as e:
        st.error(f"Nie udało się odczytać kroniki: {e}")
        return
    if not dni:
        st.info("Brak zdarzeń w ostatnich dniach.")
        return
    for d in dni:
        i_sum = sum(d["interp"].values())
        s_sum = sum(d["stresz"].values())
        with st.container(border=True):
            st.markdown(f"**{d['dzien']}**")
            st.caption(
                f"📥 Interpretacje: {i_sum} ({_fmt_liczby(d['interp'])})   |   "
                f"📝 Streszczenia: {s_sum} ({_fmt_liczby(d['stresz'])})"
            )


if __name__ == "__main__":
    st.set_page_config(page_title="Aktywność systemu", layout="wide")
    pokaz_aktywnosc()
