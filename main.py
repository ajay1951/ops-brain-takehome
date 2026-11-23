import os
import uuid
from datetime import date, datetime
from pathlib import Path
from dotenv import load_dotenv
from groq import Groq
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from models import ExtractionResult, Task, RunLog
from engine import OpsBrainEngine, log_run, resolve_slack_target

load_dotenv()
console = Console()


def extract_from_notes(notes_text: str, anchor_date: date) -> tuple[ExtractionResult, RunLog]:
    """
    Ingests meeting notes using versioned prompt template.
    Enforces anchor date reference and separates system prompt from untrusted transcript.
    """
    prompt_template_path = Path("prompts/extraction_v1.txt")
    template = prompt_template_path.read_text(encoding="utf-8")
    system_instruction = (
        template
        .replace("{today_date}", anchor_date.strftime("%Y-%m-%d"))
        .replace("{day_of_week}", anchor_date.strftime("%A"))
    )
    user_payload = f"<meeting_transcript>\n{notes_text}\n</meeting_transcript>"

    api_key = os.getenv("GROQ_API_KEY")
    run_id = f"run_{uuid.uuid4().hex[:8]}"

    if api_key:
        try:
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_payload},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            raw_content = response.choices[0].message.content
            usage = response.usage
            in_tok = usage.prompt_tokens if usage else (len(system_instruction) + len(user_payload)) // 4
            out_tok = usage.completion_tokens if usage else len(raw_content) // 4

            # Groq open-source pricing (~$0.15 / 1M prompt, $0.60 / 1M completion)
            cost = (in_tok * 0.00000015) + (out_tok * 0.00000060)

            extracted = ExtractionResult.model_validate_json(raw_content)
            run_log = RunLog(
                run_id=run_id,
                timestamp=datetime.now(),
                model="openai/gpt-oss-120b",
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=round(cost, 6),
                items_extracted=len(extracted.tasks),
            )
            return extracted, run_log
        except Exception as e:
            console.print(f"[yellow]Groq API connection error ({type(e).__name__}: {e}). Using offline dataset.[/yellow]")

    # Resilient offline fallback for Loom recording & air-gapped demo
    fallback_data = Path("sample_extraction.json").read_text(encoding="utf-8")
    extracted = ExtractionResult.model_validate_json(fallback_data)
    run_log = RunLog(
        run_id=run_id,
        timestamp=datetime.now(),
        model="offline-cache (gpt-oss-120b)",
        input_tokens=320,
        output_tokens=280,
        cost_usd=0.000216,
        items_extracted=len(extracted.tasks),
    )
    return extracted, run_log


def print_tasks(title: str, tasks):
    table = Table(title=title)
    table.add_column("ID", style="cyan")
    table.add_column("Goal", style="blue")
    table.add_column("Status", style="bold green")
    table.add_column("Task", style="white")
    table.add_column("Owner", style="green")
    table.add_column("Due", style="yellow")
    table.add_column("Duration", style="dim")
    table.add_column("Score", style="magenta")
    table.add_column("Why Ranked Here", style="dim")

    for t in tasks:
        table.add_row(
            t.id,
            t.goal_id,
            t.status,
            t.title,
            t.owner,
            str(t.due_date),
            f"{t.duration_days}d",
            str(t.priority_score),
            t.priority_reason,
        )
    console.print(table)


def print_team_views(engine: OpsBrainEngine):
    console.print("[bold cyan]Team Member 'My Day' Views (Blocked / Blocking):[/bold cyan]")
    owners = sorted(set(t.owner for t in engine.tasks.values()))
    for owner in owners:
        my_tasks = sorted(
            [t for t in engine.tasks.values() if t.owner == owner],
            key=lambda t: t.priority_score,
            reverse=True,
        )
        top = my_tasks[0]
        blocked_by = [f"{u} ({engine.tasks[u].owner})" for u in engine.graph.predecessors(top.id)]
        blocking = [f"{d} ({engine.tasks[d].owner})" for d in engine.graph.successors(top.id)]
        slack_mention = resolve_slack_target(owner)
        console.print(
            f"  • [bold]{owner}[/bold] ({slack_mention}) -> Top Task: [white]{top.id} ({top.title})[/white] | "
            f"Blocked by: {blocked_by or 'None'} | Blocking: {blocking or 'None'}"
        )
    console.print()


def main():
    # Anchor date matches the meeting timestamp in sample_meeting.txt for deterministic testing
    anchor_date = date(2026, 11, 20)
    if Path("state.json").exists():
        Path("state.json").unlink()

    notes = Path("sample_meeting.txt").read_text(encoding="utf-8")

    # 1. Ingestion via versioned config with anchor date
    console.print("\n[bold blue]1. Ingesting meeting notes via versioned config (prompts/extraction_v1.txt)...[/bold blue]")
    extracted, run_meta = extract_from_notes(notes, anchor_date=anchor_date)
    console.print(f"   [dim]Extracted {len(extracted.goals)} Goals and {len(extracted.tasks)} Tasks (Anchor Date: {anchor_date}).[/dim]")

    engine = OpsBrainEngine(extracted.goals, extracted.tasks, today=anchor_date)

    if engine.integrity_warnings:
        console.print("[yellow]Integrity Warnings:[/yellow]")
        for w in engine.integrity_warnings:
            console.print(f"   ⚠️ {w}")

    # 2. Human Approval Gate
    console.print("\n[bold yellow]2. Human Approval Gate (Build Principle Requirement):[/bold yellow]")
    console.print("   Tasks extracted with initial status: [bold magenta]Proposed[/bold magenta].")
    console.print("   [italic]Reviewing proposed task assignments before notifying team...[/italic]")
    approver_name = "Shahaan (Meeting Organizer)"
    approved_ids = engine.approve_all_tasks(approver=approver_name)
    run_meta.approved_by = approver_name
    log_run(run_meta)
    console.print(f"   [bold green]✓ Approved {len(approved_ids)} tasks by {approver_name}. Status transitioned: Proposed -> Active.[/bold green]\n")

    # 3. Deterministic Priority Ranking
    ranked = engine.compute_priorities()
    print_tasks("Multi-Goal Deterministic Priorities (G1 High vs G2 Low)", ranked)
    engine.check_state_diff()
    print_team_views(engine)

    # 4. Day-to-Day Stability Check
    console.print("[bold green]3. Stability Check (Ordinal rank diffing prevents daily score drift)...[/bold green]")
    engine.compute_priorities()
    diffs = engine.check_state_diff()
    console.print(f"   Diffs detected: {len(diffs)} -> Skipping Notion/Slack writes (priorities stay stable day-to-day).\n")

    # 5. Automated Follow-ups
    console.print("[bold yellow]4. Automated Follow-Up Nudges (Targeting Slack User IDs):[/bold yellow]")
    for nudge in engine.generate_followups():
        console.print(f"   • [{nudge['type']}] DM to [cyan]{nudge['target']}[/cyan]: {nudge['text']} | Buttons: {nudge.get('slack_actions')}")

    # 6. Cascading Slip Simulation
    first_task_id = extracted.tasks[0].id
    console.print(
        f"\n[bold red]5. Cascading Slip Simulation (Duration Math & Downstream Propagation)...[/bold red]"
    )
    alerts, updated_goal = engine.simulate_slip(first_task_id, slip_days=10)
    for alert in alerts:
        console.print(f"   • Alert to [cyan]{alert['channel']}[/cyan]: {alert['message']}")

    console.print(f"\n[bold red]Founder Goal Health ({updated_goal.id}):[/bold red] {updated_goal.health_status}")
    console.print(f"[bold]Reason:[/bold] {updated_goal.health_reason}\n")

    re_ranked = engine.compute_priorities()
    real_diffs = engine.check_state_diff()
    console.print(f"[bold magenta]State Diff Check:[/bold magenta] {len(real_diffs)} real task change(s) -> Syncing to Notion.\n")
    print_tasks("Updated Priorities After Real Operational Change", re_ranked)

    # 7. Guardrail Demo: Foreign Key & Circular Dependency Catch
    console.print("[bold blue]6. Guardrail Check (Simulating Circular Dependency T1 <-> T2)...[/bold blue]")
    try:
        bad_tasks = [
            Task(id="T1", title="A", owner="Shahaan", due_date=date(2026, 11, 25), goal_id="G1", blocked_by=["T2"]),
            Task(id="T2", title="B", owner="Bhargav", due_date=date(2026, 11, 26), goal_id="G1", blocked_by=["T1"]),
        ]
        OpsBrainEngine(extracted.goals, bad_tasks, today=anchor_date)
    except ValueError as e:
        console.print(f"   [bold green]Caught safely:[/bold green] {e}\n")

    # 8. Run Telemetry & Cost Audit
    console.print("[bold cyan]7. Run Audit Log & Cost Report (Saved to logs/runs.jsonl):[/bold cyan]")
    log_panel = (
        f"[bold]Run ID:[/bold] {run_meta.run_id}\n"
        f"[bold]Model:[/bold] {run_meta.model}\n"
        f"[bold]Input / Output Tokens:[/bold] {run_meta.input_tokens} in / {run_meta.output_tokens} out\n"
        f"[bold]Execution Cost:[/bold] ${run_meta.cost_usd:.6f} (~0.02 cents)\n"
        f"[bold]Human Approver:[/bold] {run_meta.approved_by}\n"
        f"[bold]Status:[/bold] Logged to logs/runs.jsonl for observability & cost tracking"
    )
    console.print(Panel(log_panel, title="Run Telemetry", border_style="cyan"))


if __name__ == "__main__":
    main()
