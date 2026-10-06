# Beispiel-Nachrichten (pain.001.001.09)

Fachdatum (Buchungstag) für die Demo: **2026-10-06**. Alle Daten sind fiktiv; IBANs sind mit
echten deutschen Bankleitzahlen generiert, damit Prüfziffern und BIC-Abgleich realistisch sind.

| Datei | Szenario und erwartetes Ergebnis |
|---|---|
| `01_gueltig.xml` | Drei fehlerfreie Zahlungen → alle ACCP, Gruppenstatus ACCP (XSD ✓) |
| `02_gebuehrenregelung.xml` | ChrgBr = DEBT bei SEPA (Rulebook: SLEV) → GEBUEHRENREGELUNG → automatische Korrektur → ACCP (XSD ✓) |
| `03_ausfuehrung_wochenende.xml` | Ausführungsdatum Samstag → AUSFUEHRUNGSDATUM → automatisch auf Montag 2026-10-12 verschoben → ACCP (XSD ✓) |
| `04_betrag_limit.xml` | 75.000 EUR ≥ Limit → BETRAG_LIMIT → Vier-Augen-Freigabe durch Disposition (User Task) → ACCP (XSD ✓) |
| `05_grossbetrag.xml` | 1,25 Mio EUR → BETRAG_LIMIT, Priorität HOCH → Leitung Disposition (User Task) → ACCP (XSD ✓) |
| `06_sanktion.xml` | Kreditorname ähnelt Sanktionsliste → SANKTION → Compliance (User Task) → RJCT RR04 (XSD ✓) |
| `07_iban_pruefziffer.xml` | IBAN-Prüfziffer falsch → IBAN_PRUEFZIFFER → Rückfrage (User Task) → RJCT AC01 (XSD ✓) |
| `08_bic_passt_nicht.xml` | BIC der Commerzbank zu ING-IBAN → BIC_IBAN_MISMATCH → Rückfrage (User Task) → RJCT RC01 (XSD ✓) |
| `09_waehrung_usd.xml` | USD mit Service Level SEPA → WAEHRUNG → Rückfrage (User Task) → RJCT AM11 (XSD ✓) |
| `10_dublette.xml` | Identisch zu Zahlung 1 aus 01_gueltig.xml → DUBLETTE → Zahlungsverkehr (User Task) → RJCT DUPL (XSD ✓) |
| `11_schemafehler.xml` | GrpHdr/MsgId fehlt → XSD-Verletzung → Datei zurückgewiesen, pain.002 GrpSts RJCT FF01 (XSD ✗ (gewollt)) |
| `12_kontrollsumme.xml` | CtrlSum ≠ Summe der Beträge → Datei zurückgewiesen, pain.002 GrpSts RJCT AM10 (XSD ✓) |
| `13_gemischt.xml` | Vier Zahlungen: gültig, Limit (Freigabe), Formatkorrektur, falsche Prüfziffer → Gruppenstatus PART (XSD ✓) |
