# prodsys als Simulationskern

Die Baustellenplanung (NSGA-II über die Startzeitpunkte der Sanierungen) bewertet jeden Plan mit
einer Simulation des ÖV-Netzes. Bisher war das eine eigene SimPy-Simulation
(`src/simulation.py`). Auf diesem Branch wird [prodsys](https://github.com/sdm4fzi/prodsys)
(Simulation von Produktionssystemen) zum Simulationskern. Die SimPy-Simulation bleibt als schnelle
Referenz erhalten.

## Abbildung Bahnnetz → prodsys

| Bahnwelt | prodsys | Bemerkung |
|---|---|---|
| Bahnhof | Production Resource | Prozesse `transfer_<Bhf>` (Umsteigen) und `alight_<Bhf>` (Aussteigen) |
| Ausgang des Bahnhofs | Fussweg-Ressource (LinkTransportProcess) | bringt Fahrgäste vom Bahnhof zur Sink |
| Abfahrt / Ziel einer Relation | Source / Sink | je Relation und Routenvariante ein Produkttyp |
| Strecke (Kante) | Linie = LinkTransportProcess mit Gleisknoten (Nodes) und Links | Links verbinden Gleisknoten, Bahnhof und Quellen |
| Zug, Schiff, Bus, Seilbahn | Transport Resources der Linie | Plätze über `LotDependency` (Sammelfahrt) |
| Fahrgäste | Products | ein Produkt = Gruppe von `G` Personen (Default 10) |
| Umsteigen | Prozess am Umsteigebahnhof | Route mit Umstieg = eigene Prozessfolge |
| Anlagen (Weichen, Brücken, …) | **neu:** `src/assets.py` | gibt es in prodsys nicht, siehe unten |

Zeit in prodsys: Minuten. Der GA und die Anlagen-Schicht rechnen in Stunden; umgerechnet wird an
der Grenze zum prodsys-Backend.

## Anlagen-Schicht (`src/assets.py`)

prodsys kennt keine Infrastruktur-Anlagen. Die Anlagen-Schicht ist deshalb eigenständig und wird von
beiden Backends genutzt (`InfrastructureState`):

- Zustand je Anlage als Funktion der Zeit: Alterung pro Tag, während der Arbeiten höchstens 0.2,
  danach neuwertig (1.0) und erneute Alterung.
- Sanierungen, die im Horizont nicht fertig werden, gelten als verschoben (keine Kosten).
- Je Strecke: schlechtester Anlagenzustand, Baustelle aktiv ja/nein, Langsamfahrfaktor
  `(1 + (1 − Zustand)) × 1.5 bei Baustelle`, Kapazitätsfaktor `0.5 bei Baustelle`.
- Zielgrössen Durchschnittszustand und Kosten werden direkt berechnet (ohne Simulation).

## Kopplung zur Laufzeit

prodsys wird nicht verändert; das Backend hängt sich nach `Runner.initialize_simulation()` ein:

- **Nachfrage:** eigener Ankunftsprozess je Relation mit tageszeitabhängigen Raten
  (`passenger_flows_day.csv`, wiederholt jeden Tag). Er erzeugt die Produkte über die prodsys-Quellen,
  deren eigene Ankunftsschleife abgeschaltet ist.
- **Routenwahl / Umrouten:** bei der Ankunft wird die Routenvariante mit der kleinsten erwarteten
  Reisezeit gewählt (Fahrzeit mit Zustand und Baustellen, Wartezeit, Umsteigezeit).
- **Kapazität:** nachts (`capacity_at_night`) und bei Baustellen werden Fahrzeuge geordnet aus dem
  Dienst genommen: Sie bekommen keine neuen Fahrgäste zugeteilt, fahren laufende Fahrten aber zu
  Ende (über `router.free_resources`).
- **Langsamfahrt:** die Fahrzeit jedes Abschnitts wird mit dem Langsamfahrfaktor der Strecke
  multipliziert (Wrapper um das Zeitmodell jedes Fahrzeugs).
- **Disposition:** eigene Routing-Heuristik (`product_factory.routing_heuristic_override`): freies
  Fahrzeug zuerst, sonst zu einem Fahrzeug mit wartenden Fahrgästen derselben Relation zusteigen
  (Sammelfahrt), sonst kürzeste Warteschlange. prodsys' eigene Heuristiken verteilen Transporte
  zufällig, dann entstehen keine Sammelfahrten.

## Bewusste Einschränkungen

- prodsys disponiert Fahrzeuge auf Abruf (wie Taxis), nicht nach Fahrplan. Die Wartezeit auf ein
  Fahrzeug ist Teil der Reisezeit.
- prodsys hat keine Gleis-/Streckenkapazität. Die Streckenkapazität ergibt sich aus
  Fahrzeuganzahl × Plätzen (Durchsatz pro Stunde aus `capacity_at_day`).
- Die Wartungszustände von prodsys (`MaintenanceState`) werden nicht verwendet: Sie halten ein
  Fahrzeug sofort an, auch mitten auf der Fahrt mit Fahrgästen.
- Die Optimierer von prodsys werden nicht verwendet: Sie optimieren nur die Systemkonfiguration
  (Maschinen, Layout, Steuerung) und erlauben keine eigenen Entscheidungsvariablen oder Ziele.
- prodsys ist auf `1.2.3` gepinnt, weil das Backend Laufzeit-Interna nutzt.
- Unter Windows muss der Pfad zur venv kurz genug sein (MAX_PATH 260), sonst schlägt der Import
  von prodsys mit `ModuleNotFoundError` fehl.

## Stand

| Etappe | Inhalt | Status |
|---|---|---|
| 1 | Abhängigkeiten, Anlagen-Schicht mit Tests, diese Doku | erledigt |
| 2 | Netz-Builder und Basislauf (direkte Routen) | erledigt |
| 3 | Kopplung Anlagen ↔ prodsys (Kapazität Tag/Nacht/Baustelle, Langsamfahrt) | erledigt |
| 4 | Umsteigen und Umrouten (Routenvarianten), Fahrzeugdisposition | erledigt |
| 5 | GA-Anbindung (Backend-Schalter, prodsys-Kennzahlen) | offen |
| 6 | Frontend | offen |
