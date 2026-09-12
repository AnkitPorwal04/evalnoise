"""Subprocess-mocked tests. No test in this file may reach a real model or a network."""

import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from evalnoise import cli, subscription
from evalnoise.subscription import (
    DISABLED_FEATURES, EXPECTED_VERSION_OUTPUT, REQUIRED_AUTH, Execution,
    SubscriptionCheckError, check, child_env, evaluate, exec_argv, fingerprint,
    inspect_tool_catalog, parse_events, parse_features, parse_mcp_enabled, parse_usage,
    preflight, run_bounded,
)

FEATURES_TEXT = "\n".join(f"{name}  stable  true" for name in DISABLED_FEATURES)
MCP_TEXT = "Name Command Status\nnode_repl /bin/node enabled\ncua_repl /bin/x disabled\n"
PROMPT_INPUT_TEXT = json.dumps([{"type": "message", "role": "user",
                                 "content": [{"type": "input_text", "text": "2+2"}]}])
ANSWER = {"type": "item.completed",
          "item": {"type": "agent_message", "text": json.dumps({"answer": 4})}}
# Literal phrasings rather than one broad regex, so a failure names the exact spend claim
# that reappeared instead of only reporting that some pattern matched.
CONSUMED_QUOTA_CLAIMS = (
    "was consumed", "were consumed", "has been consumed", "quota consumed",
    "consumed your", "consumed the", "quota was used", "quota was spent",
    "spent your", "charged your", "billed your",
)
USAGE = {"type": "turn.completed",
         "usage": {"input_tokens": 11, "output_tokens": 5, "cached_input_tokens": 2}}


def stub_binary(directory):
    """An executable stub, so this suite needs no Codex CLI installed on the host.

    `preflight` resolves its binary with `shutil.which`, which accepts an absolute path
    without consulting `PATH`. Every test drives the child through a scripted runner, so
    this file is never executed; it exits 70 so that a test which did spawn it would fail
    loudly instead of looking like a pass.
    """
    path = Path(directory) / "codex-stub"
    path.write_text("#!/bin/sh\nexit 70\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def execution(stdout="", returncode=0, **kwargs):
    defaults = dict(argv=("codex",), returncode=returncode, stdout=stdout, stderr="",
                    duration_s=0.01, timed_out=False, stdout_truncated=False,
                    stderr_truncated=False)
    defaults.update(kwargs)
    return Execution(**defaults)


def scripted(responses):
    """Return a runner that dispatches on the codex subcommand, recording every argv."""
    calls = []

    def runner(argv, *, cwd, env, timeout_s, **kwargs):
        calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env), "timeout_s": timeout_s})
        for marker, result in responses.items():
            if marker in " ".join(argv[1:]):
                return result
        raise AssertionError(f"unscripted call: {argv}")

    runner.calls = calls
    return runner


def happy_preflight(**overrides):
    responses = {"--version": execution(EXPECTED_VERSION_OUTPUT),
                 "login status": execution("", stderr=REQUIRED_AUTH + "\n"),
                 "features list": execution(FEATURES_TEXT),
                 "mcp list": execution(MCP_TEXT),
                 "debug prompt-input": execution(PROMPT_INPUT_TEXT)}
    responses.update(overrides)
    return responses


def jsonl(*events):
    return "\n".join(json.dumps(event) for event in events)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.env = {"HOME": self.directory.name, "PATH": os.environ["PATH"]}
        self.binary = stub_binary(self.directory.name)

    def stored(self):
        return (self.output / "subscription-smoke.json").read_text()

    def unlocked(self, exec_result):
        catalog = json.dumps({"tools": [{"name": "get_time"}]})
        return scripted(happy_preflight(**{"debug prompt-input": execution(catalog),
                                           "exec": exec_result}))

    def blocked_check(self, runner=None):
        return check(output=self.output, confirmed=True, binary=self.binary, env=self.env,
                     runner=scripted(happy_preflight()) if runner is None else runner)

    def run_check(self, exec_result, **kwargs):
        return check(output=self.output, confirmed=True, runner=self.unlocked(exec_result),
                     env=self.env, binary=self.binary, **kwargs)


class ChildEnvironment(Base):
    def test_keeps_only_the_allowlist(self):
        env = child_env({"HOME": "/h", "PATH": "/bin", "CODEX_HOME": "/h/.codex",
                         "TERM": "xterm", "TMPDIR": "/tmp", "AWS_SECRET_ACCESS_KEY": "x",
                         "OPENAI_API_KEY": "y", "LANG": "C", "SSH_AUTH_SOCK": "/s"})
        self.assertEqual(env, {"HOME": "/h", "PATH": "/bin", "CODEX_HOME": "/h/.codex",
                               "TERM": "xterm", "TMPDIR": "/tmp"})

    def test_omits_codex_home_when_unset(self):
        self.assertNotIn("CODEX_HOME", child_env({"HOME": "/h", "PATH": "/bin"}))

    def test_requires_home_for_subscription_auth(self):
        with self.assertRaisesRegex(SubscriptionCheckError, "HOME is required"):
            child_env({"PATH": "/bin"})

    def test_no_api_key_or_token_reaches_the_child(self):
        runner = scripted(happy_preflight())
        preflight(binary=self.binary, runner=runner,
                  env={**self.env, "OPENAI_API_KEY": "sk-live", "CODEX_ACCESS_TOKEN": "tok"})
        self.assertTrue(runner.calls)
        for call in runner.calls:
            self.assertNotIn("OPENAI_API_KEY", call["env"])
            self.assertNotIn("CODEX_ACCESS_TOKEN", call["env"])
            self.assertFalse([n for n in call["env"] if "KEY" in n or "TOKEN" in n])


class PreflightGating(Base):
    def state(self, **overrides):
        self.runner = scripted(happy_preflight(**overrides))
        return preflight(binary=self.binary, env=self.env, runner=self.runner)

    def test_version_pin_blocks_other_builds(self):
        state = self.state(**{"--version": execution("codex-cli 0.154.0")})
        self.assertFalse(state.version_ok)
        self.assertIn("version pin failed", state.blocked_reason)
        self.assertFalse([c for c in self.runner.calls if "login status" in " ".join(c["argv"])])

    def test_auth_is_read_from_stderr_where_codex_reports_it(self):
        state = self.state()
        self.assertTrue(state.auth_ok)
        self.assertEqual(state.auth_reported, REQUIRED_AUTH)

    def test_api_key_auth_is_refused(self):
        state = self.state(**{"login status": execution("", stderr="Logged in using an API key")})
        self.assertFalse(state.auth_ok)
        self.assertIn("bill an account you did not authorize", state.blocked_reason)

    def test_logged_out_is_refused(self):
        state = self.state(**{"login status": execution("Not logged in", returncode=1)})
        self.assertFalse(state.auth_ok)
        self.assertIn("Run `codex login`", state.blocked_reason)

    def test_renamed_feature_blocks_instead_of_silently_no_opping(self):
        trimmed = "\n".join(line for line in FEATURES_TEXT.splitlines()
                            if not line.startswith("shell_tool"))
        state = self.state(**{"features list": execution(trimmed)})
        self.assertEqual(state.features_missing, ("shell_tool",))
        self.assertIn("silent no-op", state.blocked_reason)

    def test_enabled_mcp_servers_are_recorded(self):
        self.assertEqual(self.state().mcp_servers_enabled, ("node_repl",))

    def test_preflight_never_invokes_exec(self):
        self.state()
        self.assertFalse([c for c in self.runner.calls if c["argv"][1] == "exec"])

    def test_every_disable_flag_reaches_prompt_input(self):
        self.state()
        rendered = [c for c in self.runner.calls if "prompt-input" in " ".join(c["argv"])][0]
        for name in DISABLED_FEATURES:
            with self.subTest(feature=name):
                self.assertIn(name, rendered["argv"])


class ToolCatalog(Base):
    def test_absent_catalog_is_unverifiable_not_empty(self):
        verified, reason, digest = inspect_tool_catalog(execution(PROMPT_INPUT_TEXT))
        self.assertFalse(verified)
        self.assertIn("cannot be verified", reason)
        self.assertEqual(len(digest), 64)

    def test_executing_tool_in_catalog_is_refused(self):
        for name in ("shell", "exec_command", "apply_patch"):
            with self.subTest(tool=name):
                verified, reason, _ = inspect_tool_catalog(
                    execution(json.dumps({"tools": [{"name": name}]})))
                self.assertFalse(verified)
                self.assertIn("offers executing tools", reason)

    def test_non_executing_catalog_verifies(self):
        verified, reason, _ = inspect_tool_catalog(
            execution(json.dumps({"tools": [{"name": "get_time"}]})))
        self.assertTrue(verified)
        self.assertIn("non-executing", reason)

    def test_malformed_prompt_input_is_not_a_pass(self):
        verified, reason, _ = inspect_tool_catalog(execution("not json"))
        self.assertFalse(verified)
        self.assertIn("did not return JSON", reason)

    def test_failed_prompt_input_is_not_a_pass(self):
        verified, _, _ = inspect_tool_catalog(execution(PROMPT_INPUT_TEXT, returncode=1))
        self.assertFalse(verified)

    def test_prompt_input_content_is_never_persisted(self):
        secret = json.dumps([{"type": "message", "role": "developer",
                              "content": [{"type": "input_text",
                                           "text": "/Users/secret/.codex private notes"}]}])
        runner = scripted(happy_preflight(**{"debug prompt-input": execution(secret)}))
        result = self.blocked_check(runner)
        self.assertNotIn("private notes", self.stored())
        self.assertNotIn("/Users/secret", self.stored())
        self.assertEqual(len(result["preflight"]["prompt_input_sha256"]), 64)


class BlockedByDefault(Base):
    def test_unverifiable_catalog_blocks_the_model_call(self):
        runner = scripted(happy_preflight())
        result = self.blocked_check(runner)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["model_called"])
        self.assertFalse([c for c in runner.calls if c["argv"][1] == "exec"])
        self.assertIn("detector", result["blocked_reason"])

    def test_blocked_result_is_persisted(self):
        self.blocked_check()
        stored = json.loads(self.stored())
        self.assertEqual(stored["artifact_type"], "subscription_smoke")
        self.assertEqual(stored["status"], "blocked")

    def test_blocked_artifact_claims_no_quota_was_consumed(self):
        result = self.blocked_check()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["model_called"])
        self.assertNotIn("execution", result)
        for field in ("answer", "usage", "subscription_cost_usd"):
            with self.subTest(field=field):
                self.assertIsNone(result[field])
        stored = self.stored().lower()
        for claim in CONSUMED_QUOTA_CLAIMS:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, stored)

    def test_blocked_artifact_states_quota_use_is_unknown_not_zero(self):
        result = self.blocked_check()
        self.assertIn("preflight alone sends no model request", result["billing_note"])
        caveats = " ".join(result["caveats"]).lower()
        self.assertIn("unknown, not zero", caveats)
        self.assertIn("not measured by this check", caveats)

    def test_confirmation_is_required(self):
        with self.assertRaisesRegex(SubscriptionCheckError, "confirm-subscription-use"):
            check(output=self.output, confirmed=False, runner=scripted(happy_preflight()))

    def test_refusal_writes_no_artifact(self):
        with self.assertRaises(SubscriptionCheckError):
            check(output=self.output, confirmed=False, runner=scripted(happy_preflight()))
        self.assertFalse((self.output / "subscription-smoke.json").exists())


class Interpretation(unittest.TestCase):
    """`evaluate` is pure, so result rules are checked with no catalog and no subprocess."""

    def test_known_answer_passes(self):
        outcome = evaluate(execution(jsonl(ANSWER, USAGE)))
        self.assertEqual(outcome["status"], "passed")
        self.assertEqual(outcome["answer"], 4)
        self.assertEqual(outcome["usage"], {"input_tokens": 11, "output_tokens": 5,
                                            "cached_input_tokens": 2})

    def test_wrong_answer_fails(self):
        wrong = {"type": "item.completed",
                 "item": {"type": "agent_message", "text": json.dumps({"answer": 5})}}
        outcome = evaluate(execution(jsonl(wrong)))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("Expected 4", outcome["failure"])

    def test_reasoning_is_counted_not_returned(self):
        reasoning = {"type": "item.completed",
                     "item": {"type": "reasoning", "text": "chain of thought to keep private"}}
        outcome = evaluate(execution(jsonl(reasoning, ANSWER)))
        self.assertEqual(outcome["event_counts"]["item.reasoning"], 1)
        self.assertNotIn("chain of thought", json.dumps(outcome))

    def test_unknown_event_contributes_only_a_type_name(self):
        unknown = {"type": "item.completed",
                   "item": {"type": "mystery", "payload": "undocumented private data"}}
        outcome = evaluate(execution(jsonl(unknown, ANSWER)))
        self.assertEqual(outcome["event_counts"]["item.mystery"], 1)
        self.assertNotIn("undocumented private data", json.dumps(outcome))

    def test_tool_use_detected_after_the_fact_is_not_called_a_control(self):
        for item_type in ("command_execution", "function_call", "mcp_tool_call",
                          "web_search_call", "file_change", "local_shell_call"):
            with self.subTest(item=item_type):
                tool = {"type": "item.completed", "item": {"type": item_type}}
                outcome = evaluate(execution(jsonl(tool, ANSWER)))
                self.assertEqual(outcome["status"], "failed")
                self.assertIn("not a control", outcome["failure"])

    def test_invalid_jsonl_is_a_protocol_failure(self):
        outcome = evaluate(execution("{not json}\n"))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("not valid JSON", outcome["failure"])

    def test_event_without_string_type_is_a_protocol_failure(self):
        outcome = evaluate(execution(json.dumps({"type": 7})))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("string type", outcome["failure"])

    def test_two_assistant_messages_fail(self):
        outcome = evaluate(execution(jsonl(ANSWER, ANSWER)))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("exactly one assistant message", outcome["failure"])

    def test_timeout_reports_no_retries(self):
        outcome = evaluate(execution("", returncode=None, timed_out=True))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("no retries", outcome["failure"])

    def test_output_overflow_fails(self):
        outcome = evaluate(execution(jsonl(ANSWER), stdout_truncated=True))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("byte cap", outcome["failure"])

    def test_stderr_overflow_fails(self):
        outcome = evaluate(execution(jsonl(ANSWER), stderr_truncated=True))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("byte cap", outcome["failure"])

    def test_nonzero_exit_fails(self):
        outcome = evaluate(execution(jsonl(ANSWER), returncode=1, stderr="boom"))
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("exited 1", outcome["failure"])

    def test_non_integer_answer_fails(self):
        for text in (json.dumps({"answer": True}), json.dumps({"answer": "4"}),
                     json.dumps({"answer": 4.0})):
            with self.subTest(text=text):
                message = {"type": "item.completed",
                           "item": {"type": "agent_message", "text": text}}
                self.assertEqual(evaluate(execution(jsonl(message)))["status"], "failed")

    def test_terminal_escape_in_answer_is_stripped(self):
        nasty = {"type": "item.completed",
                 "item": {"type": "agent_message", "text": "\x1b[2J\x07bad\x00"}}
        outcome = evaluate(execution(jsonl(nasty)))
        self.assertEqual(outcome["status"], "failed")
        self.assertNotIn("\x1b", json.dumps(outcome))
        self.assertNotIn("\x00", json.dumps(outcome))


class ArtifactAssembly(Base):
    """End-to-end record assembly. The verified catalog exists only in the mocked runner."""

    def test_passing_run_is_assembled_and_persisted(self):
        result = self.run_check(execution(jsonl(ANSWER, USAGE)))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["model_called"])
        self.assertEqual(json.loads(self.stored())["answer"], 4)

    def test_subscription_cost_is_null_never_zero(self):
        result = self.run_check(execution(jsonl(ANSWER, USAGE)))
        self.assertIsNone(result["subscription_cost_usd"])
        self.assertIn("no per-call usd price", result["billing_note"].lower())
        self.assertNotIn("usd", json.dumps(result["usage"]).lower())

    def test_reasoning_never_reaches_the_artifact(self):
        reasoning = {"type": "item.completed",
                     "item": {"type": "reasoning", "text": "chain of thought to keep private"}}
        self.run_check(execution(jsonl(reasoning, ANSWER)))
        self.assertNotIn("chain of thought", self.stored())

    def test_unknown_payload_never_reaches_the_artifact(self):
        unknown = {"type": "item.completed",
                   "item": {"type": "mystery", "payload": "undocumented private data"}}
        self.run_check(execution(jsonl(unknown, ANSWER)))
        self.assertNotIn("undocumented private data", self.stored())

    def test_exec_is_invoked_exactly_once_with_no_retry(self):
        runner = self.unlocked(execution("", returncode=None, timed_out=True))
        result = check(output=self.output, confirmed=True, runner=runner, env=self.env,
                       binary=self.binary)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len([c for c in runner.calls if c["argv"][1] == "exec"]), 1)

    def test_timeout_is_passed_to_the_exec_call(self):
        runner = self.unlocked(execution(jsonl(ANSWER)))
        check(output=self.output, confirmed=True, runner=runner, env=self.env,
              binary=self.binary, timeout_s=60.0)
        self.assertEqual([c for c in runner.calls if c["argv"][1] == "exec"][0]["timeout_s"], 60.0)


class NoBypassExists(unittest.TestCase):
    def test_check_exposes_no_tool_verification_override(self):
        parameters = set(inspect.signature(check).parameters)
        self.assertEqual(parameters, {"output", "binary", "confirmed", "env", "runner",
                                      "timeout_s"})

    def test_no_bypass_identifier_survives_in_the_module(self):
        source = Path(subscription.__file__).read_text()
        for banned in ("allow_unverified", "skip_tool_check", "force_model_call"):
            with self.subTest(identifier=banned):
                self.assertNotIn(banned, source)

    def test_cli_offers_no_override_flag(self):
        source = Path(cli.__file__).read_text()
        for banned in ("allow_unverified", "--force", "--skip-preflight"):
            with self.subTest(flag=banned):
                self.assertNotIn(banned, source)

    def test_blocked_reason_is_stated_as_evalnoise_policy(self):
        with tempfile.TemporaryDirectory() as home:
            state = preflight(binary=stub_binary(home), env={"HOME": home, "PATH": "/bin"},
                              runner=scripted(happy_preflight()))
        self.assertIn("EvalNoise blocks", state.blocked_reason)
        self.assertNotIn("You asked", state.blocked_reason)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_maps_a_blocked_result_to_exit_code_3(self):
        blocked = {"status": "blocked", "model_called": False, "blocked_reason": "unverifiable"}
        with mock.patch.object(cli, "subscription_check", return_value=blocked) as checked:
            code, out, _ = self.run_cli(["codex-check", "--confirm-subscription-use"])
        self.assertEqual(code, 3)
        self.assertTrue(checked.call_args.kwargs["confirmed"])
        self.assertFalse(json.loads(out)["model_called"])

    def test_cli_refuses_without_the_confirmation_flag(self):
        with mock.patch.object(cli, "subscription_check") as checked:
            checked.side_effect = SubscriptionCheckError("confirm-subscription-use")
            code, out, err = self.run_cli(["codex-check"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("confirm-subscription-use", err)
        self.assertFalse(checked.call_args.kwargs["confirmed"])


class EnforcedArgv(unittest.TestCase):
    def test_carries_every_required_control(self):
        argv = exec_argv("/usr/bin/codex", Path("/tmp/schema.json"))
        for flag in ("--strict-config", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                     "--skip-git-repo-check", "--json", "--output-schema"):
            with self.subTest(flag=flag):
                self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        for override in ('forced_login_method="chatgpt"', "tools.web_search=false",
                         "project_doc_max_bytes=0", "project_root_markers=[]", "mcp_servers={}"):
            with self.subTest(override=override):
                self.assertIn(override, argv)

    def test_never_grants_write_or_bypass(self):
        argv = " ".join(exec_argv("/usr/bin/codex", Path("/tmp/s.json")))
        for forbidden in ("danger-full-access", "workspace-write", "--add-dir",
                          "--dangerously-bypass-approvals-and-sandbox",
                          "--dangerously-bypass-hook-trust", "--approve-for-me"):
            with self.subTest(flag=forbidden):
                self.assertNotIn(forbidden, argv)

    def test_fingerprint_is_stable_and_tracks_the_surface(self):
        first = fingerprint()
        self.assertEqual(first, fingerprint())
        self.assertEqual(len(first), 64)
        with mock.patch.object(subscription, "CONFIG_OVERRIDES", ("tools.web_search=true",)):
            self.assertNotEqual(fingerprint(), first)


class Parsers(unittest.TestCase):
    def test_parse_features_reads_the_three_column_layout(self):
        parsed = parse_features("shell_tool  stable  true\nsqlite  removed  false\nheader junk\n")
        self.assertEqual(parsed, {"shell_tool": ("stable", True), "sqlite": ("removed", False)})

    def test_parse_mcp_enabled_ignores_disabled_and_header(self):
        self.assertEqual(parse_mcp_enabled(MCP_TEXT), ("node_repl",))

    def test_parse_usage_rejects_booleans_and_negatives(self):
        self.assertIsNone(parse_usage({"input_tokens": True, "output_tokens": -1})["input_tokens"])
        self.assertIsNone(parse_usage({"output_tokens": -1})["output_tokens"])
        self.assertIsNone(parse_usage("nope"))

    def test_parse_events_accepts_an_empty_stream(self):
        self.assertEqual(parse_events(""), ([], {}, (), None))


class BoundedRunner(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = Path(self.directory.name)
        self.env = {"PATH": os.environ["PATH"]}

    def test_captures_a_normal_exit(self):
        result = run_bounded([sys.executable, "-c", "print('hello')"], cwd=self.cwd,
                             env=self.env, timeout_s=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertFalse(result.timed_out)

    def test_kills_on_timeout(self):
        started = time.monotonic()
        result = run_bounded([sys.executable, "-c", "import time; time.sleep(60)"],
                             cwd=self.cwd, env=self.env, timeout_s=1.0)
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 30)

    def test_truncates_and_stops_a_flood(self):
        result = run_bounded([sys.executable, "-c",
                              "import sys\nwhile True: sys.stdout.write('x'*4096)"],
                             cwd=self.cwd, env=self.env, timeout_s=30, stdout_limit=8192)
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout), 8192)

    def test_kills_the_whole_process_group(self):
        marker = self.cwd / "child-alive"
        script = (f"import subprocess,sys,time\n"
                  f"subprocess.Popen([sys.executable,'-c',"
                  f"\"import time,pathlib;time.sleep(15);"
                  f"pathlib.Path(r'{marker}').write_text('leaked')\"])\n"
                  f"time.sleep(15)\n")
        result = run_bounded([sys.executable, "-c", script], cwd=self.cwd, env=self.env,
                             timeout_s=1.0)
        self.assertTrue(result.timed_out)
        time.sleep(3)
        self.assertFalse(marker.exists(), "a grandchild outlived the killed process group")

    def helper_script(self, marker, delay=4):
        return (f"import subprocess,sys\n"
                f"subprocess.Popen([sys.executable,'-c',"
                f"\"import time,pathlib;time.sleep({delay});"
                f"pathlib.Path(r'{marker}').write_text('leaked')\"],"
                f"stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                f"print('parent done')\n")

    def test_parent_exiting_normally_still_leaves_no_helper(self):
        marker = self.cwd / "leaked"
        result = run_bounded([sys.executable, "-c", self.helper_script(marker)], cwd=self.cwd,
                             env=self.env, timeout_s=20)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertIn("parent done", result.stdout)
        time.sleep(6)
        self.assertFalse(marker.exists(),
                         "a helper survived a normal parent exit and was never reaped")

    def test_normal_exit_preserves_a_nonzero_status(self):
        result = run_bounded([sys.executable, "-c", "import sys; sys.exit(3)"], cwd=self.cwd,
                             env=self.env, timeout_s=20)
        self.assertEqual(result.returncode, 3)

    def test_terminating_never_signals_our_own_process_group(self):
        with mock.patch("os.killpg") as killed:
            subscription._terminate_group(mock.Mock(), os.getpgrp())
        killed.assert_not_called()

    def test_gives_the_child_no_stdin(self):
        result = run_bounded([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
                             cwd=self.cwd, env=self.env, timeout_s=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("''", result.stdout)

    def test_python_floor_is_met(self):
        self.assertGreaterEqual(sys.version_info, (3, 11))


if __name__ == "__main__":
    unittest.main()
