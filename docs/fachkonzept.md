# Fachkonzept: Klärfallbearbeitung im Firmenkunden-Zahlungsverkehr

| | |
|---|---|
| **Fachdomäne** | Zahlungsverkehr (SEPA-Überweisungen), Einreichung durch Firmenkunden per Datei |
| **Nachrichtenstandard** | ISO 20022: `pain.001.001.09` (Eingang), `pain.002.001.12` (Statusrückmeldung) |
| **Prozessplattform** | Camunda 8 (BPMN 2.0, DMN 1.3, Camunda Forms) |
| **Stand** | Oktober 2026, Version 1.0 |

## 1. Ausgangslage und Ziel

Firmenkunden reichen Sammelüberweisungen als `pain.001`-Datei ein (über EBICS, SFTP oder Kundenportal).
Vor der Ausführung muss die Bank jede Zahlung **disponieren**: technisch prüfen, fachlich prüfen und
bei Auffälligkeiten entscheiden, ob die Zahlung ausgeführt, korrigiert oder abgelehnt wird. Dieser
Schritt ist in vielen Häusern eine Mischung aus Kernbank-Logik, Excel-Listen und E-Mail-Rückfragen.
Die Folgen sind bekannt: uneinheitliche Entscheidungen, keine SLA-Transparenz, aufwendige
Nachweisführung gegenüber Revision und Aufsicht.

**Ziel dieses Konzepts** ist ein durchgängiger, auditierbarer Prozess, in dem

* jede Datei automatisch technisch und fachlich geprüft wird,
* Klärfälle nach einheitlichen, vom Fachbereich gepflegten Regeln (DMN) klassifiziert und an das
  zuständige Team geroutet werden,
* formale Mängel ohne menschliches Zutun korrigiert werden,
* jede Entscheidung revisionssicher protokolliert wird und
* der Einreicher über einen standardkonformen `pain.002`-Statusreport informiert wird.

## 2. Abgrenzung

**Im Scope:** SEPA-Überweisungen (`SvcLvl/Cd = SEPA`) in EUR aus `pain.001.001.09`; Prüfung,
Klassifikation, Klärfallbearbeitung, Statusreport, Audit-Trail; ein Buchungsjournal als Stellvertreter
für das Zahlungsverkehrssystem.

**Nicht im Scope:** Lastschriften (`pain.008`), Echtzeitüberweisungen mit 10-Sekunden-Frist, Embargo-
und Geldwäsche-Screening mit produktiver Sanktionsliste (hier: fiktive Demo-Liste), Deckungsprüfung
gegen Kontosalden, Interbanken-Clearing (`pacs.*`), EBICS-Transport, Berechtigungskonzept.

## 3. Rollen und Teams

| Rolle | Kandidatengruppe in Camunda | Aufgabe |
|---|---|---|
| Sachbearbeitung Zahlungsverkehr | `team-zahlungsverkehr` | Rückfragen zu Konto- und Auftragsdaten, Dubletten |
| Disposition | `team-disposition` | Vier-Augen-Freigabe ab Betragslimit |
| Leitung Disposition | `team-disposition-leitung` | Freigabe von Großbeträgen (≥ 500.000 EUR) |
| Compliance | `team-compliance` | Prüfung von Sanktionstreffern |
| Teamleitung (Eskalation) | – (Benachrichtigung) | Wird bei SLA-Verletzung informiert |
| Einreicher (Firmenkunde) | – | Erhält `pain.002`-Statusreport |

## 4. Prozesse

### 4.1 Zahlungsauftragsdatei verarbeiten (`zv-nachricht-verarbeiten`)

![Prozess Nachricht verarbeiten](img/zv-nachricht-verarbeiten.png)

| Schritt | Fachliche Beschreibung |
|---|---|
| **pain.001 eingegangen** | Start je Datei. Eingangsvariablen: Dateiinhalt (`nachrichtXml`), Dateiname. |
| **Nachricht validieren und aufteilen** | Technische Prüfung gegen das ISO-20022-XSD, Plausibilität der Kopfdaten (`NbOfTxs` = Anzahl Transaktionen, `CtrlSum` = Summe der Beträge), Aufteilung in Einzelzahlungen. |
| **Nachricht gültig?** | Nein → die gesamte Datei wird zurückgewiesen. Ja → jede Zahlung wird einzeln disponiert. |
| **Zurückweisung melden** | `pain.002` mit Gruppenstatus `RJCT` und Statusgrund (`FF01` ungültiges Format, `AM10` Kontrollsumme, `NARR` sonstige). |
| **Zahlung prüfen und disponieren** | Call Activity, parallel je Transaktion (Multi-Instance); ruft den Teilprozess 4.2 auf. |
| **Statusreport pain.002 erstellen** | Aggregiert die Einzelergebnisse: `ACCP` (alle angenommen), `RJCT` (alle abgelehnt), `PART` (teilweise). |

### 4.2 Zahlung prüfen und disponieren (`zv-zahlung-pruefen`)

![Prozess Zahlung prüfen](img/zv-zahlung-pruefen.png)

| Schritt | Fachliche Beschreibung |
|---|---|
| **Zahlung fachlich prüfen** | Führt den Befundkatalog (Kapitel 5) aus und liefert alle Befunde mit Schweregrad und ggf. Korrekturvorschlag. |
| **Befunde vorhanden?** | Kein blockierender Befund → direkt freigeben. Sonst → klassifizieren. |
| **Klärfall klassifizieren (DMN)** | Entscheidungstabelle (Kapitel 6) bestimmt Kategorie, Priorität, Zielteam, Bearbeitungsfrist und ob automatisch korrigiert werden darf. |
| **Automatisch korrigierbar?** | Ja → Korrektur anwenden und erneut prüfen (genau eine Schleife, danach zwingend manuell). Nein → Klärfall. |
| **Klärfall bearbeiten** | User Task mit Formular (Abbildung unten). Kandidatengruppe = Zielteam, Fälligkeit = jetzt + SLA, Priorität aus Klassifikation. Entscheidung: *Freigeben* oder *Ablehnen*, jeweils mit Pflicht-Begründung. |
| **SLA überschritten** | Nicht unterbrechender Timer: Der Klärfall bleibt offen, parallel wird die Teamleitung informiert (Eskalation). |
| **Zahlung freigeben und buchen** | Übergabe an das Zahlungsverkehrssystem (hier: Buchungsjournal), Ergebnis `ACCP`. |
| **Zahlung ablehnen** | Ergebnis `RJCT` mit ISO-Statusgrund des führenden Befunds und der Begründung des Bearbeiters. |

![Formular Klärfall bearbeiten](img/zv-klaerfall-form.png)

## 5. Befundkatalog (fachliche Prüfregeln)

Die Prüfung liefert **Befunde**. Nur Befunde der Schwere *BLOCKIEREND* lösen eine Klärfallbehandlung
aus; *INFO*-Befunde werden nur protokolliert. Hat eine Zahlung mehrere blockierende Befunde, bestimmt
der **Rang** den *führenden Befund*, der die Klassifikation und den Statusgrund im `pain.002` bestimmt.

| Rang | Befund | Regel | Schwere | Auto-Korrektur | ISO-Statusgrund |
|---:|---|---|---|---|---|
| 100 | `SANKTION` | Name von Debitor oder Kreditor trifft auf einen Sanktionslisteneintrag (exakt nach Normalisierung oder Ähnlichkeit ≥ 85 %) | blockierend | nie | `RR04` Regulatory Reason |
| 90 | `BETRAG_LIMIT` | Betrag ≥ Freigabelimit (Standard 50.000 EUR, je Prozess über Task-Header konfigurierbar) | blockierend | nein | `AM02` Not Allowed Amount |
| 80 | `DUBLETTE` | Identischer Fingerprint (Debitor-IBAN, Kreditor-IBAN, Betrag, Währung, EndToEndId) innerhalb von 24 h bereits eingereicht | blockierend | nein | `DUPL` Duplicate Payment |
| 70 | `IBAN_PRUEFZIFFER` | IBAN fehlt oder Prüfziffer (ISO 7064 MOD 97-10) bzw. Länderformat ungültig | blockierend | nein | `AC01` Incorrect Account Number |
| 65 | `BIC_IBAN_MISMATCH` | Angegebener BIC gehört laut Bankleitzahlregister nicht zur IBAN | blockierend | nein (Vorschlag: BIC aus Register) | `RC01` Bank Identifier Incorrect |
| 60 | `BIC_UNGUELTIG` | BIC-Format ungültig (Länge, Länderkennung) | blockierend | nein | `RC01` |
| 50 | `WAEHRUNG` | Service Level SEPA, aber Währung ≠ EUR | blockierend | nein | `AM11` Invalid Transaction Currency |
| 40 | `BETRAG_NULL` | Betrag ≤ 0 | blockierend | nein | `AM01` Zero Amount |
| 30 | `GEBUEHRENREGELUNG` | `ChrgBr` ≠ `SLEV` bei SEPA (EPC-Rulebook verlangt SLEV) | blockierend | **ja** → `SLEV` | `CH16` Element Content Formally Incorrect |
| 20 | `AUSFUEHRUNGSDATUM` | Datum in der Vergangenheit oder kein TARGET2-Bankarbeitstag (Wochenende, Neujahr, Karfreitag, Ostermontag, 1. Mai, 25./26. Dezember) | blockierend | **ja** → nächster Bankarbeitstag | `DT01` Invalid Date |
| 20 | `AUSFUEHRUNGSDATUM` | Datum mehr als 90 Tage in der Zukunft | blockierend | nein | `DT01` |
| 10 | `VERWENDUNGSZWECK_FEHLT` | Weder unstrukturierter Verwendungszweck noch Purpose Code | info | – | `NARR` |

Fachliche Hinweise:

* **Schreibweise der IBAN** (Leerzeichen, Kleinbuchstaben) wird bereits durch das XSD abgefangen
  (Pattern `[A-Z]{2}[0-9]{2}[a-zA-Z0-9]{1,30}`) und ist deshalb kein Befund dieses Katalogs – die
  Datei wird als Ganzes mit `FF01` zurückgewiesen.
* Die Prüfung der Kopfdaten (`NbOfTxs`, `CtrlSum`) ist eine **Nachrichtenprüfung**, keine
  Zahlungsprüfung: Stimmen sie nicht, kann der Bank nicht klar sein, ob die Datei vollständig ist, also
  wird sie komplett zurückgewiesen (`AM10` bei Kontrollsumme).
* Ein Sanktionstreffer ist regulatorisch **immer** manuell durch Compliance zu bestätigen oder zu
  verwerfen, unabhängig von Betrag oder sonstigen Befunden (daher Rang 100).

## 6. Klassifikationsregeln (DMN `klaerfall-klassifikation`)

![DMN-Entscheidungstabelle](img/klaerfall-klassifikation.png)

Eingaben: führender Befund, Betrag, Sanktionstreffer (ja/nein), nur automatisch korrigierbare Befunde
(ja/nein). Ausgaben: Kategorie, Priorität, Zielteam, automatische Korrektur erlaubt, Bearbeitungsfrist
(ISO-8601-Dauer). Trefferregel **FIRST**: Die Reihenfolge der Regeln ist die fachliche Priorität.

| # | Wenn | Dann | Begründung |
|---:|---|---|---|
| 1 | Sanktionstreffer | Compliance, KRITISCH, `team-compliance`, 2 h | regulatorisch zwingend manuell |
| 2 | Dublette | Dublette, HOCH, `team-zahlungsverkehr`, 4 h | Geld darf nicht doppelt fließen |
| 3 | Betragslimit und Betrag ≥ 500.000 | Freigabe, HOCH, `team-disposition-leitung`, 4 h | erweitertes Vier-Augen-Prinzip |
| 4 | Betragslimit | Freigabe, MITTEL, `team-disposition`, 8 h | Vier-Augen-Prinzip |
| 5 | nur automatisch korrigierbare Befunde | Formatkorrektur, NIEDRIG, auto = **ja**, 24 h | keine fachliche Entscheidung nötig |
| 6 | Kontodaten (IBAN/BIC) | Kontodaten, MITTEL, `team-zahlungsverkehr`, 8 h | Rückfrage beim Einreicher |
| 7 | Auftragsdaten (Währung, Betrag, Datum, Gebühren) | Auftragsdaten, MITTEL, `team-zahlungsverkehr`, 8 h | Rückfrage beim Einreicher |
| 8 | sonst | Sonstige, NIEDRIG, `team-zahlungsverkehr`, 24 h | Auffangregel |

Die Tabelle ist die **fachliche Hoheit des Fachbereichs**: Limits, Teams und Fristen werden dort
geändert, nicht im Code. Die Offline-Tests (`tests/test_dmn_und_audit.py`) sichern, dass eine Änderung
nicht unbemerkt die Routing-Logik bricht.

## 7. Statusrückmeldung an den Einreicher (`pain.002`)

| Ebene | Status | Bedeutung |
|---|---|---|
| Gruppe (`GrpSts`) | `ACCP` | Alle Zahlungen der Datei angenommen |
| Gruppe | `PART` | Teilweise angenommen, Details je Transaktion |
| Gruppe | `RJCT` | Alle abgelehnt **oder** Datei als Ganzes zurückgewiesen (dann mit `StsRsnInf`) |
| Transaktion (`TxSts`) | `ACCP` | AcceptedCustomerProfile: fachlich geprüft, zur Ausführung angenommen |
| Transaktion | `RJCT` | Abgelehnt, `StsRsnInf/Rsn/Cd` = ISO-Statusgrund, `AddtlInf` = Begründung des Bearbeiters |

Der Report wird vor der Rückgabe gegen das offizielle XSD validiert; eine schemaverletzende
Rückmeldung verlässt das System nicht.

## 8. Audit-Trail und Nachweisführung

Jede fachliche Entscheidung erzeugt einen Eintrag (Ereignis, Prozess, Referenz = EndToEndId/MsgId,
Akteur, Daten, Zeitstempel). Die Einträge sind durch eine SHA-256-Hashkette verbunden, sodass
nachträgliche Änderungen oder Lücken erkennbar sind (`zv audit verify`). Ereignisse:

`NACHRICHT_ANGENOMMEN`, `NACHRICHT_ZURUECKGEWIESEN`, `ZAHLUNG_GEPRUEFT`, `ZAHLUNG_AUTOKORRIGIERT`,
`ZAHLUNG_FREIGEGEBEN`, `ZAHLUNG_ABGELEHNT`, `KLAERFALL_ESKALIERT`, `STATUSREPORT_ERSTELLT`.

Zusammen mit der Camunda-Historie (Operate) ergibt sich eine vollständige, revisionssichere Spur:
*Was* wurde *wann* von *wem* *warum* entschieden.

## 9. Service-Level und Eskalation

Die Bearbeitungsfrist kommt aus der Klassifikation (`PT2H` … `PT24H`) und wird dem User Task als
Fälligkeitsdatum mitgegeben (sichtbar und sortierbar in Tasklist). Läuft die Frist ab, löst ein
nicht unterbrechender Timer die Eskalation aus; der Klärfall selbst bleibt bearbeitbar. Priorität
(90/70/50/30) steuert die Sortierung in Tasklist.

## 10. Offene Punkte für eine produktive Einführung

* Anbindung einer produktiven Sanktionsliste (EU-CFSL, UN, OFAC) über ein Screening-System; Umgang mit
  False Positives (Whitelisting mit Vier-Augen-Prinzip).
* Deckungs- und Limitprüfung gegen das Kontoführungssystem.
* Idempotenz auf Basis der EndToEndId bei Wiederholungen von Jobs (Camunda liefert Jobs nach Timeout
  erneut aus).
* Berechtigungskonzept in Camunda Identity (Gruppen = Kandidatengruppen dieses Konzepts).
* Aufbewahrungsfristen und Archivierung des Audit-Trails (GoBD, 10 Jahre).
* Instant Payments: andere Fristen (10 Sekunden), dann kein manueller Klärfall, sondern direkte
  Ablehnung mit Statusgrund.
