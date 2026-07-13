"""
paleta.py — Wspolna paleta kolorow "Skaner Doradca".
  Jasny tryb: Kancelaria (cieply papier, zielen logo, braz)
  Ciemny tryb: Skaner (chlodny, niemal czarny, sygnalowa zielen)

Jedno zrodlo prawdy dla:
  - wlasnego HTML wstrzykiwanego przez st.markdown (ktory NIE dziedziczy
    automatycznie motywu Streamlit — trzeba mu kolory podac recznie),
  - generowania PDF (reportlab), ktore ZAWSZE uzywa palety JASNEJ
    (Kancelaria) niezaleznie od trybu przegladania — PDF to dokument
    czytany/drukowany na papierze, "ciemny PDF" nie ma sensu.

WAZNE: wartosci w .streamlit/config.toml sa TYMI SAMYMI liczbami, ale
wpisanymi recznie osobno (TOML nie moze importowac Pythona). Przy zmianie
koloru tutaj — zmien go tez tam.
"""

import streamlit as st

NAZWA_MARKI = "Skaner Doradca"

JASNY = {
    "bg": "#F6F3EC", "surface": "#FFFFFF", "surface2": "#EFEAD9",
    "border": "#E1DCCE", "text": "#22281F", "text2": "#5B6355",
    "primary": "#386520", "onprimary": "#FFFFFF", "accent": "#8A6D3B",
    "success": "#386520", "warning": "#B5792A",
}

CIEMNY = {
    "bg": "#0B0F0A", "surface": "#12170F", "surface2": "#070A06",
    "border": "#253023", "text": "#E3E9DE", "text2": "#8FA084",
    "primary": "#46B85B", "onprimary": "#06210C", "accent": "#5C8AA0",
    "success": "#46B85B", "warning": "#D6A23E",
}


def aktywny_tryb() -> str:
    """
    'light' lub 'dark' — odczytane z aktualnie aktywnego motywu Streamlit
    (system / recznie wybrany przez uzytkownika w Ustawieniach).

    st.context.theme.type bywa niepewne przy pierwszym uruchomieniu skryptu
    w danej sesji (znany, udokumentowany przypadek brzegowy Streamlit) —
    w razie watpliwosci bezpiecznie zakladamy 'light'.
    """
    try:
        typ = st.context.theme.type
        return typ if typ in ("light", "dark") else "light"
    except Exception:
        return "light"


def paleta() -> dict:
    """Slownik kolorow aktywnego trybu — do wlasnego HTML na ekranie."""
    return CIEMNY if aktywny_tryb() == "dark" else JASNY


def paleta_pdf() -> dict:
    """Paleta dla PDF — zawsze Kancelaria (jasna), niezaleznie od trybu ekranu."""
    return JASNY
