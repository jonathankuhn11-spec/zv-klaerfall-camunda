# ADR-0004: Audit-Trail als append-only JSONL mit SHA-256-Hashkette

**Status:** akzeptiert · **Datum:** 2026-10-05

## Kontext

Revision und Aufsicht erwarten, dass fachliche Entscheidungen vollständig, unveränderbar und
nachvollziehbar dokumentiert sind. Camunda Operate hält die Prozesshistorie, aber nicht jede fachliche
Nuance (welche Befunde, welche Korrektur, welche Begründung) in auswertbarer Form.

## Entscheidung

Eigener Audit-Trail: eine Zeile pro Ereignis, jede Zeile mit dem Hash der Vorgängerzeile. Verifikation
per Kommando (`zv audit verify`). Ereignisse werden an den fachlichen Stellen (Handler) geschrieben,
nicht als Camunda-Listener.

## Konsequenzen

* Manipulation oder Löschung einzelner Einträge ist erkennbar; vollständiger Austausch der Datei
  nicht – dafür braucht es produktiv WORM-Speicher oder externe Verankerung (z. B. täglicher
  Hash-Anker in einem separaten System).
* Reset der Demo rotiert die Datei, statt sie zu löschen.
* Der Audit-Trail ist bewusst lokal und ohne Datenbankabhängigkeit, damit die Offline-Demo ohne
  Infrastruktur läuft.
