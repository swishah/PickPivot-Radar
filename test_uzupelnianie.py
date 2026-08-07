# -*- coding: utf-8 -*-
"""Test offline: bez bazy, bez Streamlit, bez sieci. Atrapy podstawiane
przed importem modulu, zeby sprawdzic sama logike skladania polecenia,
walidacji odpowiedzi i zapisu."""
import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- atrapa streamlit (modul importuje go na gorze) ---
st = types.ModuleType("streamlit")
for nazwa in ("header","caption","button","dataframe","divider","info",
              "markdown","code","text_area","warning","error","success",
              "selectbox","number_input","columns","rerun","cache_data"):
    setattr(st, nazwa, lambda *a, **k: None)
st.session_state = {}
sys.modules["streamlit"] = st

pal = types.ModuleType("paleta"); pal.JASNY = {}; sys.modules["paleta"] = pal

# --- atrapa bazy ---
class DB:
    def __init__(self): self.zapisy = []
    def wykonaj(self, sql, params=None, fetch=False):
        if fetch: return []
        self.zapisy.append((" ".join(sql.split()), params))
arch = types.ModuleType("archiwum_supabase")
DBX = DB()
arch._get_db = lambda: DBX
sys.modules["archiwum_supabase"] = arch

import streszczacz_openrouter as so
for k in ("SUPABASE_DB_URL","SUPABASE_HOST","SUPABASE_USER","SUPABASE_PASSWORD"):
    os.environ.pop(k, None)
so.zaladuj_taksonomie(wymus=True)          # stale z kodu

import uzupelnij_klasyfikacje as uk

bledy = 0
def spr(o, w, d=""):
    global bledy
    if not w: bledy += 1
    print(f"{'OK  ' if w else 'BLAD'}  {o}{'  -> '+str(d)[:90] if d else ''}")

PARTIA = [
    {"id": 101, "sygnatura": "0111-KDIB1-1.4010.1.2026.1.AB", "data_wyd": "2026-01-05",
     "podatek": "CIT", "temat": "Estoński CIT a podpis sprawozdania",
     "streszczenie": "Spółka wybrała ryczałt od dochodów spółek...",
     "przedmiot": "", "branze": ""},
    {"id": 102, "sygnatura": "0111-KDIB2-2.4010.2.2026.2.CD", "data_wyd": "2026-01-04",
     "podatek": "CIT", "temat": "Amortyzacja kotłowni",
     "streszczenie": "Przedsiębiorstwo ciepłownicze buduje kotłownię...",
     "przedmiot": "amortyzacja i środki trwałe", "branze": ""},
]

print("=" * 70); print("1. POLECENIE"); print("=" * 70)
p = uk._polecenie("CIT", PARTIA)
spr("zawiera PELNA taksonomie przedmiotow CIT",
    all(x in p for x in so.przedmioty("CIT")), f"{len(so.przedmioty('CIT'))} pozycji")
spr("zawiera PELNA liste branz", all(b in p for b in so.branze()))
spr("nie ma taksonomii innego podatku", "stawki VAT" not in p)
spr("sa oba dokumenty", "101" in p and "102" in p)
spr("mowi, czego brakuje", "brakuje: przedmiot, branże" in p)
spr("podaje juz ustalony przedmiot", "przedmiot już ustalony: amortyzacja" in p)
spr("zada samego JSON", "WYŁĄCZNIE tablicą JSON" in p)

print(); print("=" * 70); print("2. ODPOWIEDZ POPRAWNA"); print("=" * 70)
dobra = '''```json
[{"id":101,"przedmiot":"estoński CIT (ryczałt od dochodów spółek)","branze":["produkcyjna"]},
 {"id":102,"przedmiot":"amortyzacja i środki trwałe","branze":["ciepłownicza","energetyczna"]}]
```'''
w = uk._sprawdz(dobra, PARTIA, "CIT")
spr("znaczniki ``` nie przeszkadzaja", len(w["zmiany"]) == 2, w["zastrzezenia"])
z1 = [z for z in w["zmiany"] if z["id"] == 101][0]
spr("przedmiot 101", z1["przedmiot"] == "estoński CIT (ryczałt od dochodów spółek)")
spr("branza 101", z1["branze"] == "produkcyjna")
z2 = [z for z in w["zmiany"] if z["id"] == 102][0]
spr("102: przedmiot NIE nadpisany", z2["przedmiot"] == "", z2["przedmiot"])
spr("102: dwie branze polaczone", z2["branze"] == "ciepłownicza, energetyczna", z2["branze"])

print(); print("=" * 70); print("3. WARTOSCI SPOZA TAKSONOMII"); print("=" * 70)
zla = '[{"id":101,"przedmiot":"estonski cit wymyslony","branze":["branża grzewcza"]}]'
w = uk._sprawdz(zla, PARTIA, "CIT")
spr("nic nie trafia do zapisu", w["zmiany"] == [], w["zmiany"])
spr("dwa zastrzezenia o taksonomii",
    sum("spoza taksonomii" in x for x in w["zastrzezenia"]) == 2, w["zastrzezenia"])

print(); print("=" * 70); print("4. MODEL DORZUCA OBCE ID"); print("=" * 70)
obce = '[{"id":999,"przedmiot":"różnice kursowe","branze":["handlowa"]}]'
w = uk._sprawdz(obce, PARTIA, "CIT")
spr("obcy dokument odrzucony", w["zmiany"] == [])
spr("powiedziane wprost", any("nie należy do tej partii" in x for x in w["zastrzezenia"]))

print(); print("=" * 70); print("5. MODEL POMIJA DOKUMENTY"); print("=" * 70)
czesc = '[{"id":101,"przedmiot":"różnice kursowe","branze":["handlowa"]}]'
w = uk._sprawdz(czesc, PARTIA, "CIT")
spr("jedna zmiana", len(w["zmiany"]) == 1)
spr("informacja o pominietych", any("pominął 1" in x for x in w["zastrzezenia"]),
    w["zastrzezenia"])

print(); print("=" * 70); print("6. SMIECIE ZAMIAST JSON"); print("=" * 70)
for smiec in ["", "Nie jestem pewien.", "{{{", "[niepoprawny json"]:
    w = uk._sprawdz(smiec, PARTIA, "CIT")
    spr(f"'{smiec[:20]}' -> brak zmian", w["zmiany"] == [])

print(); print("=" * 70); print("7. ZAPIS TYLKO DO PUSTYCH KOLUMN"); print("=" * 70)
DBX.zapisy.clear()
uk._zapisz([{"id": 101, "sygnatura": "X", "przedmiot": "różnice kursowe",
             "branze": "handlowa"},
            {"id": 102, "sygnatura": "Y", "przedmiot": "", "branze": "ciepłownicza"}])
spr("dwa UPDATE", len(DBX.zapisy) == 2, len(DBX.zapisy))
sql1, par1 = DBX.zapisy[0]
spr("101: ustawia oba pola", "przedmiot = %s, branze = %s" in sql1, sql1[:60])
spr("101: warunek pustki dla obu",
    "COALESCE(przedmiot, '') = '' OR COALESCE(branze, '') = ''" in sql1)
sql2, par2 = DBX.zapisy[1]
spr("102: rusza TYLKO branze", "SET branze = %s" in sql2, sql2[:60])
spr("102: nie ma warunku o przedmiocie", "COALESCE(przedmiot" not in sql2)
spr("102: parametry w kolejnosci", par2 == ("ciepłownicza", 102), par2)

print(); print("=" * 70); print("8. ZAPYTANIA NIE POBIERAJA PELNEJ TRESCI"); print("=" * 70)
import inspect
zrodlo = inspect.getsource(uk)
spr("brak SELECT *", "SELECT *" not in zrodlo)
spr("streszczenie przycinane", "left(COALESCE(s.streszczenie" in zrodlo)
spr("statystyki przez COUNT", "count(*) FILTER" in zrodlo)
spr("kolumna tekst nigdzie nie pobierana", "d.tekst" not in zrodlo and "s.tekst" not in zrodlo)

print()
print("WSZYSTKO PRZESZLO" if bledy == 0 else f"{bledy} BLEDOW")
sys.exit(1 if bledy else 0)
