# ADR-0005: BPMN/DMN/Formular werden aus einem Python-Skript generiert

**Status:** akzeptiert · **Datum:** 2026-10-05

## Kontext

Im Camunda Modeler geklickte Diagramme sind schwer zu reviewen (XML-Diffs mit Koordinaten) und
die Verbindung zwischen Modell und Code (Job-Typen, Variablen) ist implizit.

## Entscheidung

`tools/generate_models.py` erzeugt beide Prozesse, die DMN-Tabelle und das Formular inklusive
Diagram-Interchange. Die Dateien liegen trotzdem im Repository (Review, Modeler-Kompatibilität) und
die CI prüft, dass sie mit dem Generator übereinstimmen.

## Konsequenzen

* Layout und Fachinhalt an einer Stelle, Änderungen als lesbarer Python-Diff.
* Die Modelle sind normkonform und im Camunda Modeler editierbar; eine Änderung dort müsste in den
  Generator zurückgeführt werden (sonst schlägt der CI-Vergleich an).
* bpmnlint (camunda-cloud-8-9) und dmnlint laufen in der CI, damit generierte Modelle dieselben
  Qualitätsregeln erfüllen wie geklickte.
