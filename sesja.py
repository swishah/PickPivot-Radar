# -*- coding: utf-8 -*-
"""
sesja.py — trwale logowanie miedzy odswiezeniami strony (Skaner Doradca).

PROBLEM
-------
st.session_state zyje tylko w obrebie polaczenia WebSocket. Nacisniecie F5
tworzy nowa sesje, wiec `authenticated` wraca do False i trzeba logowac sie
od nowa. To nie jest usterka aplikacji, tylko sposob dzialania Streamlita —
i nie da sie tego obejsc bez ciasteczka, bo nie ma innego miejsca, w ktorym
przegladarka moglaby zapamietac stan miedzy sesjami.

ROZWIAZANIE
-----------
Po zalogowaniu zapisujemy w ciasteczku PODPISANY token:

    email|wygasa_iso|podpis      (calosc w base64url)

Podpis to HMAC-SHA256 z sekretu `SESJA_SEKRET` (st.secrets). Serwer niczego
nie przechowuje — poprawnosc tokenu wynika z samego podpisu. Zmiana choc
jednego znaku w tresci uniewaznia go, bo podpis przestaje sie zgadzac.

DWA TRYBY TRWALOSCI
-------------------
  • bez zaznaczonego boksu -> ciasteczko SESYJNE (bez daty wygasniecia).
    Odswiezenie strony NIE wylogowuje, ale zamkniecie przegladarki tak.
  • z zaznaczonym boksem   -> ciasteczko na DNI_DLUGIE dni.

CZEGO TEN MECHANIZM NIE ROBI
----------------------------
Token jest wazny do daty wygasniecia i NIE DA SIE go uniewaznic zdalnie —
nie ma po stronie serwera listy aktywnych sesji. Dezaktywacja konta zadziala
dopiero przy kolejnym logowaniu, bo `wczytaj_sesje` sprawdza status konta
w bazie przy kazdym odtworzeniu sesji. Zmiana hasla NIE uniewaznia
wczesniejszych tokenow — jesli ma, trzeba dolozyc do podpisu fragment
hasla_hash (patrz komentarz przy _material_konta).

BEZPIECZENSTWO
--------------
Token trafia do ciasteczka, a nie do adresu URL — celowo. W URL-u wystarczy,
zeby ktos skopiowal link z paska adresu, i oddaje dostep do konta.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac

import streamlit as st

NAZWA_CIASTECZKA = "skaner_sesja"
DNI_DLUGIE = 30

# Klucz w st.session_state pod ktorym trzymamy menedzera ciasteczek.
_KLUCZ_MENEDZERA = "_sesja_cookies"


# ---------------------------------------------------------------------------
# SEKRET
# ---------------------------------------------------------------------------
def _sekret() -> bytes:
    """Sekret do podpisywania tokenow.

    Kolejnosc zrodel jest istotna: wlasny SESJA_SEKRET, a gdy go nie ma —
    haslo konta DORADCA, zeby mechanizm dzialal od razu, bez dokladania
    wpisu w secrets. Wariant zapasowy ma skutek uboczny: zmiana hasla
    DORADCA uniewaznia wszystkie zapamietane sesje.
    """
    for klucz in ("SESJA_SEKRET", "DORADCA_HASLO", "ADMIN_HASLO"):
        wartosc = st.secrets.get(klucz)
        if wartosc:
            return str(wartosc).encode("utf-8")
    # Ostatnia deska ratunku — stala wartosc. Sesje beda dzialac, ale token
    # jest wtedy podrabialny przez kazdego, kto zna kod. Dlatego glosno
    # ostrzegamy zamiast po cichu udawac, ze wszystko gra.
    st.warning(
        "Brak SESJA_SEKRET w konfiguracji — zapamietywanie logowania dziala "
        "w trybie niezabezpieczonym. Dodaj SESJA_SEKRET do secrets.",
        icon="⚠️")
    return b"skaner-doradca-bez-sekretu"


def _podpisz(tresc: str) -> str:
    return hmac.new(_sekret(), tresc.encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# TOKEN
# ---------------------------------------------------------------------------
def _zbuduj_token(email: str, wygasa: dt.datetime) -> str:
    tresc = f"{email}|{wygasa.isoformat(timespec='seconds')}"
    surowy = f"{tresc}|{_podpisz(tresc)}"
    return base64.urlsafe_b64encode(surowy.encode("utf-8")).decode("ascii")


def _rozbierz_token(token: str) -> str | None:
    """Zwraca e-mail, gdy token jest poprawny i niewygasly — inaczej None."""
    try:
        surowy = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        email, wygasa_iso, podpis = surowy.rsplit("|", 2)
    except Exception:
        return None

    # compare_digest zamiast == : porownanie stalo-czasowe, zeby z czasu
    # odpowiedzi nie dalo sie zgadywac podpisu znak po znaku.
    if not hmac.compare_digest(podpis, _podpisz(f"{email}|{wygasa_iso}")):
        return None

    try:
        if dt.datetime.fromisoformat(wygasa_iso) < dt.datetime.now():
            return None
    except Exception:
        return None
    return email


# ---------------------------------------------------------------------------
# CIASTECZKA
# ---------------------------------------------------------------------------
def _menedzer():
    """Menedzer ciasteczek albo None, gdy biblioteki nie ma.

    Brak biblioteki NIE moze wywalac aplikacji — wtedy po prostu nie ma
    zapamietywania sesji, a logowanie dziala jak wczesniej.
    """
    if _KLUCZ_MENEDZERA in st.session_state:
        return st.session_state[_KLUCZ_MENEDZERA]
    try:
        import extra_streamlit_components as stx
    except Exception:
        st.session_state[_KLUCZ_MENEDZERA] = None
        return None
    # Staly klucz: komponent musi byc tym samym obiektem miedzy przeliczeniami,
    # inaczej Streamlit montuje go od nowa i ciasteczka gubia sie w locie.
    menedzer = stx.CookieManager(key="skaner_cookie_manager")
    st.session_state[_KLUCZ_MENEDZERA] = menedzer
    return menedzer


def zapisz_sesje(email: str, zapamietaj: bool) -> None:
    """Zapisuje ciasteczko po udanym logowaniu."""
    menedzer = _menedzer()
    if menedzer is None:
        return
    if zapamietaj:
        wygasa = dt.datetime.now() + dt.timedelta(days=DNI_DLUGIE)
        expires_at = wygasa
    else:
        # Ciasteczko sesyjne: bez daty wygasniecia przegladarka kasuje je przy
        # zamknieciu. Token i tak dostaje wewnetrzny termin, zeby nie byl
        # wazny bez konca, gdyby przegladarka go zachowala.
        wygasa = dt.datetime.now() + dt.timedelta(hours=12)
        expires_at = None
    try:
        menedzer.set(NAZWA_CIASTECZKA, _zbuduj_token(email, wygasa),
                     expires_at=expires_at, key="sesja_set")
    except Exception:
        pass


def wyczysc_sesje() -> None:
    """Kasuje ciasteczko przy wylogowaniu."""
    menedzer = _menedzer()
    if menedzer is None:
        return
    try:
        menedzer.delete(NAZWA_CIASTECZKA, key="sesja_del")
    except Exception:
        pass


def odczytaj_email() -> str | None:
    """E-mail z waznego ciasteczka albo None."""
    menedzer = _menedzer()
    if menedzer is None:
        return None
    try:
        token = menedzer.get(NAZWA_CIASTECZKA)
    except Exception:
        return None
    if not token:
        return None
    return _rozbierz_token(str(token))
