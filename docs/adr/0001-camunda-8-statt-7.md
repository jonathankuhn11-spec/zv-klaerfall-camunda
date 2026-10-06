# ADR-0001: Camunda 8 (Zeebe) statt Camunda 7

**Status:** akzeptiert · **Datum:** 2026-10-05

## Kontext

Beide Plattformen sind im Bankenumfeld verbreitet. Camunda 7 ist seit 2025 in der Wartungsphase
(Community-Edition eingestellt, Enterprise-Support bis 2030); Neuprojekte starten auf Camunda 8.
Der Prozess braucht User Tasks mit Kandidatengruppen, DMN, Timer und Multi-Instance.

## Entscheidung

Camunda 8.9 (Orchestration Cluster) mit externem Job-Worker über die REST API v2. Modelle werden mit
`modeler:executionPlatformVersion = 8.9.0` versioniert und gegen `camunda-cloud-8-9` gelintet.

## Konsequenzen

* Fachlogik läuft außerhalb der Engine (Worker), nicht als Java-Delegate; die Engine kennt nur Job-Typen.
* Keine eingebettete Engine, daher Offline-Pipeline als Ersatz für lokale Tests ohne Docker.
* FEEL statt JUEL in Ausdrücken; Variablen sind JSON.
* Migration bestehender Camunda-7-Modelle wäre mit dem Camunda Migration Analyzer möglich, war aber
  nicht Gegenstand.
