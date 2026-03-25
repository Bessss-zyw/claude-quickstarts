"""Manage a Claude Code process running in a tmux window."""

from __future__ import annotations

import logging
import shlex
import subprocess
import time

from config import AgentDef, HarnessConfig
from state_detector import PaneState, detect_state

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT = 60  # seconds to wait for CC to be ready


class CCInstance:
    """Lifecycle manager for one Claude Code process in a tmux window."""

    def __init__(self, agent: AgentDef, tmux_session: str, config: HarnessConfig) -> None:
        self.agent = agent
        self.tmux_session = tmux_session
        self.window_name = agent.name
        self.target = f"{tmux_session}:{agent.name}"
        self.config = config
        self._started = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch Claude Code in a new tmux window and wait for it to be ready."""
        project_dir = self.agent.project_dir

        # Build claude command
        cc_cmd = "claude"
        if self.agent.allowlist:
            cc_cmd += f" --allowedTools '{self.agent.allowlist}'"

        # Create tmux window and launch CC
        cmd = f"cd {shlex.quote(project_dir)} && {cc_cmd}"
        self._tmux(["new-window", "-t", self.tmux_session, "-n", self.window_name, cmd])
        logger.info("Started CC instance '%s' in %s", self.agent.name, project_dir)

        # Wait for CC to be ready (❯ prompt).
        # During startup CC may show a "trust this folder" dialog or other
        # permission prompts — auto-approve them so the instance reaches idle.
        start_time = time.time()
        # Give CC a moment to fully render its initial screen
        time.sleep(5)
        while time.time() - start_time < _STARTUP_TIMEOUT:
            output = self.capture()
            state = detect_state(output)
            if state == PaneState.IDLE:
                logger.info("CC instance '%s' is ready.", self.agent.name)
                self._started = True
                return
            if state == PaneState.PERMISSION:
                logger.info("CC instance '%s' has a permission/trust prompt — auto-approving.", self.agent.name)
                self.approve_permission()
                time.sleep(3)
                continue
            time.sleep(3)

        logger.warning("CC instance '%s' did not reach idle state within %ds.",
                        self.agent.name, _STARTUP_TIMEOUT)
        self._started = True  # proceed anyway

    def stop(self) -> None:
        """Send /exit to CC, then kill the window if needed."""
        try:
            self.send("/exit")
            time.sleep(2)
        except Exception:
            pass
        try:
            self._tmux(["kill-window", "-t", self.target])
        except Exception:
            pass
        self._started = False

    def is_running(self) -> bool:
        """Check if the tmux window still exists."""
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", self.tmux_session],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return False
            result = subprocess.run(
                ["tmux", "list-windows", "-t", self.tmux_session, "-F", "#{window_name}"],
                capture_output=True, text=True, timeout=5,
            )
            return self.window_name in result.stdout.splitlines()
        except Exception:
            return False

    # ── Communication ──────────────────────────────────────────────────

    def send(self, text: str) -> None:
        """Send text to the CC pane via tmux send-keys."""
        self._tmux(["send-keys", "-t", self.target, text, "Enter"])
        logger.debug("Sent to '%s': %s", self.agent.name, text[:150])

    def capture(self, lines: int = 200) -> str:
        """Capture current pane output."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", self.target, "-p", "-S", f"-{lines}"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout
        except Exception as e:
            logger.error("Failed to capture pane '%s': %s", self.agent.name, e)
            return ""

    def approve_permission(self) -> None:
        """Press Enter to approve a permission prompt."""
        self._tmux(["send-keys", "-t", self.target, "", "Enter"])
        logger.info("Auto-approved permission for '%s'", self.agent.name)

    def reject_permission(self) -> None:
        """Press Escape to reject/cancel a permission prompt."""
        self._tmux(["send-keys", "-t", self.target, "Escape"])
        logger.info("Rejected permission for '%s'", self.agent.name)

    def send_compact(self, hint: str = "") -> None:
        """Send /compact with optional context preservation hint."""
        if hint:
            self.send(hint)
            time.sleep(2)
        self.send("/compact")
        logger.info("Sent /compact to '%s'", self.agent.name)

    # ── Logging ────────────────────────────────────────────────────────

    def save_log(self, content: str) -> None:
        """Append captured output to the agent's log file."""
        import os
        log_path = os.path.join(self.config.agents_log_dir, f"{self.agent.name}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(content)
            f.write("\n--- capture ---\n")

    # ── Internal ───────────────────────────────────────────────────────

    def _tmux(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux"] + args,
            capture_output=True, text=True, timeout=10,
        )
