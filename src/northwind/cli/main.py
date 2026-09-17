from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from northwind.db import session_scope
from northwind.models.ingestion import IngestionRun, SOURCE_SYSTEMS

app = typer.Typer(help="Northwind identity governance operator CLI")
ingest_app = typer.Typer(help="Run or inspect source ingestion")
app.add_typer(ingest_app, name="ingest")

console = Console()


@app.command()
def health():
    """Print dependency and source-freshness status. Mirrors GET /health."""
    with session_scope() as session:
        table = Table(title="Northwind health")
        table.add_column("Source")
        table.add_column("Last status")
        table.add_column("Last completed")
        for src in SOURCE_SYSTEMS:
            run = session.execute(
                select(IngestionRun)
                .where(IngestionRun.source_system == src)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            table.add_row(src, run.status if run else "never run", str(run.completed_at) if run else "-")
        console.print(table)


@ingest_app.command("all")
def ingest_all(input_dir: str = typer.Option("seed/baseline", "--input-dir")):
    """Ingest all six sources from INPUT_DIR, in dependency order (Zoho first, since every
    other source correlates against the identities it creates)."""
    from pathlib import Path as _Path

    from northwind.ingestion.ad import ingest_ad
    from northwind.ingestion.aws import ingest_aws
    from northwind.ingestion.entra import ingest_entra
    from northwind.ingestion.plantops import ingest_plantops
    from northwind.ingestion.salesforce import ingest_salesforce
    from northwind.ingestion.zoho import ingest_zoho

    base = _Path(input_dir)
    jobs = [
        ("Zoho People", ingest_zoho, base / "zoho_people_workers.csv"),
        ("Entra ID", ingest_entra, base / "entra_users.json"),
        ("Active Directory", ingest_ad, base / "ad_accounts.csv"),
        ("Salesforce", ingest_salesforce, base / "salesforce_users.csv"),
        ("AWS IAM", ingest_aws, base / "aws_iam.json"),
        ("PlantOps", ingest_plantops, base / "plantops_access_202609.xlsx"),
    ]
    for label, fn, path in jobs:
        if not path.exists():
            console.print(f"[red]Missing input for {label}: {path}[/red]")
            raise typer.Exit(code=1)
        with session_scope() as session:
            run = fn(session, path)
            color = "green" if run.status == "succeeded" else "red"
            console.print(
                f"[{color}]{label:20}[/{color}] rows_in={run.rows_in:5} "
                f"normalized={run.rows_normalized:5} quarantined={run.rows_quarantined:5} "
                f"status={run.status}"
            )
            if run.status != "succeeded":
                console.print(f"  [red]error: {run.error}[/red]")


@app.command()
def reconcile(run: str = typer.Argument("latest")):
    """Show the reconciliation report: rows in/normalized/quarantined and reason-code
    breakdown for the latest ingestion run of every source (or a specific run id)."""
    from sqlalchemy import select

    from northwind.models.ingestion import ReconciliationMetric

    with session_scope() as session:
        sources = SOURCE_SYSTEMS if run == "latest" else None
        runs = []
        if sources:
            for src in sources:
                r = session.execute(
                    select(IngestionRun).where(IngestionRun.source_system == src)
                    .order_by(IngestionRun.started_at.desc()).limit(1)
                ).scalar_one_or_none()
                if r:
                    runs.append(r)
        else:
            r = session.get(IngestionRun, run)
            if r:
                runs.append(r)

        if not runs:
            console.print("[yellow]No ingestion runs found.[/yellow]")
            return

        for r in runs:
            table = Table(title=f"{r.source_system} -- run {r.id[:8]} ({r.status})")
            table.add_column("Stage")
            table.add_column("Reason code")
            table.add_column("Rows", justify="right")
            metrics = session.execute(
                select(ReconciliationMetric).where(ReconciliationMetric.run_id == r.id)
            ).scalars().all()
            for m in sorted(metrics, key=lambda m: (m.stage, -m.row_count)):
                table.add_row(m.stage, m.reason_code, str(m.row_count))
            console.print(table)
            invariant_ok = r.rows_in == r.rows_normalized + r.rows_quarantined
            mark = "[green]OK[/green]" if invariant_ok else "[red]BROKEN[/red]"
            console.print(
                f"  rows_in={r.rows_in}  normalized={r.rows_normalized}  "
                f"quarantined={r.rows_quarantined}  invariant={mark}\n"
            )


@app.command(name="why-not")
def why_not(identity: str = typer.Argument(...), campaign: str = typer.Argument(...)):
    """Explain in plain English why IDENTITY did or did not land in CAMPAIGN."""
    from northwind.access_graph.triage import why_not_in_campaign
    with session_scope() as session:
        console.print(why_not_in_campaign(session, identity, campaign))


@app.command(name="why")
def why_cmd(identity: str = typer.Argument(...), entitlement: str = typer.Argument(...)):
    """Explain why IDENTITY has (or doesn't have) ENTITLEMENT, returning the access path."""
    from northwind.access_graph.triage import why_has_entitlement
    with session_scope() as session:
        console.print(why_has_entitlement(session, identity, entitlement))


@app.command(name="diff")
def diff_cmd(run_a: str = typer.Argument(...), run_b: str = typer.Argument(...)):
    """Show what changed between two ingestion runs and what that did to reconciliation counts."""
    from northwind.access_graph.triage import diff_runs
    with session_scope() as session:
        console.print(diff_runs(session, run_a, run_b))


@app.command()
def access(identity_name: str = typer.Argument(...)):
    """Show everything IDENTITY_NAME can do, and how they got it (direct + inherited)."""
    from northwind.access_graph.effective_access import effective_access_for_identity, find_identity_by_name

    with session_scope() as session:
        identity = find_identity_by_name(session, identity_name)
        if not identity:
            console.print(f"[red]No identity matching '{identity_name}'[/red]")
            raise typer.Exit(code=1)

        console.print(f"[bold]{identity.name}[/bold] ({identity.title}, {identity.department}, "
                       f"{identity.lifecycle_status})")
        grants = effective_access_for_identity(session, identity.id)
        if not grants:
            console.print("  No access found.")
            return

        table = Table()
        table.add_column("Application")
        table.add_column("Entitlement")
        table.add_column("Type")
        table.add_column("Privileged")
        table.add_column("How")
        for g in grants:
            how = "direct" if g.grant_type == "direct" else " -> ".join(g.path)
            table.add_row(g.application_name, g.entitlement_name, g.entitlement_type,
                          "yes" if g.privileged else "", how)
        console.print(table)


@app.command()
def jml(action: str = typer.Argument(..., help="joiner|mover|leaver"),
        identity_name: str = typer.Argument(...),
        trigger: str = typer.Option(..., "--trigger"),
        mode: str = typer.Option("execute", "--mode", help="execute|dry_run")):
    """Trigger a JML workflow run for IDENTITY_NAME."""
    from northwind.access_graph.effective_access import find_identity_by_name
    from northwind.lifecycle.joiner import run_joiner
    from northwind.lifecycle.leaver import run_leaver
    from northwind.lifecycle.mover import run_mover

    fn = {"joiner": run_joiner, "mover": run_mover, "leaver": run_leaver}.get(action)
    if fn is None:
        console.print(f"[red]Unknown action '{action}' -- use joiner, mover, or leaver[/red]")
        raise typer.Exit(code=1)

    with session_scope() as session:
        identity = find_identity_by_name(session, identity_name)
        if not identity:
            console.print(f"[red]No identity matching '{identity_name}'[/red]")
            raise typer.Exit(code=1)
        run = fn(session, identity.id, trigger, mode=mode)
        console.print(f"[bold]{action}[/bold] for {identity.name}: run={run.id[:8]} status={run.status} mode={mode}")
        for step in run.steps:
            console.print(f"  {step.step_name:40} {step.status:10} {step.response or step.request or ''}")


@app.command(name="campaign")
def campaign_cmd(action: str = typer.Argument(..., help="create|progress|evidence|close"),
                  name_or_id: str = typer.Argument(...),
                  application: str = typer.Option(None, "--application")):
    """Manage access review campaigns. 'create NAME --application Salesforce', or
    'progress|evidence|close CAMPAIGN_ID'."""
    from northwind.campaigns.service import campaign_progress, create_campaign, evidence_pack, execute_pending_revocations

    with session_scope() as session:
        if action == "create":
            c = create_campaign(session, name_or_id, application)
            console.print(f"Created campaign {c.id} scoped to {application} "
                          f"(stale source: {c.launched_with_stale_source})")
        elif action == "progress":
            p = campaign_progress(session, name_or_id)
            table = Table(title=f"Campaign {name_or_id[:8]} progress")
            for k, v in p.items():
                table.add_row(k, str(v))
            console.print(table)
        elif action == "evidence":
            rows = evidence_pack(session, name_or_id)
            table = Table(title="Evidence pack")
            table.add_column("Identity")
            table.add_column("Entitlement")
            table.add_column("Reviewer")
            table.add_column("Decision")
            table.add_column("Revocation")
            for r in rows[:30]:
                table.add_row(r["identity"], r["entitlement"], r["reviewer"], r["decision"], r["revocation_executed"])
            console.print(table)
            console.print(f"[dim]{len(rows)} total review items (showing 30)[/dim]")
        elif action == "close":
            n = execute_pending_revocations(session, name_or_id)
            console.print(f"Executed {n} pending revocations for campaign {name_or_id[:8]}")
        else:
            console.print(f"[red]Unknown action '{action}'[/red]")


@app.command()
def findings(rule: str = typer.Option(None, "--rule"), top: int = typer.Option(20, "--top")):
    """Run the A3 findings catalog (or show existing findings, filtered by --rule)."""
    from sqlalchemy import select

    from northwind.findings.rules import run_all_findings
    from northwind.models.governance import Finding

    with session_scope() as session:
        if rule is None:
            results = run_all_findings(session)
            table = Table(title="Findings run")
            table.add_column("Rule")
            table.add_column("Count", justify="right")
            for rule_name, count in results.items():
                table.add_row(rule_name, str(count))
            console.print(table)

        query = select(Finding).where(Finding.status == "open").order_by(Finding.risk_score.desc()).limit(top)
        if rule:
            query = select(Finding).where(Finding.rule_code == rule, Finding.status == "open") \
                .order_by(Finding.risk_score.desc()).limit(top)
        rows = session.execute(query).scalars().all()
        table = Table(title=f"Top {len(rows)} open findings")
        table.add_column("Score", justify="right")
        table.add_column("Rule")
        table.add_column("Severity")
        table.add_column("Explanation")
        for f in rows:
            table.add_row(str(f.risk_score), f.rule_code, f.severity, f.explanation[:90])
        console.print(table)


if __name__ == "__main__":
    app()
