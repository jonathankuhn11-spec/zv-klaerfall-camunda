# Anforderungen: User Stories und Abnahmekriterien

Jede Story hat Abnahmekriterien im Gegeben/Wenn/Dann-Format und ist auf Beispieldateien und
automatisierte Tests rückverfolgbar (Kapitel 3). Priorität nach MoSCoW.

## 1. Epics

| Epic | Beschreibung |
|---|---|
| E1 Dateieingang | pain.001-Dateien technisch prüfen und in Einzelzahlungen zerlegen |
| E2 Fachliche Prüfung | Zahlungen nach dem Befundkatalog prüfen |
| E3 Klärfallsteuerung | Klärfälle klassifizieren, routen, bearbeiten, eskalieren |
| E4 Rückmeldung | Einreicher standardkonform informieren |
| E5 Nachweisführung | Entscheidungen revisionssicher dokumentieren |
| E6 Betrieb | Prozess lokal und in CI lauffähig halten |

## 2. User Stories

### US-01 Datei technisch prüfen (E1, Must)

*Als* Sachbearbeiter Zahlungsverkehr *möchte ich*, dass fehlerhaft aufgebaute Dateien gar nicht erst
in die Bearbeitung gelangen, *damit* ich keine Zeit mit technischen Fehlern verbringe.

* **AK-01.1** Gegeben eine Datei, die das XSD `pain.001.001.09` verletzt, wenn sie eingereicht wird,
  dann wird sie als Ganzes zurückgewiesen und der Einreicher erhält einen `pain.002` mit
  `GrpSts = RJCT` und Statusgrund `FF01`.
* **AK-01.2** Gegeben eine Datei mit abweichender Kontrollsumme (`CtrlSum` ≠ Summe der Beträge), wenn
  sie eingereicht wird, dann wird sie zurückgewiesen mit Statusgrund `AM10`.
* **AK-01.3** Gegeben eine Datei, deren `NbOfTxs` nicht zur Anzahl der Transaktionen passt, wenn sie
  eingereicht wird, dann wird sie zurückgewiesen mit Statusgrund `NARR` und einer Erläuterung.
* **AK-01.4** Gegeben eine gültige Datei mit n Transaktionen, wenn sie eingereicht wird, dann werden
  genau n Instanzen des Teilprozesses *Zahlung prüfen und disponieren* gestartet.
* **AK-01.5** Dateien mit DOCTYPE-Deklarationen oder externen Entitäten werden abgewiesen (Schutz vor
  XXE-Angriffen).

### US-02 Konto- und Bankdaten prüfen (E2, Must)

*Als* Bank *möchte ich* ungültige IBANs und unpassende BICs erkennen, *damit* keine Zahlung an ein
nicht existierendes Konto geht.

* **AK-02.1** Gegeben eine Zahlung mit falscher IBAN-Prüfziffer, wenn sie geprüft wird, dann entsteht
  der Befund `IBAN_PRUEFZIFFER` (blockierend, nicht korrigierbar).
* **AK-02.2** Gegeben eine Zahlung, deren BIC laut Bankleitzahlregister nicht zur IBAN gehört, wenn sie
  geprüft wird, dann entsteht `BIC_IBAN_MISMATCH` mit dem registrierten BIC als Korrekturvorschlag.
* **AK-02.3** Gegeben einen formal ungültigen BIC, dann entsteht `BIC_UNGUELTIG`.

### US-03 Auftragsdaten prüfen und formale Mängel automatisch korrigieren (E2, Must)

*Als* Sachbearbeiter *möchte ich*, dass rein formale Mängel ohne mein Zutun korrigiert werden,
*damit* ich nur echte Klärfälle sehe.

* **AK-03.1** Gegeben ein Ausführungsdatum an einem Samstag, wenn geprüft wird, dann wird das Datum
  automatisch auf den nächsten TARGET2-Bankarbeitstag verschoben, die Zahlung freigegeben und die
  Korrektur im Audit-Trail und im Prozess (`korrekturen`) dokumentiert.
* **AK-03.2** Gegeben `ChrgBr = DEBT` bei einer SEPA-Überweisung, wenn geprüft wird, dann wird auf
  `SLEV` korrigiert und freigegeben.
* **AK-03.3** Gegeben ein Ausführungsdatum mehr als 90 Tage in der Zukunft, dann ist der Befund nicht
  automatisch korrigierbar und geht als Klärfall *Auftragsdaten* an den Zahlungsverkehr.
* **AK-03.4** Gegeben Währung USD bei Service Level SEPA, dann Befund `WAEHRUNG`, Klärfall
  *Auftragsdaten*.
* **AK-03.5** Nach einer automatischen Korrektur wird die Zahlung genau einmal erneut geprüft; eine
  zweite Korrekturschleife ist ausgeschlossen (`erneutePruefung`).
* **AK-03.6** Die erneute Prüfung meldet die Zahlung nicht fälschlich als Dublette.

### US-04 Vier-Augen-Freigabe (E3, Must)

*Als* Disponent *möchte ich* Zahlungen ab einem Limit persönlich freigeben, *damit* das
Vier-Augen-Prinzip eingehalten wird.

* **AK-04.1** Gegeben einen Betrag ≥ 50.000 EUR, wenn geprüft wird, dann entsteht ein User Task für
  `team-disposition` mit Priorität 50 und Fälligkeit in 8 Stunden.
* **AK-04.2** Gegeben einen Betrag ≥ 500.000 EUR, dann geht der Task an `team-disposition-leitung`
  mit Priorität 70 und Fälligkeit in 4 Stunden.
* **AK-04.3** Das Limit ist ohne Codeänderung konfigurierbar (Task-Header `betragslimit`).
* **AK-04.4** Freigabe und Ablehnung erfordern eine Begründung (Pflichtfeld, mindestens 5 Zeichen).

### US-05 Sanktionsprüfung (E3, Must)

*Als* Compliance-Beauftragter *möchte ich* jeden Sanktionstreffer selbst beurteilen, *damit* keine
Zahlung an sanktionierte Parteien automatisch ausgeführt wird.

* **AK-05.1** Gegeben einen Kreditornamen, der einem Listeneintrag entspricht (nach Normalisierung
  von Rechtsformen, Umlauten, Groß-/Kleinschreibung) oder ihm zu ≥ 85 % ähnelt, wenn geprüft wird,
  dann geht der Fall mit Priorität KRITISCH an `team-compliance`, Fälligkeit 2 Stunden.
* **AK-05.2** Ein Sanktionstreffer wird niemals automatisch korrigiert oder freigegeben, auch wenn
  alle anderen Befunde korrigierbar wären.
* **AK-05.3** Bei Ablehnung erhält der Einreicher den Statusgrund `RR04`.

### US-06 Dublettenerkennung (E3, Must)

* **AK-06.1** Gegeben eine Zahlung, die mit identischem Fingerprint innerhalb von 24 Stunden bereits
  eingereicht wurde, dann entsteht `DUBLETTE`, Klärfall *Dublette* mit Priorität HOCH.
* **AK-06.2** Bei Ablehnung Statusgrund `DUPL`.

### US-07 Klärfall bearbeiten (E3, Must)

*Als* Sachbearbeiter *möchte ich* im Klärfall alle Befunde und die Zahlungsdaten auf einen Blick
sehen und mit einer Begründung entscheiden, *damit* die Bearbeitung schnell und nachvollziehbar ist.

* **AK-07.1** Das Formular zeigt Kategorie, Priorität, Frist, Zielteam, Zahlungsdaten und alle
  Befunde (schreibgeschützt).
* **AK-07.2** Entscheidung *Freigeben* führt zu `ACCP`, *Ablehnen* zu `RJCT` mit dem Statusgrund des
  führenden Befunds und der Begründung als `AddtlInf`.
* **AK-07.3** Bearbeiterkürzel und Begründung landen im Audit-Trail (Akteur ≠ `system`).

### US-08 SLA-Überwachung (E3, Should)

* **AK-08.1** Läuft die Frist eines Klärfalls ab, wird die Eskalation ausgelöst und protokolliert
  (`KLAERFALL_ESKALIERT`), ohne dass der Klärfall abgebrochen wird.

### US-09 Statusreport (E4, Must)

* **AK-09.1** Für jede verarbeitete Datei entsteht genau ein `pain.002.001.12`, das das offizielle XSD
  erfüllt.
* **AK-09.2** Gruppenstatus `ACCP`/`PART`/`RJCT` gemäß Fachkonzept Kapitel 7; je Transaktion
  `TxSts` und ggf. `StsRsnInf`.
* **AK-09.3** `NbOfTxsPerSts` enthält die Anzahl je Status.

### US-10 Revisionssicherer Audit-Trail (E5, Must)

* **AK-10.1** Jede Entscheidung erzeugt einen Eintrag mit Zeitstempel, Ereignis, Referenz, Akteur.
* **AK-10.2** Jeder Eintrag enthält den Hash seines Vorgängers; `zv audit verify` erkennt geänderte
  und gelöschte Einträge.
* **AK-10.3** Der Audit-Trail wird bei einem Demo-Reset nicht gelöscht, sondern rotiert.

### US-11 Betrieb ohne und mit Cluster (E6, Should)

* **AK-11.1** `zv demo --offline` verarbeitet alle Beispieldateien ohne Camunda in unter 30 Sekunden
  mit identischer Fachlogik und derselben DMN-Datei.
* **AK-11.2** `docker compose up`, `zv deploy`, `zv ingest`, `zv worker` führen denselben Durchlauf
  im Cluster aus; Klärfälle sind in Tasklist bearbeitbar.
* **AK-11.3** Ein umbenannter Job-Typ oder eine umbenannte Decision-ID wird von den Tests erkannt,
  bevor etwas deployt wird.

## 3. Rückverfolgbarkeit

| Story | Beispieldatei(en) | Automatisierte Tests |
|---|---|---|
| US-01 | `11_schemafehler.xml`, `12_kontrollsumme.xml` | `test_iso20022.py::test_schemafehler_*`, `test_xxe_*`; `test_handlers_und_pipeline.py::test_nachricht_validieren_*` |
| US-02 | `07_iban_pruefziffer.xml`, `08_bic_passt_nicht.xml` | `test_rules.py::test_iban_pruefziffer`, `test_bic_ungueltig_und_bic_iban_mismatch` |
| US-03 | `02_gebuehrenregelung.xml`, `03_ausfuehrung_wochenende.xml`, `09_waehrung_usd.xml` | `test_rules.py::test_gebuehrenregelung_*`, `test_ausfuehrungsdatum_*`, `test_waehrung_*`, `test_nur_auto_korrigierbar_und_korrektur`; `test_handlers_und_pipeline.py::test_korrigieren_*` |
| US-04 | `04_betrag_limit.xml`, `05_grossbetrag.xml` | `test_rules.py::test_betrag_null_und_limit`; `test_dmn_und_audit.py::test_dmn_klassifikation`; `test_handlers_und_pipeline.py::test_pruefen_betragslimit_aus_task_header` |
| US-05 | `06_sanktion.xml` | `test_rules.py::test_sanktion_*`, `test_fuehrender_befund_folgt_dem_rang`; `test_dmn_und_audit.py::test_dmn_klassifikation` |
| US-06 | `10_dublette.xml` | `test_rules.py::test_dublette_*` |
| US-07 | alle Klärfall-Dateien | `test_handlers_und_pipeline.py::test_freigeben_und_ablehnen`; `test_modelle_konsistenz.py::test_user_task_konfiguration`; Integration `test_camunda_e2e.py` |
| US-08 | – (`zv demo --offline --sla-ueberschritten`) | `test_handlers_und_pipeline.py::test_offline_eigener_entscheider_und_sla` |
| US-09 | `13_gemischt.xml` (PART) | `test_iso20022.py::test_statusreport_*`, `test_gruppenstatus`; `test_offline_durchlauf_aller_beispiele` (XSD-Prüfung jedes Reports) |
| US-10 | alle | `test_dmn_und_audit.py::test_audit_*` |
| US-11 | alle | `test_offline_durchlauf_aller_beispiele`; `test_modelle_konsistenz.py`; CI-Job *integration* |
