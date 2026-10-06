# Architektur

## 1. Überblick

```
   Firmenkunde                    Bank: Prozessplattform                         Bank: Fachsysteme
   ───────────                    ──────────────────────                         ─────────────────
   pain.001 ──► zv ingest ──► Camunda 8 Orchestration Cluster ◄──── Job-Worker (Python) ──► Buchungsjournal
                               ├─ zv-nachricht-verarbeiten          │  zv.nachricht-validieren    (Stellvertreter ZV-System)
                               ├─ zv-zahlung-pruefen (×n)           │  zv.pruefen / zv.korrigieren
                               ├─ DMN klaerfall-klassifikation      │  zv.freigeben / zv.ablehnen
                               └─ User Tasks (Tasklist)             │  zv.eskalieren / zv.statusreport
   pain.002 ◄──────────────────────────────────────────────────────┘        │
                                                                     Audit-Trail (JSONL, Hashkette)
```

Camunda hält den **Zustand des Ablaufs** (welche Zahlung wartet worauf), der Worker hält **keinen**
Zustand außer den fachlichen Nebenläufen (Dublettenspeicher, Audit-Trail, Journal). Die Fachlogik ist
in reinen Funktionen gekapselt und läuft identisch im Worker und in der Offline-Pipeline.

## 2. Komponenten

| Komponente | Modul | Verantwortung |
|---|---|---|
| ISO-20022-Adapter | `iso20022/pain001.py`, `pain002.py` | XSD-Validierung, Mapping pain.001 → Domänenmodell, Erzeugung pain.002 |
| Domänenmodell | `models.py` | `Zahlung`, `Befund`, `Pruefergebnis`, `Klassifikation`, `Ergebnis`; Befundkatalog, Statusgründe |
| Fachregeln | `rules/validation.py` (+ `kalender`, `sanktionen`, `dubletten`) | Prüfung und Korrektur |
| DMN-Light | `rules/dmn_lite.py` | FEEL-Teilmenge zur Offline-Auswertung der echten DMN-Datei |
| Job-Handler | `handlers.py` | Fachlogik je Service Task als reine Funktion `(variablen, dienste, kontext) → variablen` |
| Camunda-Client | `camunda/client.py` | Orchestration Cluster REST API v2 (Deployment, Instanzen, Jobs, Decisions, User Tasks) |
| Worker | `camunda/worker.py` | Long-Polling je Job-Typ, Completion/Failure/BPMN-Error |
| Offline-Pipeline | `pipeline/offline.py` | Nachbildung des Kontrollflusses ohne Cluster |
| Audit-Trail | `audit/trail.py` | Append-only JSONL mit SHA-256-Hashkette |
| CLI | `cli.py` | `validate`, `demo`, `deploy`, `ingest`, `worker`, `tasks`, `audit`, `status` |
| Modelle | `bpmn/*.bpmn`, `bpmn/*.dmn`, `forms/*.form` | generiert aus `tools/generate_models.py` |

## 3. Variablenvertrag (Prozess ↔ Worker)

Alle Variablen sind JSON; Datumswerte ISO-8601-Strings, Beträge als Zahl (float) für DMN-Vergleiche,
fachlich exakt bleibt `Decimal` innerhalb des Workers.

### Prozess `zv-nachricht-verarbeiten`

| Variable | Typ | Gesetzt von | Gelesen von |
|---|---|---|---|
| `nachrichtXml` | string | Einreichung (`zv ingest`) | `zv.nachricht-validieren` |
| `dateiname` | string | Einreichung | Validierung, Audit |
| `nachrichtGueltig` | boolean | `zv.nachricht-validieren` | Gateway *Nachricht gültig?* |
| `nachrichtFehler`, `nachrichtFehlerCode`, `nachrichtFehlerText` | list/string | Validierung | `zv.statusreport` (Zurückweisung) |
| `nachricht` | object (`msgId`, `pmtInfId`, `einreicher`, …) | Validierung | Call Activity (Input), `zv.statusreport` |
| `zahlungen` | list[object] | Validierung | Multi-Instance `inputCollection` |
| `zahlung` | object | Multi-Instance `inputElement` | Call Activity (Input) |
| `ergebnis` | object (`endToEndId`, `status`, `statusgrund`, `kommentar`, `korrekturen`) | Teilprozess (Output-Mapping) | Multi-Instance `outputElement` |
| `ergebnisse` | list[object] | Multi-Instance `outputCollection` | `zv.statusreport` |
| `statusreportPfad`, `gruppenstatus` | string | `zv.statusreport` | Operate/Reporting |

### Prozess `zv-zahlung-pruefen`

| Variable | Typ | Gesetzt von | Gelesen von |
|---|---|---|---|
| `zahlung`, `nachricht` | object | Input-Mapping des Aufrufers | alle Handler |
| `pruefung` | object (`befunde[]`, `gueltig`, `fuehrenderBefund`, …) | `zv.pruefen` | `zv.korrigieren`, `zv.ablehnen`, Formular |
| `befundeVorhanden` | boolean | `zv.pruefen` | Gateway *Befunde vorhanden?* |
| `befundCode`, `betrag`, `sanktionstreffer`, `nurAutoKorrigierbar` | string/number/boolean | `zv.pruefen` | **DMN-Eingaben** |
| `befundeText`, `zahlungText` | string | `zv.pruefen` | Formular (schreibgeschützt) |
| `erneutePruefung` | boolean | `zv.pruefen` (Echo), `zv.korrigieren` (true) | Gateway *Automatisch korrigierbar?*, `zv.pruefen` |
| `klassifikation` | object (`kategorie`, `prioritaet`, `zielTeam`, `autoKorrektur`, `sla`) | **DMN** (`resultVariable`) | Gateway, User Task (Kandidatengruppe, Fälligkeit, Priorität), Timer, Formular |
| `korrekturen` | list[string] | `zv.korrigieren` | `zv.freigeben`, Ergebnis |
| `entscheidung`, `bearbeiter`, `kommentar` | string | **Formular** | Gateway *Entscheidung*, `zv.freigeben`, `zv.ablehnen` |
| `ergebnis`, `buchungsId` | object/string | `zv.freigeben` / `zv.ablehnen` | Output-Mapping an den Aufrufer |
| `eskaliertAm` | string | `zv.eskalieren` | Operate |

Task-Header: `betragslimit` am Task *Zahlung fachlich prüfen* (Standard `50000.00`).

## 4. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Technischer Fehler im Handler (Exception) | `POST /jobs/{key}/failure` mit `retries − 1` und 5 s Backoff; bei 0 Retries Incident in Operate, dort nach Behebung auflösbar |
| Fachlicher Fehler, der im Prozess behandelt werden soll | `FachlicherFehler(code)` → `POST /jobs/{key}/error` (BPMN-Error; aktuell kein Catch-Event modelliert, vorgesehen z. B. für `KONTO_GESPERRT`) |
| Nachricht technisch ungültig | kein Fehler, sondern Prozesspfad *Zurückweisung melden* |
| DMN ohne Treffer | ausgeschlossen durch Auffangregel 8 (Test erzwingt sie) |
| Worker doppelt (Job nach Timeout erneut ausgeliefert) | Handler sind wiederholbar; Audit-Trail enthält dann zwei Einträge; produktiv: Idempotenz über EndToEndId (siehe Fachkonzept 10) |
| Cluster nicht erreichbar | Worker wartet 3 s und versucht es erneut; CLI-Befehle brechen mit Exit-Code 2 ab |

## 5. Betrieb

* **Konfiguration** über Umgebungsvariablen: `CAMUNDA_URL`, `CAMUNDA_USER`/`CAMUNDA_PASSWORD`
  (Basic Auth, falls aktiviert), `ZV_DATA_DIR` (Laufzeitdaten), `ZV_BETRAGSLIMIT`, `ZV_BANK_NAME`,
  `ZV_WORKER_NAME`, `ZV_PROJEKT_DIR` (Ort von `bpmn/`, `forms/`, `samples/`).
* **Laufzeitdaten** unter `ZV_DATA_DIR`: `audit-trail.jsonl`, `buchungen.jsonl`, `dubletten.sqlite`,
  `statusreports/*.xml`.
* **Skalierung**: Worker sind zustandslos bezüglich des Prozesses und können mehrfach gestartet werden;
  Dublettenspeicher und Audit-Trail wären produktiv in eine Datenbank zu verlagern.
* **Camunda lokal**: `docker/docker-compose.yaml` (ein Container, H2-Speicher, ≈ 1,5 GB RAM);
  Operate/Tasklist/Identity unter `http://localhost:8080`, Zugang `demo`/`demo`.

## 6. Sicherheit

* XML-Parsing ohne Entity-Auflösung, ohne DTD, ohne Netzwerkzugriff (XXE-/DoS-Schutz).
* Keine Geheimnisse im Repository; Demo-Zugänge sind die Standardwerte der Camunda-Distribution.
* Der Audit-Trail ist manipulationserkennbar, nicht manipulationssicher: Produktiv gehört er in einen
  WORM-Speicher oder eine Datenbank mit Append-only-Rechten.
