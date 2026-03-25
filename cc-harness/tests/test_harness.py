"""Tests for CC-Native Multi-Agent Harness."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AgentDef, HarnessConfig, load_config, parse_task_file
from state_detector import (
    PaneState, classify_permission, detect_state, extract_last_response,
    get_context_pct, get_cost,
)


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ═══════════════════════════════════════════════════════════════════════════
# config.py
# ═══════════════════════════════════════════════════════════════════════════

class TestParseTaskFile(unittest.TestCase):

    def test_parse_yaml(self):
        data = parse_task_file(os.path.join(FIXTURES, "test_task.yaml"))
        self.assertEqual(data["name"], "test-task")
        self.assertEqual(data["goal"], "Write hello.py")
        self.assertIn("coder", data["agents"])
        self.assertIn("tester", data["agents"])

    def test_parse_markdown(self):
        data = parse_task_file(os.path.join(FIXTURES, "test_task.md"))
        self.assertEqual(data["name"], "test-task-md")
        # Goal should come from markdown body
        self.assertIn("Test Task", data["goal"])
        self.assertIn("Requirements", data["goal"])

    def test_markdown_without_frontmatter_raises(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False)
        tmp.write("# No frontmatter\nJust text.")
        tmp.close()
        with self.assertRaises(ValueError):
            parse_task_file(tmp.name)
        os.unlink(tmp.name)


class TestLoadConfig(unittest.TestCase):

    def test_load_yaml(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.yaml"))
        self.assertIsInstance(cfg, HarnessConfig)
        self.assertEqual(cfg.task_name, "test-task")
        self.assertEqual(cfg.working_dir, "/tmp/cc-harness-test")
        self.assertIn("coder", cfg.agents)
        self.assertIn("tester", cfg.agents)

    def test_load_markdown(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.md"))
        self.assertEqual(cfg.task_name, "test-task-md")
        self.assertIn("Test Task", cfg.task_goal)

    def test_agent_def_fields(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.yaml"))
        coder = cfg.agents["coder"]
        self.assertIsInstance(coder, AgentDef)
        self.assertEqual(coder.name, "coder")
        self.assertEqual(coder.role, "Write code")
        self.assertEqual(coder.project_dir, "/tmp/cc-harness-test")
        self.assertEqual(coder.allowlist, "Bash,Read,Write")

    def test_agent_def_no_allowlist(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.yaml"))
        tester = cfg.agents["tester"]
        self.assertIsNone(tester.allowlist)

    def test_derived_paths(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.yaml"))
        self.assertEqual(cfg.harness_path, "/tmp/cc-harness-test/.harness")
        self.assertEqual(cfg.plan_file, "/tmp/cc-harness-test/.harness/plan.json")
        self.assertEqual(cfg.output_path, "/tmp/cc-harness-test/output")

    def test_ensure_dirs(self):
        tmpdir = tempfile.mkdtemp()
        try:
            cfg = HarnessConfig(task_file="x", working_dir=tmpdir)
            cfg.ensure_dirs()
            self.assertTrue(os.path.isdir(cfg.harness_path))
            self.assertTrue(os.path.isdir(cfg.agents_log_dir))
            self.assertTrue(os.path.isdir(cfg.output_path))
        finally:
            shutil.rmtree(tmpdir)

    def test_max_iterations_override(self):
        cfg = load_config(os.path.join(FIXTURES, "test_task.yaml"), max_iterations=5)
        self.assertEqual(cfg.max_iterations, 5)


# ═══════════════════════════════════════════════════════════════════════════
# state_detector.py
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectState(unittest.TestCase):

    def test_active_thinking(self):
        self.assertEqual(detect_state("some output\nThinking..."), PaneState.ACTIVE)

    def test_active_doodling(self):
        self.assertEqual(detect_state("Doodling away"), PaneState.ACTIVE)

    def test_active_spinner(self):
        self.assertEqual(detect_state("line1\n✢ processing"), PaneState.ACTIVE)

    def test_active_star_pattern(self):
        self.assertEqual(detect_state("* Reading files"), PaneState.ACTIVE)

    def test_idle_prompt(self):
        self.assertEqual(detect_state("output done\n  ❯"), PaneState.IDLE)

    def test_idle_prompt_bare(self):
        self.assertEqual(detect_state("❯"), PaneState.IDLE)

    def test_permission_yn(self):
        self.assertEqual(detect_state("Allow this action? [y/N]"), PaneState.PERMISSION)

    def test_permission_proceed(self):
        self.assertEqual(detect_state("Do you want to proceed?"), PaneState.PERMISSION)

    def test_permission_allow_once(self):
        self.assertEqual(detect_state("Allow once"), PaneState.PERMISSION)

    def test_expired_srun(self):
        self.assertEqual(detect_state("srun: error: something"), PaneState.EXPIRED)

    def test_expired_crun(self):
        self.assertEqual(detect_state("End crun session"), PaneState.EXPIRED)

    def test_expired_connection(self):
        self.assertEqual(detect_state("Connection closed by remote host"), PaneState.EXPIRED)

    def test_unknown_empty(self):
        self.assertEqual(detect_state(""), PaneState.UNKNOWN)
        self.assertEqual(detect_state("   \n  "), PaneState.UNKNOWN)

    def test_unknown_random_text(self):
        self.assertEqual(detect_state("just some random text"), PaneState.UNKNOWN)

    def test_priority_expired_over_idle(self):
        """Expired should win even if idle prompt is also present."""
        output = "srun: error: timeout\n  ❯"
        self.assertEqual(detect_state(output), PaneState.EXPIRED)

    def test_priority_permission_over_idle(self):
        output = "Do you want to proceed? [y/N]\n  ❯"
        # Permission check comes before idle
        self.assertEqual(detect_state(output), PaneState.PERMISSION)


class TestContextPct(unittest.TestCase):

    def test_extracts_pct(self):
        self.assertEqual(get_context_pct("line\n45% $3.21\n"), 45)

    def test_multiple_pct_takes_last(self):
        self.assertEqual(get_context_pct("10%\n20%\n75%\n"), 75)

    def test_no_pct(self):
        self.assertIsNone(get_context_pct("no percentage here"))

    def test_empty_input(self):
        self.assertIsNone(get_context_pct(""))


class TestGetCost(unittest.TestCase):

    def test_extracts_cost(self):
        self.assertEqual(get_cost("45% $12.34"), "$12.34")

    def test_no_cost(self):
        self.assertIsNone(get_cost("no cost"))


class TestExtractLastResponse(unittest.TestCase):

    def test_between_prompts(self):
        output = "  ❯\nfirst command\nresponse line 1\nresponse line 2\n  ❯"
        result = extract_last_response(output)
        self.assertIn("response line 1", result)
        self.assertIn("response line 2", result)

    def test_no_prompts_returns_tail(self):
        output = "just some output\nwithout prompts"
        result = extract_last_response(output)
        self.assertIn("just some output", result)

    def test_truncation(self):
        output = "  ❯\n" + "x" * 5000 + "\n  ❯"
        result = extract_last_response(output)
        self.assertIn("[truncated]", result)
        self.assertLessEqual(len(result), 3100)


class TestClassifyPermission(unittest.TestCase):

    def test_safe_trust_folder(self):
        output = " ❯ 1. Yes, I trust this folder\n   2. No, exit\n Enter to confirm"
        state, prompt_text, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertFalse(is_dangerous)

    def test_safe_allow_tool(self):
        output = "Allow this action? [y/N]\nBash(python hello.py)"
        state, prompt_text, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertFalse(is_dangerous)

    def test_safe_allow_once(self):
        output = "Allow once\nAllow always"
        state, _, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertFalse(is_dangerous)

    def test_dangerous_rm_rf(self):
        output = "Do you want to proceed? [y/N]\nBash(rm -rf /tmp/important)"
        state, prompt_text, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertTrue(is_dangerous)
        self.assertIn("rm -rf", prompt_text)

    def test_dangerous_sudo(self):
        output = "Allow this action? [y/N]\nBash(sudo apt-get purge something)"
        state, _, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertTrue(is_dangerous)

    def test_dangerous_force_push(self):
        output = "Do you want to proceed? [y/N]\nBash(git push --force origin main)"
        state, _, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertTrue(is_dangerous)

    def test_dangerous_reset_hard(self):
        output = "Allow this action? [y/N]\nBash(git reset --hard HEAD~5)"
        state, _, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertTrue(is_dangerous)

    def test_dangerous_delete_keyword(self):
        output = "Do you want to proceed? [y/N]\nThis will delete all files in the directory"
        state, _, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.PERMISSION)
        self.assertTrue(is_dangerous)

    def test_not_permission_returns_state(self):
        output = "  ❯"
        state, prompt_text, is_dangerous = classify_permission(output)
        self.assertEqual(state, PaneState.IDLE)
        self.assertEqual(prompt_text, "")
        self.assertFalse(is_dangerous)


class TestReviewPermission(unittest.TestCase):

    def _make_planner(self):
        from planner import Planner
        cfg = HarnessConfig(
            task_file="x", working_dir="/tmp",
            task_name="test", task_goal="do stuff",
            coordinator_base_url="http://fake", coordinator_api_key="fake",
        )
        with patch("planner.OpenAI"):
            p = Planner(cfg)
        return p

    def test_approve(self):
        p = self._make_planner()
        with patch.object(p, "_call", return_value='{"allow": true, "reason": "needed for task"}'):
            self.assertTrue(p.review_permission("coder", "clean build", "rm -rf build/"))

    def test_deny(self):
        p = self._make_planner()
        with patch.object(p, "_call", return_value='{"allow": false, "reason": "too risky"}'):
            self.assertFalse(p.review_permission("coder", "write code", "sudo rm -rf /"))

    def test_deny_on_parse_failure(self):
        p = self._make_planner()
        with patch.object(p, "_call", return_value="I cannot parse this"):
            self.assertFalse(p.review_permission("coder", "step", "prompt"))


# ═══════════════════════════════════════════════════════════════════════════
# cc_instance.py
# ═══════════════════════════════════════════════════════════════════════════

class TestCCInstance(unittest.TestCase):

    def test_init_target(self):
        from cc_instance import CCInstance
        agent = AgentDef(name="coder", role="code", project_dir="/tmp")
        cfg = HarnessConfig(task_file="x", working_dir="/tmp")
        inst = CCInstance(agent, "mysession", cfg)
        self.assertEqual(inst.target, "mysession:coder")
        self.assertEqual(inst.window_name, "coder")

    @patch("cc_instance.subprocess.run")
    def test_capture(self, mock_run):
        from cc_instance import CCInstance
        mock_run.return_value = MagicMock(stdout="  ❯")
        agent = AgentDef(name="coder", role="code", project_dir="/tmp")
        cfg = HarnessConfig(task_file="x", working_dir="/tmp")
        inst = CCInstance(agent, "sess", cfg)
        output = inst.capture()
        self.assertEqual(output, "  ❯")
        mock_run.assert_called_once()

    @patch("cc_instance.subprocess.run")
    def test_send(self, mock_run):
        from cc_instance import CCInstance
        mock_run.return_value = MagicMock()
        agent = AgentDef(name="coder", role="code", project_dir="/tmp")
        cfg = HarnessConfig(task_file="x", working_dir="/tmp")
        inst = CCInstance(agent, "sess", cfg)
        inst.send("do something")
        # send() calls send-keys first, then capture-pane to check for paste indicator.
        # Verify the first call is the send-keys.
        first_call_args = mock_run.call_args_list[0][0][0]
        self.assertEqual(first_call_args[0], "tmux")
        self.assertIn("send-keys", first_call_args)
        self.assertIn("do something", first_call_args)


# ═══════════════════════════════════════════════════════════════════════════
# planner.py
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanner(unittest.TestCase):

    def _make_planner(self):
        from planner import Planner
        cfg = HarnessConfig(
            task_file="x", working_dir="/tmp",
            task_name="test", task_goal="do stuff",
            coordinator_base_url="http://fake", coordinator_api_key="fake",
        )
        with patch("planner.OpenAI"):
            p = Planner(cfg)
        return p

    def test_init(self):
        p = self._make_planner()
        self.assertEqual(p.model, "aws/anthropic/bedrock-claude-opus-4-6")

    def test_token_usage_starts_zero(self):
        p = self._make_planner()
        usage = p.get_token_usage()
        self.assertEqual(usage["prompt_tokens"], 0)
        self.assertEqual(usage["completion_tokens"], 0)

    def test_create_plan_parses_json(self):
        p = self._make_planner()
        raw_response = 'Here is the plan:\n[{"description": "step1", "assigned_to": "coder", "depends_on": []}]\nDone.'
        with patch.object(p, "_call", return_value=raw_response):
            agents = {"coder": AgentDef(name="coder", role="code", project_dir="/tmp")}
            steps = p.create_plan(agents)
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0]["description"], "step1")
            self.assertEqual(steps[0]["assigned_to"], "coder")
            self.assertEqual(steps[0]["status"], "pending")
            self.assertEqual(steps[0]["id"], 0)

    def test_create_plan_fallback_on_bad_json(self):
        p = self._make_planner()
        with patch.object(p, "_call", return_value="not json at all"):
            agents = {"coder": AgentDef(name="coder", role="code", project_dir="/tmp")}
            steps = p.create_plan(agents)
            self.assertEqual(len(steps), 1)
            self.assertIn("Execute the task", steps[0]["description"])

    def test_evaluate_and_replan_parses_json(self):
        p = self._make_planner()
        raw = '{"next_instructions": {"coder": "do X"}, "plan_updates": [], "is_complete": false, "reasoning": "ok"}'
        with patch.object(p, "_call", return_value=raw):
            agents = {"coder": AgentDef(name="coder", role="code", project_dir="/tmp")}
            result = p.evaluate_and_replan([], agents)
            self.assertFalse(result["is_complete"])
            self.assertEqual(result["next_instructions"]["coder"], "do X")

    def test_evaluate_fallback_on_bad_json(self):
        p = self._make_planner()
        with patch.object(p, "_call", return_value="garbage"):
            result = p.evaluate_and_replan([], {})
            self.assertFalse(result["is_complete"])
            self.assertEqual(result["reasoning"], "parse_error")


if __name__ == "__main__":
    unittest.main()
