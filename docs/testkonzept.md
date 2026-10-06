# Testkonzept

## 1. Ziele und Risiken

Der Prozess entscheidet über Geldflüsse. Die größten Risiken, gegen die getestet wird:

| Risiko | Folge | Absicherung |
|---|---|---|
| Fachregel liefert falschen Befund (z. B. gültige IBAN als ungültig) | Falschablehnung, Kundenunzufriedenheit | Regeltests mit Positiv-/Negativfällen und Grenzwerten |
| Klassifikation routet an das falsche Team | SLA-Verletzung, Compliance-Lücke | DMN-Tests gegen die echte Tabelle, Hit-Policy-Reihenfolge |
| Sanktionstreffer wird automatisch freigegeben | regulatorischer Verstoß | Rangtest, DMN-Regel 1 vor allen anderen, Vorrang-Test |
| Prozessmodell und Code laufen auseinander (Job-Typ, Decision-ID, Variablen) | Prozess bleibt hängen, Incident | Konsistenztests über BPMN/DMN/Form/Code |
| Ungültiger pain.002 verlässt das System | Rückmeldung wird vom Kundensystem abgelehnt | XSD-Validierung vor Rückgabe, Tests je Report |
| Audit-Trail manipulierbar | kein Revisionsnachweis | Hashketten-Tests mit Manipulation und Löschung |
| Endlosschleife bei Auto-Korrektur | hängende Instanz | Schleifenschutz `erneutePruefung`, Pipeline-Test |
| Schadhafte XML-Eingaben (XXE, Billion Laughs) | Datenabfluss, Ausfall | Parser ohne Entity-Auflösung/DTD, Negativtest |

## 2. Teststufen

```
                 ┌───────────────────────────┐
                 │  Integration (Cluster)    │  tests/integration/   – echter Camunda 8, deploy → ingest →
                 │  1 Szenario, CI-Job       │                         Worker → User Task → pain.002
                 ├───────────────────────────┤
                 │  Pipeline / End-to-End    │  test_handlers_und_pipeline.py – 13 Beispieldateien
                 │  offline, identische Logik│                         gegen Erwartungswerte
                 ├───────────────────────────┤
                 │  Komponenten              │  Handler, DMN-Tabelle, Audit-Trail, REST-Client (respx),
                 │                           │  Worker (Fake-Client), Modell-Konsistenz
                 ├───────────────────────────┤
                 │  Unit                     │  Parser, Regeln, Kalender, Sanktionen, Dubletten,
                 │                           │  pain.002-Generator
                 └───────────────────────────┘
           + statische Prüfung: Ruff, Mypy (strict), bpmnlint (camunda-cloud-8-9), dmnlint
```

## 3. Testdaten

* **Beispieldateien** `samples/pain.001/*.xml`: 13 Szenarien, generiert aus `tools/generate_samples.py`
  (reproduzierbar, Änderungen diff-bar). IBANs werden aus echten Bankleitzahlen generiert, damit
  Prüfziffern und der BIC-Abgleich gegen das Bankleitzahlregister realistisch sind; Firmen sind fiktiv.
* **Fachdatum** `2026-10-06` (Buchungstag), festgeschrieben in `samples/pain.001/fachdatum.txt`, damit
  Datumsregeln deterministisch bleiben. Alle datumsabhängigen Tests und die Demo verwenden dieses Datum.
* **Sanktionsliste** `src/zv_klaerfall/data/sanktionsliste.json`: ausschließlich fiktive Einträge.
* **Kalender**: Ostern 2024–2027 nach Gauß als Referenzwerte, TARGET2-Feiertage.

## 4. Testfallkatalog (Auszug)

| ID | Testfall | Eingabe | Erwartung | Umsetzung |
|---|---|---|---|---|
| TC-01 | Gültige Datei, 3 Zahlungen | `01_gueltig.xml` | 3 × ACCP, Gruppe ACCP | Pipeline-Test |
| TC-02 | Gebührenregelung korrigieren | `02_gebuehrenregelung.xml` | `GEBUEHRENREGELUNG`, Korrektur DEBT→SLEV, ACCP | Pipeline, `test_gebuehrenregelung_auto_korrigierbar` |
| TC-03 | Ausführung Samstag | `03_ausfuehrung_wochenende.xml` | Verschiebung auf Montag 12.10., ACCP | Pipeline, Datums-Parametrisierung |
| TC-04 | Vier-Augen 75.000 EUR | `04_betrag_limit.xml` | Freigabe, `team-disposition`, nach Freigabe ACCP | Pipeline, DMN, Integration |
| TC-05 | Großbetrag 1,25 Mio EUR | `05_grossbetrag.xml` | `team-disposition-leitung`, HOCH | Pipeline, DMN |
| TC-06 | Sanktionstreffer (unscharf) | `06_sanktion.xml` | Compliance, KRITISCH, RJCT RR04 | Pipeline, `test_sanktion_*` |
| TC-07 | IBAN-Prüfziffer | `07_iban_pruefziffer.xml` | Kontodaten, RJCT AC01 | Pipeline, `test_iban_pruefziffer` |
| TC-08 | BIC passt nicht zur IBAN | `08_bic_passt_nicht.xml` | `BIC_IBAN_MISMATCH`, Vorschlag INGDDEFFXXX, RJCT RC01 | Pipeline, Regeltest |
| TC-09 | Währung USD | `09_waehrung_usd.xml` | Auftragsdaten, RJCT AM11 | Pipeline |
| TC-10 | Dublette | `10_dublette.xml` nach `01` | Dublette, RJCT DUPL | Pipeline, `test_dublette_*` |
| TC-11 | XSD-Verletzung | `11_schemafehler.xml` | Datei zurückgewiesen, pain.002 RJCT FF01 | Pipeline, Handler-Test |
| TC-12 | Kontrollsumme | `12_kontrollsumme.xml` | Datei zurückgewiesen, pain.002 RJCT AM10 | Pipeline, Handler-Test |
| TC-13 | Gemischte Datei | `13_gemischt.xml` | 3 × ACCP (davon 1 korrigiert, 1 freigegeben), 1 × RJCT, Gruppe PART | Pipeline |
| TC-14 | Rang bei mehreren Befunden | synthetisch | Sanktion führt vor Limit und IBAN | `test_fuehrender_befund_folgt_dem_rang` |
| TC-15 | Schleifenschutz | synthetisch | nach Korrektur `erneutePruefung = true`, keine Dublette | `test_korrigieren_setzt_erneute_pruefung_*` |
| TC-16 | Audit-Manipulation | synthetisch | geänderte/gelöschte Zeile wird erkannt | `test_audit_kette_und_manipulationserkennung` |
| TC-17 | SLA-Eskalation | `06_sanktion.xml` mit `sla_simulieren` | `KLAERFALL_ESKALIERT`, Klärfall bleibt entscheidbar | `test_offline_eigener_entscheider_und_sla` |
| TC-18 | Modell-Code-Vertrag | BPMN/DMN/Form | Job-Typen = Handler, Decision-ID, Form-ID, Gateway-Defaults | `test_modelle_konsistenz.py` |
| TC-19 | XXE | synthetisch | abgewiesen, kein Dateizugriff | `test_xxe_wird_nicht_aufgeloest` |
| TC-20 | Cluster-Durchlauf | `01`, `04` | Instanzen abgeschlossen, User Task per API entschieden, pain.002 | `tests/integration/test_camunda_e2e.py` |

## 5. Ausführung

```bash
pytest                      # Unit, Komponenten, Pipeline (Sekunden)
pytest --cov=zv_klaerfall   # mit Abdeckung
make lint lint-models       # Ruff, Mypy strict, bpmnlint, dmnlint
docker compose -f docker/docker-compose.yaml up -d
CAMUNDA_URL=http://localhost:8080 pytest -m integration
```

Die CI (`.github/workflows/ci.yml`) führt alle Stufen aus, zusätzlich: generierte Modelle und
Beispiele müssen mit dem Repository-Stand übereinstimmen (`git diff --exit-code`), Docker-Image baut,
Compose-Datei ist valide.

## 6. Abnahme und Nachweis

* Jede Story in `docs/anforderungen.md` ist auf Tests rückverfolgbar (Kapitel 3 dort).
* Testergebnisse der CI und der Audit-Trail eines Demo-Durchlaufs (`.data/audit-trail.jsonl`) dienen als
  Abnahmenachweis; `zv audit verify` bestätigt die Integrität.
* Fachliche Abnahme der DMN-Tabelle erfolgt am Bild `docs/img/klaerfall-klassifikation.png` bzw. im
  Camunda Modeler, nicht am Code.
