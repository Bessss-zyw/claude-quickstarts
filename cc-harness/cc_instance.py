"""Manage a Claude Code process running in a tmux window."""

from __future__ import annotations

import logging
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
        cmd = f"cd {project_dir} && {cc_cmd}"
        self._tmux(["new-window", "-t", self.tmux_session, "-n", self.window_name, cmd])
        logger.info("Started CC instance '%s' in %s", self.agent.name, project_dir)

        # Wait for CC to be ready (❯ prompt)
        start_time = time.time()
        while time.time() - start_time < _STARTUP_TIMEOUT:
            output = self.capture()
            state = detect_state(output)
            if state == PaneState.IDLE:
                logger.info("CC instance '%s' is ready.", self.agent.name)
                self._started = True
                return
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

    def send_compact(self, hint: str = "") -> None:
        """Send /compact with optional context preservation hint."""
        if hint:
            self.send(hint)
            time.sleep(2)
        self.send("/compact")
        logger.info("Sent /compact to '%s'", self.agent.name)

    # ── Internal ───────────────────────────────────────────────────────

    def _tmux(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux"] + args,
            capture_output=True, text=True, timeout=10,
        )
