import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
import networkx as nx
from models import Goal, Task, RunLog

# Map team members to production Slack User IDs for targeted Block Kit DMs
USER_DIRECTORY = {
    "shahaan": "U05QZ7B2K9X",
    "riya": "U03K7L9M2PQ",
    "bhargav": "U04A8B9C1DE",
}

def resolve_slack_target(owner: str) -> str:
    """Resolves owner name to native Slack User ID format <@U...> for real notification delivery."""
    clean = owner.strip().lower()
    user_id = USER_DIRECTORY.get(clean)
    return f"<@{user_id}>" if user_id else f"@{clean}"


class OpsBrainEngine:
    def __init__(self, goals: List[Goal], tasks: List[Task], today: date):
        self.goals: Dict[str, Goal] = {g.id: g for g in goals}
        self.tasks: Dict[str, Task] = {t.id: t for t in tasks}
        self.today = today
        self.graph = nx.DiGraph()
        self.integrity_warnings: List[str] = []
        self._build_dag()

    def _build_dag(self):
        self.graph.clear()
        self.integrity_warnings.clear()

        for tid in self.tasks:
            self.graph.add_node(tid)

        fallback_goal_id = next(iter(self.goals.keys())) if self.goals else "G_DEFAULT"

        for task in self.tasks.values():
            # Validate goal reference exists
            if task.goal_id not in self.goals:
                self.integrity_warnings.append(
                    f"Task '{task.id}' referenced unknown goal '{task.goal_id}'; reassigned to '{fallback_goal_id}'."
                )
                task.goal_id = fallback_goal_id

            # Verify prerequisites exist in task registry
            for dep_id in task.blocked_by:
                if dep_id in self.tasks:
                    self.graph.add_edge(dep_id, task.id)
                else:
                    self.integrity_warnings.append(
                        f"Integrity Warning: Task '{task.id}' depends on missing task '{dep_id}'."
                    )

        # Detect cyclic dependencies before any writes or notifications
        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = list(nx.simple_cycles(self.graph))
            raise ValueError(f"Circular dependency detected: {cycles}. Sync blocked; ops lead alerted.")

    def approve_all_tasks(self, approver: str) -> List[str]:
        """Human approval gate: transitions Proposed tasks to Active and binds the approver identity."""
        approved = []
        for task in self.tasks.values():
            if task.status == "Proposed":
                task.status = "Active"
                task.approved_by = approver
                approved.append(task.id)
        return approved

    def compute_priorities(self) -> List[Task]:
        for tid, task in self.tasks.items():
            goal = self.goals.get(task.goal_id)
            goal_weight = goal.priority_weight if goal else 2
            goal_id_label = goal.id if goal else "Unknown"

            downstream_count = len(list(nx.descendants(self.graph, tid)))
            days_left = (task.due_date - self.today).days

            urgency = max(0.0, min(1.0, (14 - days_left) / 14))
            goal_component = (goal_weight / 3.0) * 40
            urgency_component = urgency * 35
            blocking_component = min(downstream_count * 8, 25)

            task.priority_score = round(goal_component + urgency_component + blocking_component, 1)
            task.priority_reason = (
                f"{goal_id_label} weight {goal_weight}/3 | "
                f"Due in {days_left}d | "
                f"Blocks {downstream_count} task(s)"
            )

        return sorted(self.tasks.values(), key=lambda t: t.priority_score, reverse=True)

    def check_state_diff(self, state_file: str = "state.json") -> List[str]:
        """
        Diffs against ordinal rank position and discrete state (due date, status).
        Ignores daily floating score drift so priorities stay stable day-to-day.
        """
        ranked = self.compute_priorities()
        current = {
            t.id: {
                "due": str(t.due_date),
                "status": t.status,
                "rank_order": idx,
            }
            for idx, t in enumerate(ranked)
        }
        path = Path(state_file)
        if not path.exists():
            path.write_text(json.dumps(current, indent=2), encoding="utf-8")
            return ["Initial snapshot saved."]

        previous = json.loads(path.read_text(encoding="utf-8"))
        diffs = []
        for tid, vals in current.items():
            if tid not in previous or previous[tid] != vals:
                diffs.append(f"{tid}: {previous.get(tid)} -> {vals}")

        if diffs:
            path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        return diffs

    def generate_followups(self) -> List[dict]:
        """Generates Slack Block Kit nudges using verified Slack User IDs."""
        nudges = []
        for task in self.tasks.values():
            if task.status in ("Done", "Proposed"):
                continue
            days_left = (task.due_date - self.today).days
            if days_left <= 2:
                goal = self.goals.get(task.goal_id)
                goal_owner_target = resolve_slack_target(goal.owner) if goal else "@ops-lead"
                task_owner_target = resolve_slack_target(task.owner)

                if task.nudge_count >= 2:
                    nudges.append({
                        "type": "ESCALATION",
                        "target": f"{goal_owner_target} (Goal Owner)",
                        "text": f"ESCALATION: '{task.title}' ({task_owner_target}) missed 2 follow-ups and is due in {days_left}d.",
                    })
                else:
                    task.nudge_count += 1
                    nudges.append({
                        "type": "NUDGE",
                        "target": task_owner_target,
                        "text": f"Quick check-in on '{task.title}' (due {task.due_date}). Still on track?",
                        "slack_actions": ["On Track", "Slipped (+2d)", "Blocked"],
                    })
        return nudges

    def simulate_slip(self, task_id: str, slip_days: int) -> Tuple[List[dict], Goal]:
        """
        Simulates task delay, cascades dates based on actual task duration,
        and updates goal health status.
        """
        slipped_task = self.tasks[task_id]
        old_date = slipped_task.due_date
        slipped_task.due_date += timedelta(days=slip_days)
        slipped_task.status = "Delayed"

        alerts = []
        downstream_ids = list(nx.descendants(self.graph, task_id))

        for d_id in nx.topological_sort(self.graph):
            if d_id in downstream_ids:
                d_task = self.tasks[d_id]
                upstream_dates = [self.tasks[u].due_date for u in self.graph.predecessors(d_id)]
                if upstream_dates and max(upstream_dates) >= d_task.due_date:
                    # New deadline accounts for task's required duration
                    new_due = max(upstream_dates) + timedelta(days=d_task.duration_days)
                    alerts.append({
                        "channel": resolve_slack_target(d_task.owner),
                        "message": (
                            f"Heads up {d_task.owner} ({resolve_slack_target(d_task.owner)}): '{slipped_task.title}' "
                            f"slipped {slip_days}d ({old_date} -> {slipped_task.due_date}). "
                            f"Your task '{d_task.title}' is pushed from {d_task.due_date} -> {new_due}."
                        )
                    })
                    d_task.due_date = new_due

        goal = self.goals[slipped_task.goal_id]
        goal_tasks = [t for t in self.tasks.values() if t.goal_id == goal.id]
        latest_task_date = max(t.due_date for t in goal_tasks)

        if latest_task_date > goal.target_date:
            breach = (latest_task_date - goal.target_date).days
            goal.health_status = "Off Track"
            goal.health_reason = (
                f"'{slipped_task.title}' slipped {slip_days}d ({old_date} -> {slipped_task.due_date}), "
                f"cascading through {len(downstream_ids)} tasks and pushing {goal.id} {breach}d past {goal.target_date}."
            )
        elif (goal.target_date - latest_task_date).days <= 2:
            goal.health_status = "At Risk"
            goal.health_reason = f"Buffer shrunk to <=2 days after '{slipped_task.title}' slipped {slip_days}d."

        return alerts, goal


def log_run(log: RunLog, log_file: str = "logs/runs.jsonl"):
    """Persists operational run telemetry for observability and cost auditing."""
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(log.model_dump_json() + "\n")
