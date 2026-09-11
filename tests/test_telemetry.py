"""Sampler wiring inside a trial, enforcement auditing, and probe evaluation."""

from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evalnoise.config import Profile, parse
from evalnoise.docker import DockerError
from evalnoise.probe import CLAIM, evaluate, expectations
from evalnoise.report import generate
from evalnoise.runner import CliSampler, execute, trial
from evalnoise.storage import write_json
from test_core import FakeDocker, config
from test_endpoint import SAMPLE, later


class RecordedStream:
    def __init__(self, samples, leaked=False):
        self.samples = samples
        self.leaked = leaked
        self.closed = False
        self.closed_at = None
        self.order = []

    def close(self, timeout=5):
        self.closed = True
        self.order.append("close")
        return {"samples": self.samples, "errors": ["recorded"] if self.leaked else [],
                "samples_received": len(self.samples), "samples_retained": len(self.samples),
                "bytes_read": 10, "truncated": False, "thread_leaked": self.leaked,
                "closed_before_cleanup": True, "retention_policy": "test",
                "derived": {"throttled_periods_delta": 7}}


class StreamingDocker(FakeDocker):
    def __init__(self, stream=None, **kwargs):
        super().__init__(telemetry_source="engine_stream", **kwargs)
        self.stream_object = stream
        self.events = []
        self.stream_requests = []

    def stream(self, container_id, interval, started):
        self.stream_requests.append((container_id, interval))
        return self.stream_object

    def remove(self, name):
        self.events.append("remove")
        if self.stream_object is not None:
            self.stream_object.order.append("remove")
        return super().remove(name)


class TrialHarness(unittest.TestCase):
    def run_trial(self, backend, interval=2, profile=None):
        experiment = parse(config())
        with tempfile.TemporaryDirectory() as directory:
            result = trial(backend, "abcdef123456", Path(directory),
                           {"id": "t000", "task": "cpu", "repeat": 0, "seed": 42},
                           experiment.tasks[0], profile or experiment.profiles[0],
                           {"id": "sha256:abc"}, interval, threading.Event())
            persisted = json.loads((Path(directory) / "trials/t000.json").read_text())
        self.assertEqual(result, persisted)
        return result


class TrialTelemetryTests(TrialHarness):
    def test_engine_stream_samples_are_stored_raw_with_metadata(self):
        stream = RecordedStream([{"received_elapsed_s": 0.1, "engine_read": SAMPLE["read"],
                                  "engine_preread": None, "degraded": False, "raw": SAMPLE}])
        result = self.run_trial(StreamingDocker(stream))
        self.assertEqual(result["telemetry_source"], "engine_stream")
        self.assertEqual(result["telemetry"][0]["raw"], SAMPLE)
        self.assertEqual(result["telemetry_meta"]["derived"]["throttled_periods_delta"], 7)
        self.assertNotIn("samples", result["telemetry_meta"])
        self.assertEqual(result["status"], "passed")

    def test_stream_is_closed_before_container_removal(self):
        stream = RecordedStream([])
        backend = StreamingDocker(stream)
        self.run_trial(backend)
        self.assertEqual(stream.order, ["close", "remove"])

    def test_stream_errors_do_not_change_the_workload_outcome(self):
        stream = RecordedStream([], leaked=True)
        result = self.run_trial(StreamingDocker(stream))
        self.assertEqual(result["status"], "passed")
        self.assertIn("recorded", result["telemetry_errors"])
        self.assertTrue(result["telemetry_meta"]["thread_leaked"])

    def test_unavailable_stream_falls_back_to_the_cli_snapshot(self):
        backend = StreamingDocker(None)
        result = self.run_trial(backend)
        self.assertEqual(result["telemetry_source"], "cli_snapshot")
        self.assertTrue(result["telemetry"])
        self.assertIn("raw", result["telemetry"][0])

    def test_sampler_stays_opt_in(self):
        backend = StreamingDocker(RecordedStream([]))
        result = self.run_trial(backend, interval=0)
        self.assertEqual(result["telemetry_source"], "disabled")
        self.assertEqual(result["telemetry"], [])
        self.assertEqual(backend.stream_requests, [])

    def test_stream_is_requested_with_the_full_container_id(self):
        backend = StreamingDocker(RecordedStream([]))
        self.run_trial(backend)
        self.assertEqual(backend.stream_requests, [("c" * 64, 2)])

    def test_resource_audit_is_recorded_before_start(self):
        backend = StreamingDocker(RecordedStream([]))
        result = self.run_trial(backend)
        self.assertTrue(result["resource_audit"]["enforced_as_requested"])
        commands = [call[0] for call in backend.calls]
        self.assertIn("start", commands)

    def test_rewritten_limits_refuse_the_trial_before_it_runs(self):
        class RewritingDocker(FakeDocker):
            def resources(self, name):
                return {**super().resources(name), "Memory": 1}
        backend = RewritingDocker()
        result = self.run_trial(backend, interval=0)
        self.assertEqual(result["status"], "enforcement_error")
        self.assertFalse(result["resource_audit"]["enforced_as_requested"])
        self.assertNotIn("start", [call[0] for call in backend.calls])
        self.assertEqual(len(backend.removed), 1)


class StreamCloseFaultTests(TrialHarness):
    def test_close_failure_still_cleans_up_and_persists_evidence(self):
        class Exploding(RecordedStream):
            def close(self, timeout=5):
                self.order.append("close")
                raise RuntimeError("close exploded")
        stream = Exploding([])
        backend = StreamingDocker(stream)
        experiment = parse(config())
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                trial(backend, "abcdef123456", Path(directory),
                      {"id": "t000", "task": "cpu", "repeat": 0, "seed": 42},
                      experiment.tasks[0], experiment.profiles[0], {"id": "sha256:abc"}, 2,
                      threading.Event())
            persisted = json.loads((Path(directory) / "trials/t000.json").read_text())
        self.assertEqual(stream.order, ["close", "remove"])
        self.assertEqual(len(backend.removed), 1)
        self.assertEqual(persisted["status"], "runner_error")
        self.assertTrue(persisted["telemetry_meta"]["close_failed"])
        self.assertIn("close exploded", " ".join(persisted["telemetry_errors"]))


class BlockingStatsDocker(FakeDocker):
    """CLI-snapshot backend whose `stats` parks until the test releases it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.sampler_thread = None
        self.removed_while_sampling = None

    def stats(self, name):
        self.sampler_thread = threading.current_thread()
        self.entered.set()
        self.release.wait(30)
        return {"MemUsage": "late / sample"}

    def remove(self, name):
        self.removed_while_sampling = (self.sampler_thread is not None
                                       and self.sampler_thread.is_alive())
        return super().remove(name)


class SnapshotSamplerTests(TrialHarness):
    def persisted_trial(self, backend, interval=2, timeout=None):
        experiment = parse(config())
        root = tempfile.mkdtemp(prefix="evalnoise-sampler-")
        patcher = (patch("evalnoise.runner.SAMPLER_JOIN_TIMEOUT_S", timeout)
                   if timeout is not None else nullcontext())
        with patcher:
            result = trial(backend, "abcdef123456", Path(root),
                           {"id": "t000", "task": "cpu", "repeat": 0, "seed": 42},
                           experiment.tasks[0], experiment.profiles[0],
                           {"id": "sha256:abc"}, interval, threading.Event())
        return result, Path(root) / "trials/t000.json"

    def test_snapshot_path_records_bounded_metadata_and_no_invented_deltas(self):
        result, _ = self.persisted_trial(FakeDocker())
        meta = result["telemetry_meta"]
        self.assertEqual(result["telemetry_source"], "cli_snapshot")
        self.assertTrue(result["telemetry"])
        self.assertEqual(meta["samples_received"], meta["samples_retained"])
        self.assertEqual(meta["samples_retained"], len(result["telemetry"]))
        self.assertFalse(meta["thread_leaked"])
        self.assertTrue(meta["closed_before_cleanup"])
        self.assertFalse(meta["truncated"])
        self.assertIsNone(meta["derived"])
        self.assertIn("never zero", meta["derived_note"])
        self.assertNotIn("samples", meta)

    def test_wedged_sampler_is_detached_and_cannot_mutate_recorded_evidence(self):
        backend = BlockingStatsDocker()
        built = []

        class Captured(CliSampler):
            def start(self):
                built.append(self)
                return super().start()

        with patch("evalnoise.runner.CliSampler", Captured):
            result, path = self.persisted_trial(backend, timeout=.1)
        self.assertTrue(backend.entered.wait(10), "sampler never reached the daemon call")
        sampler = built[0]

        meta = result["telemetry_meta"]
        self.assertTrue(meta["thread_leaked"])
        self.assertFalse(meta["closed_before_cleanup"])
        self.assertEqual(meta["samples_received"], 0)
        self.assertEqual(result["telemetry"], [])
        self.assertIn("detached", " ".join(result["telemetry_errors"]))
        self.assertEqual(result["status"], "passed",
                         "a leaked sampler is telemetry, never a workload outcome")

        before = path.read_bytes()
        backend.release.set()
        backend.sampler_thread.join(10)
        self.assertFalse(backend.sampler_thread.is_alive(), "sampler never finished")
        # The sample the leaked thread produced after close() must have been refused at the
        # source, not merely absent from a defensive copy of the buffer.
        self.assertEqual(sampler._samples, [], "a detached sampler still buffered a late sample")
        self.assertEqual(sampler.received, 0, "a detached sampler still counted a late sample")
        self.assertEqual(sampler.close(timeout=.1)["samples_received"], 0)
        self.assertEqual(result["telemetry"], [], "a detached sampler mutated live evidence")
        self.assertEqual(result["telemetry_meta"]["samples_received"], 0)
        self.assertEqual(path.read_bytes(), before,
                         "a detached sampler mutated the persisted artifact")

    def test_wedged_sampler_does_not_block_shutdown_or_cleanup(self):
        backend = BlockingStatsDocker()
        started = time.monotonic()
        result, _ = self.persisted_trial(backend, timeout=.1)
        elapsed = time.monotonic() - started
        self.assertTrue(backend.entered.is_set())
        self.assertLess(elapsed, 20, "cleanup waited on the wedged sampler")
        self.assertEqual(backend.removed, ["evalnoise-abcdef123456-t000"])
        self.assertTrue(backend.removed_while_sampling,
                        "removal must not have waited for the sampler thread to exit")
        self.assertIsNone(result["cleanup_error"])
        backend.release.set()
        backend.sampler_thread.join(10)

    def test_sampling_faults_are_recorded_without_changing_the_outcome(self):
        backend = FakeDocker()
        backend.stats = lambda _: (_ for _ in ()).throw(DockerError("stats unavailable"))
        result, _ = self.persisted_trial(backend)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["telemetry_errors"])
        self.assertIn("stats unavailable", " ".join(result["telemetry_errors"]))
        self.assertEqual(result["telemetry_meta"]["samples_received"], 0)
        self.assertFalse(result["telemetry_meta"]["thread_leaked"])

    def test_sampler_close_failure_still_cleans_up_and_persists(self):
        class Exploding(CliSampler):
            def close(self, timeout=None):
                super().close(timeout)
                raise RuntimeError("sampler close exploded")
        backend = FakeDocker()
        experiment = parse(config())
        with patch("evalnoise.runner.CliSampler", Exploding), tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                trial(backend, "abcdef123456", Path(root),
                      {"id": "t000", "task": "cpu", "repeat": 0, "seed": 42},
                      experiment.tasks[0], experiment.profiles[0], {"id": "sha256:abc"}, 2,
                      threading.Event())
            persisted = json.loads((Path(root) / "trials/t000.json").read_text())
        self.assertEqual(len(backend.removed), 1)
        self.assertEqual(persisted["status"], "runner_error")
        self.assertTrue(persisted["telemetry_meta"]["close_failed"])
        self.assertIn("sampler close exploded", " ".join(persisted["telemetry_errors"]))


class CliSamplerUnitTests(unittest.TestCase):
    def sampler(self, backend=None, interval=.01):
        return CliSampler(backend or FakeDocker(), "evalnoise-test", interval, time.monotonic())

    def test_detached_sampler_drops_late_samples_and_faults(self):
        sampler = self.sampler()
        meta = sampler.close(timeout=1)
        sampler._record({"elapsed_s": 99, "raw": {"late": True}})
        sampler._fault("late fault")
        self.assertEqual(sampler.close(timeout=1)["samples"], meta["samples"])
        self.assertEqual(sampler.close(timeout=1)["samples_received"], 0)
        self.assertNotIn("late fault", " ".join(sampler.close(timeout=1)["errors"]))

    def test_retained_samples_are_bounded_and_counted_as_received(self):
        sampler = self.sampler()
        with patch("evalnoise.runner.MAX_SNAPSHOT_SAMPLES", 3):
            for index in range(10):
                sampler._record({"elapsed_s": index, "raw": {}})
        meta = sampler.close(timeout=1)
        self.assertEqual(meta["samples_retained"], 3)
        self.assertEqual(meta["samples_received"], 10)
        self.assertTrue(meta["truncated"])

    def test_error_log_is_bounded_and_says_so(self):
        sampler = self.sampler()
        with patch("evalnoise.runner.MAX_SNAPSHOT_ERRORS", 4):
            for index in range(50):
                sampler._fault(f"fault {index}")
        errors = sampler.close(timeout=1)["errors"]
        self.assertEqual(len(errors), 5)
        self.assertIn("reached its bound", errors[-1])

    def test_a_healthy_sampler_stops_and_reports_no_leak(self):
        sampler = self.sampler().start()
        meta = sampler.close(timeout=5)
        self.assertFalse(meta["thread_leaked"])
        self.assertTrue(meta["closed_before_cleanup"])
        self.assertFalse(sampler.thread_leaked)
        self.assertIn("not a guaranteed cadence", meta["retention_policy"])


class ReportFidelityTests(unittest.TestCase):
    def build(self, data=None, backend=None):
        with tempfile.TemporaryDirectory() as root:
            directory = execute(parse(data or config()), root, backend or FakeDocker())
            summary = generate(directory)
            return summary, (directory / "report.html").read_text(), json.loads(
                (directory / "manifest.json").read_text())

    def test_per_profile_effective_sampling_replaces_the_global_value(self):
        data = config()
        data["sample_interval_s"] = 0
        data["profiles"][1]["sample_interval_s"] = 2
        summary, page, _ = self.build(data)
        rows = {row["profile"]: row for row in summary["fidelity"]["profiles"]}
        self.assertEqual(rows["tight"]["effective_sample_interval_s"], 0)
        self.assertFalse(rows["tight"]["sampling_enabled"])
        self.assertEqual(rows["roomy"]["effective_sample_interval_s"], 2)
        self.assertTrue(rows["roomy"]["sampling_enabled"])
        self.assertIn("profile override", page)
        self.assertIn("Measurement fidelity", page)
        self.assertNotIn("Sampling interval: 0 s", page)

    def test_absent_telemetry_is_null_not_zero(self):
        summary, page, _ = self.build()
        for row in summary["fidelity"]["profiles"]:
            self.assertIsNone(row["samples_received"])
            self.assertIsNone(row["samples_retained"])
            self.assertIsNone(row["truncated_trials"])
        for row in summary["fidelity"]["trials"]:
            self.assertIsNone(row["throttled_periods_delta"])
            self.assertIsNone(row["samples_retained"])
        self.assertIn("Not measured", page)
        self.assertIn("it is not zero", page)

    def test_engine_identity_warning_is_visible_and_escaped(self):
        backend = FakeDocker()
        backend.engine_id_at_end = "<img src=x onerror=alert(1)>"
        summary, page, _ = self.build(backend=backend)
        self.assertIsNotNone(summary["fidelity"]["engine_identity_warning"])
        self.assertIn("Engine identity warning", page)
        self.assertIn("&lt;img", page)
        self.assertNotIn("<img src=x", page)

    def test_stable_engine_has_no_warning(self):
        summary, page, _ = self.build()
        self.assertIsNone(summary["fidelity"]["engine_identity_warning"])
        self.assertNotIn("Engine identity warning", page)

    def test_affinity_and_endpoint_are_reported(self):
        data = config()
        data["profiles"][0]["cpuset_cpus"] = "0-1"
        summary, page, _ = self.build(data)
        rows = {row["profile"]: row for row in summary["fidelity"]["profiles"]}
        self.assertEqual(rows["tight"]["cpuset_cpus"], "0-1")
        self.assertTrue(rows["tight"]["affinity_configured"])
        self.assertIsNone(rows["roomy"]["cpuset_cpus"])
        self.assertFalse(rows["roomy"]["affinity_configured"])
        self.assertEqual(summary["fidelity"]["endpoint_source"], "default")
        self.assertIn("CPU affinity", page)

    def test_absent_affinity_reads_as_configuration_not_a_failed_measurement(self):
        summary, page, _ = self.build()
        self.assertIn("No affinity mask", page)
        self.assertFalse(any(row["affinity_configured"] for row in summary["fidelity"]["profiles"]))
        fidelity = page[page.index("Measurement fidelity"):page.index("Environment and provenance")]
        affinity_column = fidelity[fidelity.index("<tbody>"):fidelity.index("</tbody>")]
        self.assertIn("No affinity mask", affinity_column)

    def test_recorded_telemetry_is_aggregated_and_labelled_not_peak(self):
        data = config()
        data["repeats"], data["tasks"] = 1, data["tasks"][:1]
        data["profiles"][0]["sample_interval_s"] = 2
        sample = {"received_elapsed_s": .1, "engine_read": SAMPLE["read"], "engine_preread": None,
                  "degraded": False, "raw": SAMPLE}
        summary, page, _ = self.build(data, StreamingDocker(RecordedStream([sample])))
        rows = {row["profile"]: row for row in summary["fidelity"]["profiles"]}
        self.assertEqual(rows["tight"]["telemetry_sources"], ["engine_stream"])
        self.assertEqual(rows["tight"]["samples_received"], 1)
        self.assertEqual(rows["tight"]["samples_retained"], 1)
        self.assertEqual(rows["tight"]["truncated_trials"], 0)
        self.assertEqual(rows["tight"]["leaked_reader_threads"], 0)
        self.assertIsNone(rows["roomy"]["samples_received"])
        recorded = [t for t in summary["fidelity"]["trials"] if t["profile"] == "tight"]
        self.assertEqual(recorded[0]["throttled_periods_delta"], 7)
        self.assertEqual(recorded[0]["telemetry_source"], "engine_stream")
        self.assertIn("engine_stream", page)
        self.assertIn("not peaks", page)

    def test_snapshot_telemetry_reports_counts_without_inventing_deltas(self):
        data = config()
        data["repeats"], data["tasks"] = 1, data["tasks"][:1]
        data["profiles"][0]["sample_interval_s"] = 2
        summary, page, _ = self.build(data, FakeDocker(telemetry_source="cli_snapshot"))
        rows = {row["profile"]: row for row in summary["fidelity"]["profiles"]}
        self.assertEqual(rows["tight"]["telemetry_sources"], ["cli_snapshot"])
        self.assertGreaterEqual(rows["tight"]["samples_received"], 1)
        self.assertEqual(rows["tight"]["leaked_reader_threads"], 0)
        self.assertEqual(rows["tight"]["truncated_trials"], 0)
        recorded = [t for t in summary["fidelity"]["trials"] if t["profile"] == "tight"]
        self.assertIsNone(recorded[0]["throttled_periods_delta"],
                          "the snapshot path must not invent engine counter deltas")
        self.assertIsNone(recorded[0]["cpu_total_ns_delta"])
        self.assertIn("cli_snapshot", page)
        self.assertIn("Not measured", page)

    def test_m0_and_m1_manifests_without_m2_fields_still_report(self):
        summary, _, manifest = self.build()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "legacy"
            (directory / "trials").mkdir(parents=True)
            legacy = {k: v for k, v in manifest.items()
                      if k not in ("engine_identity_final", "coordination")}
            legacy["environment"] = {"engine": {"NCPU": 4}}
            legacy["config"] = {k: v for k, v in legacy["config"].items()}
            for profile in legacy["config"]["profiles"]:
                profile.pop("cpuset_cpus", None)
                profile.pop("sample_interval_s", None)
            write_json(directory / "manifest.json", legacy)
            for batch in legacy["plan"]:
                for planned in batch["trials"]:
                    write_json(directory / "trials" / f"{planned['id']}.json",
                               {"id": planned["id"], "task": planned["task"],
                                "profile": batch["profile"], "repeat": batch["repeat"],
                                "status": "passed", "container_duration_s": 1.0,
                                "container_name": f"evalnoise-{legacy['run_id']}-{planned['id']}",
                                "logs": {"text": "legacy"},
                                "contract_sha256": legacy["task_contracts"][planned["task"]]})
            rebuilt = generate(directory)
            page = (directory / "report.html").read_text()
        self.assertEqual(rebuilt["recorded_trials"], 18)
        self.assertIsNone(rebuilt["fidelity"]["engine_identity_warning"])
        self.assertIsNone(rebuilt["fidelity"]["profiles"][0]["samples_received"])
        self.assertEqual(rebuilt["fidelity"]["trials"][0]["telemetry_source"], "not_recorded")
        self.assertIn("Measurement fidelity", page)


class ProbeEvaluationTests(unittest.TestCase):
    def profile(self, **kwargs):
        return Profile("standard", kwargs.pop("cpus", 1), kwargs.pop("memory_mb", 128), 10, **kwargs)

    def payload(self, profile, **overrides):
        files = dict(expectations(profile), cpuset_cpus="", cpuset_cpus_effective="0-3", cpu_stat="nr_periods 1")
        files.update(overrides)
        return {"cgroup_v2_unified": True, "online_cpus": 4, "files": files, "inconclusive_reason": None}

    def test_matching_limits_are_conclusive(self):
        profile = self.profile()
        result = evaluate(self.payload(profile), profile)
        self.assertTrue(result["conclusive"])
        self.assertTrue(result["enforced_as_requested"])
        self.assertEqual(result["matches"]["cpu_max"]["observed"], "100000 100000")

    def test_deliberately_unsupported_limits_are_caught(self):
        profile = self.profile()
        for key, value in (("memory_max", "max"), ("memory_swap_max", "max"),
                           ("pids_max", "max"), ("cpu_max", "max 100000")):
            result = evaluate(self.payload(profile, **{key: value}), profile)
            self.assertIn(key, result["mismatches"])
            self.assertFalse(result["enforced_as_requested"])

    def test_missing_cgroup_v2_is_inconclusive_not_failed(self):
        result = evaluate({"cgroup_v2_unified": False, "inconclusive_reason": "no unified hierarchy"},
                          self.profile())
        self.assertFalse(result["conclusive"])
        self.assertEqual(result["mismatches"], [])
        self.assertIn("no unified hierarchy", result["reason"])

    def test_effective_affinity_is_compared_not_assumed(self):
        profile = self.profile(cpuset_cpus="0-1")
        exact = evaluate(self.payload(profile, cpuset_cpus="0-1", cpuset_cpus_effective="0-1"), profile)
        self.assertTrue(exact["affinity"]["effective_is_requested"])
        self.assertTrue(exact["enforced_as_requested"])
        narrowed = evaluate(self.payload(profile, cpuset_cpus="0-1", cpuset_cpus_effective="0"), profile)
        self.assertFalse(narrowed["affinity"]["effective_is_requested"])
        self.assertTrue(narrowed["affinity"]["effective_is_subset"])
        self.assertIn("cpuset_cpus_effective", narrowed["mismatches"])

    def test_probe_claim_denies_a_reservation(self):
        self.assertIn("does not prove dedicated CPUs", CLAIM)


if __name__ == "__main__":
    unittest.main()
