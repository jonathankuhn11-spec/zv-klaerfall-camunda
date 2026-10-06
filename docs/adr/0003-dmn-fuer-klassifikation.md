# ADR-0003: Klärfallklassifikation als DMN-Entscheidungstabelle, nicht im Code

**Status:** akzeptiert · **Datum:** 2026-10-05

## Kontext

Welches Team einen Klärfall mit welcher Priorität und Frist bearbeitet, ändert sich häufiger als der
Prozess (neue Limits, Umorganisation, Regulatorik). Diese Regeln im Python-Code zu halten, würde jede
Änderung zu einem Release machen.

## Entscheidung

Die Klassifikation ist eine DMN-Tabelle (`klaerfall-klassifikation`, Hit-Policy FIRST) mit vier
Eingaben, die der Prüf-Handler flach als Variablen bereitstellt. Der Code liefert nur Fakten
(welcher Befund, welcher Betrag, Sanktion ja/nein, nur Formales ja/nein); die Bewertung liegt in der
Tabelle.

## Konsequenzen

* Der Fachbereich kann Limits, Teams und Fristen im Modeler ändern und deployen.
* Jede Regel trägt eine Begründung (Annotation), die in der Tabelle sichtbar ist.
* Ein kleiner DMN-Evaluator im Projekt wertet dieselbe Datei offline aus, damit Tests und Demo ohne
  Cluster laufen; er unterstützt genau die benötigte FEEL-Teilmenge und bricht bei allem anderen
  bewusst mit Fehler ab.
* FIRST statt UNIQUE, weil die Prioritätsreihenfolge (Sanktion vor allem) fachlich gewollt ist und
  Überlappungen zwischen Regeln nicht künstlich vermieden werden müssen.
