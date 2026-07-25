import os, sys, time, subprocess
sys.path.insert(0, '/home/claude/faza2b')

# Test sam przygotowuje sobie dane — inaczej zalezy od tego, co akurat
# zostawil w bazie poprzedni przebieg. Poprzednia wersja tego testu tego nie
# robila i padla 12 razy po zasileniu bazy prawdziwa taksonomia.
subprocess.run(["su","postgres","-c", """psql -d test_faza1 -q -c "
DELETE FROM taksonomia_branze; DELETE FROM taksonomia_przedmiotow;
INSERT INTO taksonomia_branze (branza,kolejnosc,aktywna) VALUES
 ('Z-BAZY-ciepłownicza',0,true),('Z-BAZY-energetyczna',1,true),
 ('Z-BAZY-wycofana',2,false);
INSERT INTO taksonomia_przedmiotow (podatek,przedmiot,kolejnosc,aktywny) VALUES
 ('CIT','Z-BAZY-estoński',0,true),('CIT','Z-BAZY-WHT',1,true),
 ('CIT','Z-BAZY-wycofany',2,false),('VAT','Z-BAZY-stawki',0,true);" """],
 capture_output=True)
bledy = 0
def spr(o, w, d=""):
    global bledy
    if not w: bledy += 1
    print(f"{'OK  ' if w else 'BLAD'}  {o}{'  -> '+str(d) if d else ''}")

print("=" * 68); print("1. BEZ KONFIGURACJI BAZY -> stale z kodu"); print("=" * 68)
for k in ("SUPABASE_DB_URL","SUPABASE_HOST","SUPABASE_USER","SUPABASE_PASSWORD"):
    os.environ.pop(k, None)
import streszczacz_openrouter as s
spr("zrodlo = stale", s.zrodlo_taksonomii() == "stałe w kodzie", s.zrodlo_taksonomii())
spr("18 branz z kodu", len(s.branze()) == 18, len(s.branze()))
spr("26 przedmiotow CIT", len(s.przedmioty("CIT")) == 26, len(s.przedmioty("CIT")))
spr("prompt zawiera branze z kodu", "ciepłownicza" in s._system_dla("CIT"))
spr("walidacja dziala na stalych", s._waliduj_branze(["ciepłownicza"]) == ["ciepłownicza"])

print(); print("=" * 68); print("2. Z BAZA -> wartosci z bazy"); print("=" * 68)
os.environ["SUPABASE_DB_URL"] = "postgresql://postgres:test@localhost:5432/test_faza1"
spr("zrodlo = baza", s.zaladuj_taksonomie(wymus=True) == "baza")
spr("2 branze aktywne (wycofana pominieta)", len(s.branze()) == 2, s.branze())
spr("wycofana NIE na liscie", "Z-BAZY-wycofana" not in s.branze())
spr("kolejnosc zachowana", s.branze()[0] == "Z-BAZY-ciepłownicza", s.branze()[0])
spr("przedmioty CIT z bazy", s.przedmioty("CIT") == ["Z-BAZY-estoński","Z-BAZY-WHT"], s.przedmioty("CIT"))
spr("przedmioty VAT z bazy", s.przedmioty("VAT") == ["Z-BAZY-stawki"])
spr("nieznany podatek -> pusto", s.przedmioty("PIT") == [], s.przedmioty("PIT"))

print(); print("=" * 68); print("3. PROMPT I WALIDACJA UZYWAJA BAZY"); print("=" * 68)
sys_cit = s._system_dla("CIT")
spr("prompt ma branze z bazy", "Z-BAZY-ciepłownicza" in sys_cit)
spr("prompt NIE ma starych stalych", "wodno-kanalizacyjna" not in sys_cit)
spr("prompt ma przedmioty z bazy", "Z-BAZY-estoński" in sys_cit)
spr("walidacja branz wg bazy", s._waliduj_branze(["Z-BAZY-energetyczna"]) == ["Z-BAZY-energetyczna"])
spr("stara wartosc odrzucona", s._waliduj_branze(["ciepłownicza"]) == [])
spr("wycofana odrzucona", s._waliduj_branze(["Z-BAZY-wycofana"]) == [])
spr("walidacja przedmiotu wg bazy", s._waliduj_przedmioty(["Z-BAZY-WHT"], "CIT") == ["Z-BAZY-WHT"])
spr("maks 2 branze", len(s._waliduj_branze(["Z-BAZY-ciepłownicza","Z-BAZY-energetyczna","Z-BAZY-ciepłownicza"])) <= 2)

print(); print("=" * 68); print("4. AWARIA BAZY -> cichy powrot do stalych"); print("=" * 68)
os.environ["SUPABASE_DB_URL"] = "postgresql://zly:zly@127.0.0.1:9999/nieistnieje"
spr("zrodlo wraca na stale", s.zaladuj_taksonomie(wymus=True) == "stałe w kodzie")
spr("stale znow dzialaja", len(s.branze()) == 18)
spr("brak wyjatku, streszczanie moze isc dalej", "ciepłownicza" in s._system_dla("CIT"))

print(); print("=" * 68); print("5. PUSTE TABELE -> traktowane jak brak danych"); print("=" * 68)
import subprocess
subprocess.run(["su","postgres","-c",
  "psql -d test_faza1 -q -c 'DELETE FROM taksonomia_branze; DELETE FROM taksonomia_przedmiotow;'"],
  capture_output=True)
os.environ["SUPABASE_DB_URL"] = "postgresql://postgres:test@localhost:5432/test_faza1"
spr("pusta tabela -> stale", s.zaladuj_taksonomie(wymus=True) == "stałe w kodzie")
spr("model nie dostanie pustej listy", len(s.branze()) == 18)

print(); print("=" * 68); print("6. BUFOR"); print("=" * 68)
t0 = time.time(); [s.branze() for _ in range(200)]; dt = time.time() - t0
spr("200 odczytow z bufora bez ruchu do bazy", dt < 0.05, f"{dt*1000:.1f} ms")

print(); print("=" * 68); print("7. streszczenie_wadliwe BEZ ZMIAN"); print("=" * 68)
spr("puste", s.streszczenie_wadliwe("") is True)
spr("surowy JSON", s.streszczenie_wadliwe('{"temat": "x"}') is True)
spr("za krotkie", s.streszczenie_wadliwe("krótkie") is True)
spr("etykieta bezpieczenstwa", s.streszczenie_wadliwe("User Safety: safe") is True)
dobre = "Wnioskodawca prowadzi działalność w zakresie dostawy ciepła. " * 4
spr("poprawne przechodzi", s.streszczenie_wadliwe(dobre) is False)

print(); print("WSZYSTKO PRZESZLO" if bledy==0 else f"{bledy} BLEDOW")
sys.exit(1 if bledy else 0)
