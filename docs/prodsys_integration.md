# prodsys als Simulationskern

Die Baustellenplanung (NSGA-II über die Startzeitpunkte der Sanierungen) bewertet jeden Plan mit
einer Simulation des ÖV-Netzes. Auf diesem Branch ist [prodsys](https://github.com/sdm4fzi/prodsys)
(Simulation von Produktionssystemen) der Simulationskern. Grundlage des Betriebs ist ein Fahrplan
(GTFS). Baustellen reduzieren die Streckenkapazität, und eine Disposition erzeugt daraus den
Baufahrplan, in dem Fahrten verspätet, umgeleitet oder gestrichen werden. Die eigene
SimPy-Simulation (`src/simulation.py`) bleibt als schnelle Referenz ohne Fahrplan erhalten
(`--backend simpy`).

## Abbildung Bahnnetz → prodsys

| Bahnwelt | prodsys | Bemerkung |
|---|---|---|
| Bahnhof | Production Resource | Prozesse `transfer_<Bhf>` (Umsteigen) und `alight_<Bhf>` (Aussteigen); alle Einsteigenden warten in der Warteschlange des Bahnhofs |
| Ausgang des Bahnhofs | Fussweg-Ressource (LinkTransportProcess) | bringt Fahrgäste vom Bahnhof zur Sink |
| Abfahrt / Ziel einer Relation | Source / Sink | je Relation und Routenvariante ein Produkttyp; die Source legt neue Fahrgäste direkt in die Warteschlange ihres Bahnhofs |
| Streckenabschnitt (Kante) | Link zwischen zwei Gleisknoten (Nodes) | Kapazität `track_capacity` = Fahrzeuge gleichzeitig |
| Fahrzeug (Zug, Schiff, Bus, Kabine) | Transport Resource | ein Fahrzeug je Umlauf des Fahrplans; Plätze über `LotDependency` |
| Fahrgäste | Products | ein Produkt = Gruppe von `G` Personen (Default 10) |
| Umsteigen | Prozess am Umsteigebahnhof | Route mit Umstieg = eigene Prozessfolge |
| Anlagen (Weichen, Brücken, …) | **neu:** `src/assets.py` | gibt es in prodsys nicht, siehe unten |

Zeit in prodsys: Minuten. Der GA und die Anlagen-Schicht rechnen in Stunden.

## Fahrplan (GTFS)

`src/timetable/gtfs.py` liest einen GTFS-Feed: `stops`, `routes`, `trips`, `stop_times` und
`calendar` und/oder `calendar_dates`. Für den Horizont werden die Fahrten jedes Betriebstags
instanziert (Tag *d* beginnt bei Minute *d* × 1440, Zeiten über 24:00 sind erlaubt).

Konventionen für den Referenzjahr-Fahrplan:

- `stop_id` = `node_id` aus `nodes_template.csv`.
- Jedes Paar aufeinanderfolgender Halte einer Fahrt muss eine Kante in `edges_template.csv` sein.
- Plätze je Fahrt: Zusatzspalte `vehicle_capacity` in `trips.txt` oder `routes.txt`, sonst ein
  Standardwert je Verkehrsträger (`route_type` → Zug, Bus, Schiff, Luftseilbahn …).
- `block_id` (optional) ist ein Tagesdienst eines Fahrzeugs; die Dienste werden auf physische
  Fahrzeuge verteilt. Ohne `block_id` plant `timetable/vehicles.py` die Umläufe selbst
  (Wendezeit 2 min).

Solange es den Referenzjahr-Fahrplan noch nicht gibt, ist `data/gtfs` ein synthetischer Feed aus den
Netzdaten (`python src/generate_timetable.py`). Er hat einen Takt je Verkehrsträger (Zug 30, Schiff
60, Bus 30, Luftseilbahn 15 min). Die Plätze sind so gewählt, dass `capacity_at_day` pro 12 h
erreicht wird, der Nachttakt folgt aus `capacity_at_night`, und die Gegenrichtung fährt um einen
halben Takt versetzt.

## Streckenkapazität und Anlagen-Schicht (`src/assets.py`)

- `track_capacity` (neue Spalte in `edges_template.csv`) gibt an, wie viele Fahrzeuge gleichzeitig
  auf dem Abschnitt sein dürfen. Die Werte sind Annahmen: Zug 2, Rigi-Bahn 2, Luftseilbahn 2 (zwei
  Kabinen), Schiff 3, Bus 4.
- Eine Baustelle sperrt Gleise: `floor(track_capacity × 0.5)`. Ein einspuriger Abschnitt ist
  damit ganz gesperrt.
- Zustand je Anlage als Funktion der Zeit: Alterung pro Tag, während der Arbeiten höchstens 0.2,
  danach neuwertig und erneute Alterung. Sanierungen, die im Horizont nicht fertig werden, gelten
  als verschoben (keine Kosten).
- Fahrzeit im Fahrplanbetrieb: Der Fahrplan enthält die Fahrzeiten für den Zustand bei
  Horizontbeginn. Langsamer werden Fahrten nur durch weitere Alterung und durch Baustellen
  (Faktor 1.5), nie schneller als geplant (`edge_slowdown_vs_timetable`).

## Baufahrplan (`src/timetable/disposition.py`)

Fahrten werden in der Reihenfolge ihrer Abfahrt eingeplant:

1. Das Fahrzeug muss am Startbahnhof stehen und bereit sein (Umlauf). Eine Ausfahrt aus dem Depot
   darf überall beginnen.
2. Jeder Abschnitt fährt zum frühesten Zeitpunkt ab Fahrplanzeit, an dem für die ganze Fahrzeit
   Platz auf dem Abschnitt ist. Haltezeiten bleiben erhalten.
3. Gibt es innerhalb von 30 min keinen Platz oder ist der Abschnitt gesperrt, wird eine Umleitung
   über Abschnitte desselben Verkehrsträgers gesucht. Gibt es keine, fällt die Fahrt aus (erster
   Abschnitt) oder wendet vorher (späterer Abschnitt). Ein Fahrzeug, das nicht ans Ziel kommt,
   verpasst seine folgenden Fahrten, bis es wieder an einem Startbahnhof steht.

Jede Fahrt erhält einen Status: pünktlich (< 3 min), verspätet, umgeleitet, gewendet oder
ausgefallen, jeweils mit Grund.

## Betrieb in prodsys (`src/rail_prodsys/runtime.py`)

prodsys selbst wird nicht verändert; das Backend hängt sich nach `Runner.initialize_simulation()`
ein:

- **Nachfrage:** eigener Ankunftsprozess je Relation mit tageszeitabhängigen Raten
  (`passenger_flows_day.csv`, jeden Tag wiederholt). Die Fahrgastgruppen werden über die
  prodsys-Quellen erzeugt, deren eigene Ankunftsschleife abgeschaltet ist.
- **Fahrzeuge nach Baufahrplan** (`TimetableOperator`):
  - Die Linienfahrzeuge sind nie unter den freien Ressourcen des Routers. prodsys disponiert sie
    also nicht auf Abruf.
  - Zur Abfahrt belegt das Fahrzeug den Abschnitt und übernimmt die wartenden Fahrgäste genau
    dieses Abschnitts in Wartereihenfolge bis zur Platzzahl, so wie der Router von prodsys
    zuteilen würde. Dann fährt es in der Fahrzeit des Baufahrplans.
  - Wer keinen Platz mehr findet, wartet auf die nächste Fahrt. Leerfahrten versetzen nur das
    Fahrzeug.
- **Routenwahl / Umrouten** (`TimetableRouteChooser`): bei der Ankunft am Bahnhof die Variante mit
  der frühesten Ankunft im gefahrenen Fahrplan. Abfahrten, die die bereits Wartenden füllen,
  werden übersprungen.
- **Sicherheitsnetz Streckenkapazität:** zur Laufzeit belegt jedes Fahrzeug einen Platz auf dem
  Abschnitt; Baustellen halten Plätze. Weil der Baufahrplan die Kapazität schon einhält, wartet
  dabei niemand (`section_wait_minutes` = 0, Test vorhanden).

## Bedienung

```bash
# GA mit prodsys-Backend (Default), Horizont in Stunden
python src/main.py --horizon 336 --population 24 --generations 10 --cx 0.6 --mut 0.3
# Referenzjahr-Fahrplan statt synthetischem Feed
python src/main.py --gtfs pfad/zum/gtfs --timetable-start 20260105 --horizon 336
# Synthetischen Fahrplan aus den Netzdaten neu erzeugen
python src/generate_timetable.py
# Referenz mit der eigenen SimPy-Simulation (ohne Fahrplan)
python src/main.py --backend simpy --horizon 336
# Frontend (Backend-Auswahl in der Seitenleiste, Details zum gezeigten Plan)
streamlit run src/frontend.py
# Tests
pip install -r requirements-dev.txt
python -m pytest tests
```

- `--group-size` (Default 10): Personen pro simuliertem Produkt. Kleiner = feiner, aber langsamer.
- `output/summary.json` enthält beim prodsys-Backend `details` für den gewählten Plan:
  - je Linie Fahrzeuge, Plätze, Besetzungsgrad, beförderte und stehengelassene Fahrgäste,
    Baustellenstunden sowie die Fahrten des Baufahrplans je Status und die mittlere Verspätung;
  - je Relation die mittlere Reisezeit und die Anteile der Routenvarianten;
  - Umsteiger je Bahnhof.

Laufzeit (Gruppen à 10): eine Bewertung über 7 Tage dauert etwa 12–15 s, davon gut 2 s
Initialisierung von prodsys. Die Disposition selbst braucht für 7 Tage etwa 0.05 s.

## Bewusste Einschränkungen

- Die Disposition plant regelbasiert (frühester freier Platz, Umleitung, sonst Ausfall). Welche
  Fahrten bei Engpässen Vorrang haben, ist nicht Teil der Optimierung. Der GA bewertet die Folgen
  über Reisezeit und beförderte Fahrgäste.
- Fahrgäste, die durchfahren, steigen an Zwischenhalten kurz aus und wieder ein. Im aktuellen Netz
  gibt es nur Fahrten über einen Abschnitt; bei Mehrhalt-Fahrten aus echten Daten ist das eine
  Vereinfachung.
- Umleitungen nutzen nur Abschnitte desselben Verkehrsträgers. Im aktuellen Netz gibt es keine
  solche Alternative, deshalb fallen Fahrten aus, und die Fahrgäste weichen auf andere Linien aus.
  Ersatzverkehr (Bus statt Zug) ist noch nicht modelliert.
- Routenvarianten der Fahrgäste werden nach Grundzeiten vorausgewählt (höchstens 3, höchstens 3×
  so lang wie die schnellste; `max_detour_factor` erhöht die Auswahl).
- Die Wartungszustände von prodsys (`MaintenanceState`) und seine Optimierer werden nicht
  verwendet: Erstere halten ein Fahrzeug mitten auf der Fahrt an, Letztere optimieren nur die
  Systemkonfiguration und erlauben keine eigenen Entscheidungsvariablen oder Ziele.
- prodsys ist auf `1.2.3` gepinnt, weil das Backend Laufzeit-Interna nutzt (Router-Warteschlange,
  Quellen, Transportzustände).
- Unter Windows muss der Pfad zur venv kurz genug sein (MAX_PATH 260), sonst schlägt der Import
  von prodsys mit `ModuleNotFoundError` fehl.

## Stand

| Etappe | Inhalt | Status |
|---|---|---|
| 1 | Abhängigkeiten, Anlagen-Schicht mit Tests, diese Doku | erledigt |
| 2 | Netz-Builder und Basislauf | erledigt |
| 3 | Kopplung Anlagen ↔ prodsys | erledigt |
| 4 | Umsteigen und Umrouten (Routenvarianten) | erledigt |
| 5 | GA-Anbindung (Backend-Schalter, prodsys-Kennzahlen) | erledigt |
| 6 | Frontend | erledigt |
| 7 | Streckenkapazität, GTFS-Fahrplan, Umläufe | erledigt |
| 8 | Disposition: Baufahrplan | erledigt |
| 9 | prodsys fährt nach Baufahrplan (ersetzt den Abruf-Betrieb) | erledigt |
| 10 | GA, Frontend, Doku, neue Ergebnisse | erledigt |
| offen | Materialfluss (z. B. Bauzüge, die Streckenkapazität belegen), Ersatzverkehr | offen |
