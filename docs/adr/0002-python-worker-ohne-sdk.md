# ADR-0002: Python-Worker direkt gegen die Orchestration Cluster REST API, ohne SDK

**Status:** akzeptiert · **Datum:** 2026-10-05

## Kontext

Für Camunda 8 gibt es offizielle Clients für Java, Spring, Node.js, Go, Rust und einen Python-Client
(pyzeebe, gRPC). Das Projekt braucht Deployment, Prozessstart, Job-Aktivierung/-Abschluss, DMN-Auswertung
und User-Task-Bearbeitung.

## Entscheidung

Ein eigener, ~150 Zeilen kleiner Client auf Basis von `httpx` gegen die REST API v2, deren Endpunkte und
Schemata aus der OpenAPI-Spezifikation von Camunda 8.9 übernommen wurden.

## Begründung

* Die REST API v2 ist seit 8.8 die primäre API; gRPC wird zurückgebaut. pyzeebe setzt auf gRPC.
* Transparenz: Jeder Aufruf ist im Code lesbar und mit `respx` ohne Cluster testbar.
* Keine Abhängigkeit auf Versionszyklen eines Drittanbieter-SDKs.

## Konsequenzen

* Kein Job-Streaming (nur Long-Polling), für den Durchsatz dieses Projekts ausreichend.
* Authentifizierung aktuell nur Basic Auth / offen (lokale Entwicklung); OIDC-Token wären ergänzbar.
* Bei Umstieg auf Java/Spring würde man den offiziellen Spring-Client nehmen; die Handler-Signatur
  (`variablen → variablen`) macht die Fachlogik davon unabhängig.
