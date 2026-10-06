"""Kommandozeile ``zv`` – Demo, Deployment, Worker, Klärfallbearbeitung, Audit.

Zwei Betriebsarten:

* ``zv demo --offline``  → kompletter Durchlauf aller Beispieldateien ohne Camunda (10 Sekunden).
* ``zv deploy`` / ``zv ingest`` / ``zv worker`` / ``zv tasks``  → gegen einen Camunda-8-Cluster
  (``docker compose up -d``, Operate/Tasklist unter http://localhost:8080).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from zv_klaerfall.camunda.client import CamundaClient
from zv_klaerfall.config import Dienste, Einstellungen
from zv_klaerfall.iso20022 import pain001
from zv_klaerfall.models import betrag_de
from zv_klaerfall.rules import validation

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="Zahlungsverkehrs-Klärfallbearbeitung mit Camunda 8 (ISO 20022 pain.001 → pain.002).",
)
tasks_app = typer.Typer(no_args_is_help=True, help="Offene Klärfälle (User Tasks) anzeigen und bearbeiten.")
audit_app = typer.Typer(no_args_is_help=True, help="Revisionssicheren Audit-Trail lesen und verifizieren.")
app.add_typer(tasks_app, name="tasks")
app.add_typer(audit_app, name="audit")
console = Console()

PROZESS_NACHRICHT = "zv-nachricht-verarbeiten"
FachdatumOpt = Annotated[
    str | None,
    typer.Option(
        "--fachdatum",
        help="Buchungstag (JJJJ-MM-TT), Standard: heute bzw. samples/pain.001/fachdatum.txt bei der Demo",
    ),
]


def _einstellungen() -> Einstellungen:
    return Einstellungen()


def _client(e: Einstellungen) -> CamundaClient:
    c = CamundaClient(e.camunda_url, user=e.camunda_user, password=e.camunda_password)
    if not c.erreichbar():
        console.print(
            f"[red]Camunda unter {e.camunda_url} nicht erreichbar.[/red] Starten mit: docker compose up -d"
        )
        raise typer.Exit(2)
    return c


def _fachdatum(wert: str | None, *, standard_aus_samples: bool = False) -> date:
    if wert:
        return date.fromisoformat(wert)
    if standard_aus_samples:
        datei = _einstellungen().projektwurzel / "samples" / "pain.001" / "fachdatum.txt"
        if datei.exists():
            return date.fromisoformat(datei.read_text().strip())
    return date.today()


def _sample_dateien(pfade: list[Path] | None) -> list[Path]:
    if pfade:
        return [p for p in pfade if p.is_file()]
    return sorted((_einstellungen().projektwurzel / "samples" / "pain.001").glob("*.xml"))


# ----------------------------------------------------------------------------------------------
@app.command()
def validate(
    dateien: Annotated[list[Path], typer.Argument(help="pain.001-Dateien", exists=True)],
    fachdatum: FachdatumOpt = None,
) -> None:
    """Prüft pain.001-Dateien (XSD + fachliche Regeln) und zeigt alle Befunde, ohne Prozess."""
    heute = _fachdatum(fachdatum, standard_aus_samples=True)
    for pfad in dateien:
        try:
            nachricht = pain001.parse_datei(pfad)
        except pain001.SchemaFehler as exc:
            console.print(Panel("\n".join(exc.fehler[:10]), title=f"[red]{pfad.name}: XSD-Verletzung[/red]"))
            continue
        tabelle = Table(
            title=f"{pfad.name} · MsgId {nachricht.kopf.msg_id} · {len(nachricht.zahlungen)} Zahlung(en)"
        )
        tabelle.add_column("EndToEndId")
        tabelle.add_column("Betrag", justify="right")
        tabelle.add_column("Kreditor")
        tabelle.add_column("Befunde")
        for z in nachricht.zahlungen:
            erg = validation.pruefe(z, validation.Pruefkontext(heute=heute))
            befunde = (
                "\n".join(
                    f"[{'red' if b.schwere == 'BLOCKIEREND' else 'yellow'}]{b.code}[/] {b.meldung}"
                    for b in erg.befunde
                )
                or "[green]ohne Befund[/green]"
            )
            tabelle.add_row(z.end_to_end_id, betrag_de(z.betrag, z.waehrung), z.kreditor.name or "", befunde)
        console.print(tabelle)


# ----------------------------------------------------------------------------------------------
@app.command()
def demo(
    dateien: Annotated[
        list[Path] | None, typer.Argument(help="pain.001-Dateien (Standard: samples/pain.001/*.xml)")
    ] = None,
    offline: Annotated[
        bool, typer.Option("--offline", help="Ohne Camunda: Prozess wird in Python nachgebildet")
    ] = False,
    interaktiv: Annotated[
        bool, typer.Option("--interaktiv", help="Klärfälle im Terminal selbst entscheiden")
    ] = False,
    sla: Annotated[
        bool, typer.Option("--sla-ueberschritten", help="SLA-Eskalation für jeden Klärfall simulieren")
    ] = False,
    reset: Annotated[
        bool, typer.Option("--reset/--no-reset", help="Laufzeitdaten (Dubletten, Journal) vorher leeren")
    ] = True,
    fachdatum: FachdatumOpt = None,
) -> None:
    """Verarbeitet die Beispieldateien end-to-end und zeigt Ergebnis je Zahlung."""
    if not offline:
        console.print(
            "Online-Demo = deploy + ingest + worker. Nutze [bold]zv deploy && zv ingest && zv worker[/bold] "
            "oder [bold]--offline[/bold] für den Durchlauf ohne Cluster."
        )
        raise typer.Exit(1)
    from zv_klaerfall.pipeline.offline import Entscheidung, OfflinePipeline, standard_entscheider

    e = _einstellungen()
    dienste = Dienste(e)
    if reset:
        dienste.zuruecksetzen()
    heute = _fachdatum(fachdatum, standard_aus_samples=True)

    def entscheider(k, pruefung, v):  # type: ignore[no-untyped-def]
        if not interaktiv:
            return standard_entscheider(k, pruefung, v)
        console.print(
            Panel(
                v["befundeText"],
                title=f"Klärfall {k.kategorie} / {k.prioritaet} → {k.zielTeam}",
                subtitle=v["zahlungText"],
            )
        )
        wahl = typer.prompt("Entscheidung [F]reigeben / [A]blehnen", default="A").strip().upper()
        kommentar = typer.prompt("Kommentar", default="")
        return Entscheidung("FREIGEBEN" if wahl.startswith("F") else "ABLEHNEN", "cli", kommentar or None)

    pipeline = OfflinePipeline(
        dienste,
        e.bpmn_verzeichnis / "klaerfall-klassifikation.dmn",
        entscheider=entscheider,
        heute=heute,
        sla_simulieren=sla,
    )
    tabelle = Table(title=f"Offline-Durchlauf · Fachdatum {heute.isoformat()}", show_lines=True)
    for spalte in ("Datei", "Zahlung", "Befunde", "Klärfall → Team", "Ergebnis"):
        tabelle.add_column(spalte)
    gesamt = {"ACCP": 0, "RJCT": 0, "zurueckgewiesen": 0}
    for pfad in _sample_dateien(dateien):
        lauf = pipeline.verarbeite_datei(pfad)
        if not lauf.nachricht_gueltig:
            gesamt["zurueckgewiesen"] += 1
            tabelle.add_row(
                pfad.name,
                "[dim]gesamte Datei[/dim]",
                "\n".join(lauf.fehler[:3]),
                "",
                f"[red]zurückgewiesen (pain.002 {lauf.gruppenstatus})[/red]",
            )
            continue
        for z in lauf.zahlungen:
            gesamt[z.status] += 1
            farbe = "green" if z.status == "ACCP" else "red"
            extra = f"\n[dim]korrigiert: {'; '.join(z.korrekturen)}[/dim]" if z.korrekturen else ""
            esk = " [yellow]⚠ eskaliert[/yellow]" if z.eskaliert else ""
            tabelle.add_row(
                pfad.name,
                z.end_to_end_id,
                ", ".join(z.befunde) or "[green]keine[/green]",
                f"{z.kategorie or '–'} → {z.ziel_team or '–'}{esk}",
                f"[{farbe}]{z.status}[/{farbe}]{extra}",
            )
        tabelle.add_row(
            "[dim]↳ pain.002 Statusreport[/dim]", "", "", "", f"[bold]{lauf.gruppenstatus}[/bold]"
        )
    console.print(tabelle)
    bericht = dienste.audit.verifiziere()
    console.print(
        f"Angenommen: [green]{gesamt['ACCP']}[/green] · Abgelehnt: [red]{gesamt['RJCT']}[/red] · "
        f"Dateien zurückgewiesen: {gesamt['zurueckgewiesen']} · Audit-Trail: {bericht.eintraege} Einträge, "
        f"Hashkette {'[green]intakt[/green]' if bericht.gueltig else '[red]GEBROCHEN[/red]'}"
    )
    console.print(
        f"Ausgaben unter [bold]{e.datenverzeichnis}[/bold] "
        "(statusreports/, buchungen.jsonl, audit-trail.jsonl)"
    )


# ----------------------------------------------------------------------------------------------
@app.command()
def deploy() -> None:
    """Deployt BPMN, DMN und Formular in den Camunda-Cluster."""
    e = _einstellungen()
    dateien = (
        sorted(e.bpmn_verzeichnis.glob("*.bpmn"))
        + sorted(e.bpmn_verzeichnis.glob("*.dmn"))
        + sorted(e.forms_verzeichnis.glob("*.form"))
    )
    with _client(e) as c:
        ergebnis = c.deploye(dateien)
    tabelle = Table(title=f"Deployment {ergebnis.get('deploymentKey')}")
    tabelle.add_column("Typ")
    tabelle.add_column("ID")
    tabelle.add_column("Version")
    tabelle.add_column("Key")
    for d in ergebnis.get("deployments", []):
        for typ, inhalt in d.items():
            if isinstance(inhalt, dict):
                tabelle.add_row(
                    typ,
                    str(
                        inhalt.get("processDefinitionId")
                        or inhalt.get("decisionDefinitionId")
                        or inhalt.get("decisionRequirementsId")
                        or inhalt.get("formId")
                    ),
                    str(inhalt.get("version") or inhalt.get("processDefinitionVersion") or ""),
                    str(
                        inhalt.get("processDefinitionKey")
                        or inhalt.get("decisionDefinitionKey")
                        or inhalt.get("decisionRequirementsKey")
                        or inhalt.get("formKey")
                    ),
                )
    console.print(tabelle)


@app.command()
def ingest(
    dateien: Annotated[
        list[Path] | None, typer.Argument(help="pain.001-Dateien (Standard: samples/pain.001/*.xml)")
    ] = None,
) -> None:
    """Startet je Datei eine Instanz von zv-nachricht-verarbeiten (wie ein EBICS-/SFTP-Eingang)."""
    e = _einstellungen()
    with _client(e) as c:
        for pfad in _sample_dateien(dateien):
            inst = c.starte_prozess(
                PROZESS_NACHRICHT, {"nachrichtXml": pfad.read_text(encoding="utf-8"), "dateiname": pfad.name}
            )
            console.print(f"[green]✓[/green] {pfad.name} → Instanz {inst['processInstanceKey']}")


@app.command()
def worker(
    dauer: Annotated[
        float | None, typer.Option("--dauer", help="Sekunden, danach beenden (Standard: bis Strg+C)")
    ] = None,
    fachdatum: FachdatumOpt = None,
) -> None:
    """Startet die Job-Worker für alle zv.*-Service-Tasks."""
    from zv_klaerfall.camunda.worker import JobWorker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    e = _einstellungen()
    heute = _fachdatum(fachdatum, standard_aus_samples=True)
    with _client(e) as c:
        w = JobWorker(c, Dienste(e), worker_name=e.worker_name, heute=heute)
        console.print(f"Worker läuft gegen {e.camunda_url} (Fachdatum {heute}). Strg+C zum Beenden.")
        w.laufe(dauer)
        console.print(
            f"Verarbeitet: {w.statistik.verarbeitet}, fehlgeschlagen: {w.statistik.fehlgeschlagen}, "
            f"je Typ: {w.statistik.je_typ}"
        )


# ----------------------------------------------------------------------------------------------
@tasks_app.command("list")
def tasks_list(
    gruppe: Annotated[
        str | None, typer.Option("--gruppe", help="Nur Kandidatengruppe, z. B. team-compliance")
    ] = None,
) -> None:
    """Zeigt offene Klärfälle mit Befunden."""
    e = _einstellungen()
    with _client(e) as c:
        tasks = c.suche_user_tasks(kandidatengruppe=gruppe)
        tabelle = Table(title=f"Offene Klärfälle ({len(tasks)})", show_lines=True)
        for spalte in ("Task-Key", "Team", "Priorität", "Fällig", "Zahlung", "Befunde"):
            tabelle.add_column(spalte)
        for t in tasks:
            v = c.user_task_variablen(t["userTaskKey"])
            tabelle.add_row(
                str(t["userTaskKey"]),
                ", ".join(t.get("candidateGroups") or []),
                str(t.get("priority", "")),
                str(t.get("dueDate") or "")[:16],
                str(v.get("zahlungText", ""))[:120],
                str(v.get("befundeText", "")),
            )
    console.print(tabelle)


@tasks_app.command("auto")
def tasks_auto(
    gruppe: Annotated[str | None, typer.Option("--gruppe", help="Nur Kandidatengruppe")] = None,
) -> None:
    """Entscheidet alle offenen Klärfälle mit der Demo-Policy (Freigabe bei Limit, sonst Ablehnung)."""
    from zv_klaerfall.models import Klassifikation, Pruefergebnis
    from zv_klaerfall.pipeline.offline import standard_entscheider

    e = _einstellungen()
    with _client(e) as c:
        tasks = c.suche_user_tasks(kandidatengruppe=gruppe)
        for t in tasks:
            v = c.user_task_variablen(t["userTaskKey"])
            try:
                k = Klassifikation.model_validate(v["klassifikation"])
                pruefung = Pruefergebnis.model_validate(v["pruefung"])
            except (KeyError, ValueError) as exc:
                console.print(f"[yellow]Task {t['userTaskKey']} übersprungen: {exc}[/yellow]")
                continue
            ent = standard_entscheider(k, pruefung, v)
            c.user_task_zuweisen(t["userTaskKey"], ent.bearbeiter)
            c.user_task_abschliessen(
                t["userTaskKey"],
                {"entscheidung": ent.entscheidung, "bearbeiter": ent.bearbeiter, "kommentar": ent.kommentar},
            )
            console.print(
                f"[green]✓[/green] {t['userTaskKey']} ({k.kategorie} → {k.zielTeam}): {ent.entscheidung}"
            )
        if not tasks:
            console.print("Keine offenen Klärfälle.")


@tasks_app.command("complete")
def tasks_complete(
    task_key: Annotated[str, typer.Argument(help="userTaskKey aus 'zv tasks list'")],
    entscheidung: Annotated[str, typer.Option("--entscheidung", help="FREIGEBEN oder ABLEHNEN")],
    bearbeiter: Annotated[str, typer.Option("--bearbeiter", help="Kürzel des Bearbeiters")],
    kommentar: Annotated[
        str, typer.Option("--kommentar", help="Begründung (Pflicht, landet im Audit-Trail)")
    ],
) -> None:
    """Schließt einen Klärfall ab (Alternative zur Tasklist-Oberfläche)."""
    entscheidung = entscheidung.upper()
    if entscheidung not in ("FREIGEBEN", "ABLEHNEN"):
        raise typer.BadParameter("FREIGEBEN oder ABLEHNEN")
    e = _einstellungen()
    with _client(e) as c:
        c.user_task_zuweisen(task_key, bearbeiter)
        c.user_task_abschliessen(
            task_key, {"entscheidung": entscheidung, "bearbeiter": bearbeiter, "kommentar": kommentar}
        )
    console.print(f"[green]✓[/green] Task {task_key}: {entscheidung} durch {bearbeiter}")


# ----------------------------------------------------------------------------------------------
@audit_app.command("verify")
def audit_verify() -> None:
    """Prüft die SHA-256-Hashkette des Audit-Trails."""
    d = Dienste(_einstellungen())
    b = d.audit.verifiziere()
    if b.gueltig:
        console.print(f"[green]✓ Audit-Trail intakt[/green] ({b.eintraege} Einträge, {d.audit.pfad})")
    else:
        console.print(f"[red]✗ Audit-Trail manipuliert[/red] ({b.eintraege} Einträge):")
        for f in b.fehler:
            console.print(f"  - {f}")
        raise typer.Exit(1)


@audit_app.command("show")
def audit_show(
    referenz: Annotated[
        str | None, typer.Option("--referenz", help="Nur Einträge zu dieser EndToEndId/MsgId")
    ] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """Listet Audit-Einträge (neueste zuletzt)."""
    d = Dienste(_einstellungen())
    eintraege = [z for z in d.audit.lese() if not referenz or z["referenz"] == referenz][-limit:]
    tabelle = Table(title=f"Audit-Trail ({len(eintraege)} Einträge)")
    for spalte in ("Zeit", "Ereignis", "Referenz", "Akteur", "Daten"):
        tabelle.add_column(spalte)
    for z in eintraege:
        tabelle.add_row(
            datetime.fromisoformat(z["ts"]).astimezone().strftime("%d.%m. %H:%M:%S"),
            z["ereignis"],
            z["referenz"],
            z["akteur"],
            json.dumps(z["daten"], ensure_ascii=False)[:100],
        )
    console.print(tabelle)


@app.command()
def status() -> None:
    """Zeigt Cluster-Topologie und laufende/abgeschlossene Instanzen."""
    e = _einstellungen()
    with _client(e) as c:
        topo = c.topologie()
        console.print(
            f"Camunda {topo.get('gatewayVersion')} · Cluster {topo.get('clusterSize')} Broker, "
            f"{topo.get('partitionsCount')} Partition(en)"
        )
        time.sleep(0.2)
        for prozess in (PROZESS_NACHRICHT, "zv-zahlung-pruefen"):
            aktiv = c.suche_prozessinstanzen(prozess, aktiv=True)
            fertig = c.suche_prozessinstanzen(prozess, aktiv=False)
            console.print(f"  {prozess}: {len(aktiv)} aktiv, {len(fertig)} abgeschlossen")


if __name__ == "__main__":
    app()
