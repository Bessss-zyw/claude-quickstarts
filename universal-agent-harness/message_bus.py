"""File-based inter-agent message bus."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


class MessageBus:
    """Each message is a standalone JSON file under .harness/messages/."""

    def __init__(self, messages_dir: str) -> None:
        self._dir = messages_dir
        os.makedirs(self._dir, exist_ok=True)

    # ── Write side ─────────────────────────────────────────────────────

    def create_task(self, from_agent: str, to_agent: str, content: str) -> str:
        """Create a pending task_assignment message. Returns the task id."""
        next_num = len(self._list_files()) + 1
        task_id = f"msg_{next_num:03d}"
        msg = {
            "id": task_id,
            "from": from_agent,
            "to": to_agent,
            "type": "task_assignment",
            "content": content,
            "status": "pending",
            "result": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        path = os.path.join(self._dir, f"{task_id}.json")
        with open(path, "w") as f:
            json.dump(msg, f, indent=2)
        return task_id

    def complete_task(self, task_id: str, result: str) -> None:
        """Mark a task as done and store the result."""
        msg = self.get_task(task_id)
        msg["status"] = "done"
        msg["result"] = result
        msg["completed_at"] = datetime.now(timezone.utc).isoformat()
        path = os.path.join(self._dir, f"{task_id}.json")
        with open(path, "w") as f:
            json.dump(msg, f, indent=2)

    # ── Read side ──────────────────────────────────────────────────────

    def get_pending_tasks(self) -> list[dict]:
        """Return all pending messages sorted by id."""
        return [m for m in self.get_all_tasks() if m["status"] == "pending"]

    def get_task(self, task_id: str) -> dict:
        """Load a single message by id."""
        path = os.path.join(self._dir, f"{task_id}.json")
        with open(path) as f:
            return json.load(f)

    def get_all_tasks(self) -> list[dict]:
        """Return every message sorted by id."""
        msgs: list[dict] = []
        for fname in self._list_files():
            with open(os.path.join(self._dir, fname)) as f:
                msgs.append(json.load(f))
        msgs.sort(key=lambda m: m["id"])
        return msgs

    # ── Helpers ────────────────────────────────────────────────────────

    def _list_files(self) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        return [f for f in os.listdir(self._dir) if f.startswith("msg_") and f.endswith(".json")]
