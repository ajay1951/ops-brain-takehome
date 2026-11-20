import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
import networkx as nx
from models import Goal, Task, RunLog

class OpsBrainEngine:
    def __init__(self, goals: List[Goal], tasks: List[Task], today: date):
        self.goals: Dict[str, Goal] = {g.id: g for g in goals}
        self.tasks: Dict[str, Task] = {t.id: t for t in tasks}
        self.today = today
        self.graph = nx.DiGraph()
        self._build_dag()

    def _build_dag(self):
        self.graph.clear()
        for tid in self.tasks:
            self.graph.add_node(tid)
        for task in self.tasks.values():
            for dep_id in task.blocked_by:
                if dep_id in self.tasks:
                    self.graph.add_edge(dep_id, task.id)

        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = list(nx.simple_cycles(self.graph))
            raise ValueError(f"Circular dependency detected: {cycles}. Sync blocked; ops lead alerted.")

    def approve_all_tasks(self, approver: str) -> List[str]:
        """Human approval gate: transitions Proposed tasks to Active before assignments go live."""
        approved = []
        for task in self.tasks.values():
            if task.status == "Proposed":
                task.status = "Active"
                approved.append(task.id)
        return approved

    def compute_priorities(self) -> List[Task]:
        for tid, task in self.tasks.items():
            goal = self.goals[task.goal_id]
            downstream_count = len(list(nx.descendants(self.graph, tid)))
            days_left = (task.due_date - self.today).days

            urgency = max(0.0, min(1.0, (14 - days_left) / 14))
            goal_component = (goal.priority_weight / 3.0) * 40
            urgency_component = urgency * 35
            blocking_component = min(downstream_count * 8, 25)

            task.priority_score = round(goal_component + urgency_component + blocking_component, 1)
            task.priority_reason = (
                f"{goal.id} weight {goal.priority_weight}/3 | "
                f"Due in {days_left}d | "
                f"Blocks {downstream_count} task(s)"
            )

        return sorted(self.tasks.values(), key=lambda t: t.priority_score, reverse=True)

    def check_state_diff(self, state_file: str = "state.json") -> List[str]:
        """Ensures priorities/Notion only update when something real changes."""
        current = {
            tid: {"due": str(t.due_date), "status": t.status, "score": t.priority_score}
            for tid, t in self.tasks.items()
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
        """Generates Slack Block Kit nudges for tasks due <= 48h or escalates after 2 nudges."""
        nudges = []
        for task in self.tasks.values():
            if task.status in ("Done", "Proposed"):
                continue
            days_left = (task.due_date - self.today).days
            if days_left <= 2:
                goal = self.goals[task.goal_id]
                if task.nudge_count >= 2:
                    nudges.append({
                        "type": "ESCALATION",
                        "target": f"@{goal.owner.lower()} (Goal Owner)",
                        "text": f"ESCALATION: '{task.title}' ({task.owner}) missed 2 follow-ups and is due in {days_left}d.",
                    })
                else:
                    task.nudge_count += 1
                    nudges.append({
                        "type": "NUDGE",
                        "target": f"@{task.owner.lower()}",
                        "text": f"Quick check-in on '{task.title}' (due {task.due_date}). Still on track?",
                        "slack_actions": ["On Track", "Slipped (+2d)", "Blocked"],
                    })
        return nudges

    def simulate_slip(self, task_id: str, slip_days: int) -> Tuple[List[dict], Goal]:
        slipped_task = self.tasks[task_id]
        old_date = slipped_task.due_date
        slipped_task.due_date += timedelta(days=slip_days)
        slipped_task.status = "Blocked"

        alerts = []
        downstream_ids = list(nx.descendants(self.graph, task_id))

        for d_id in nx.topological_sort(self.graph):
            if d_id in downstream_ids:
                d_task = self.tasks[d_id]
                upstream_dates = [self.tasks[u].due_date for u in self.graph.predecessors(d_id)]
                if upstream_dates and max(upstream_dates) >= d_task.due_date:
                    new_due = max(upstream_dates) + timedelta(days=3)
                    alerts.append({
                        "channel": f"@{d_task.owner.lower()}",
                        "message": (
                            f"Heads up {d_task.owner}: '{slipped_task.title}' ({slipped_task.owner}) "
                            f"slipped {slip_days}d (new due: {slipped_task.due_date}). "
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
