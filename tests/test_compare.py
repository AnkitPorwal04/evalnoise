"""Paired comparison: compatibility refusal, denominators, clustering, and reports."""

import inspect
import json
from pathlib import Path
import tempfile
import unittest

from evalnoise import compare as compare_module
from evalnoise.compare import (CompareError, INFRASTRUCTURE_STATUSES, UNRESOLVED_STATUSES,
                               WITHHELD, check_engine_stability, compare, engine_facts,
                               render, write)
from evalnoise.config import parse, plan
from evalnoise.storage import write_json
from evalnoise.verification import contract_hash

IMAGE = "evalnoise-workloads:local"
IMAGES = {IMAGE: {"id": "sha256:" + "a" * 64, "architecture": "arm64"}}


def raw_config(tasks=6, repeats=3, profiles=None, name="contrast-fixture"):
    return {
        "schema_version": 1, "name": name, "seed": 7, "repeats": repeats,
        "tasks": [{"id": f"task-{index:02d}", "image": IMAGE, "command": ["python", "-c", "pass"]}
                  for index in range(tasks)],
        "profiles": profiles or [
            {"id": "baseline", "cpus": 1, "memory_mb": 256, "timeout_s": 10},
            {"id": "candidate", "cpus": 1, "memory_mb": 512, "timeout_s": 10}],
    }


def build_run(directory, raw, outcome, *, run_id="fixture01", status="completed",
              contracts=True, images=None, engine="daemon-a", version="0.5.0",
              engine_identity=NotImplemented, stable=True, finite=True):
    """Write a run directory whose evidence passes the existing report validation.

    The `engine_identity` keys here are deliberately the lowercase ones that
    `evalnoise/docker.py` actually writes (`id`, `name`, `server_version`,
    `cgroup_version`, `ncpu`). An earlier fixture used Docker-API casing, which
    matched a bug in the reader and made the engine check pass vacuously.
    """
    directory = Path(directory)
    (directory / "trials").mkdir(parents=True, exist_ok=True)
    experiment = parse(raw)
    images = images or IMAGES
    if engine_identity is NotImplemented:
        engine_identity = {"id": engine, "name": "docker-desktop",
                           "server_version": "29.1.5", "cgroup_version": "2", "ncpu": 12}
    manifest = {
        "run_id": run_id, "status": status, "schema_version": 1,
        "evalnoise_version": version, "measurement_kind": "container_lifecycle",
        "config": experiment.data(), "plan": plan(experiment), "images": images,
        "started_at": "2026-09-13T00:00:00Z", "finished_at": "2026-09-13T00:01:00Z",
        "environment": {"engine_identity": engine_identity},
        "engine_identity_final": {"stable": stable, "expected": engine,
                                  "observed": engine if stable else "daemon-other",
                                  "error": None, "note": "fixture"},
    }
    if contracts:
        manifest["task_contracts"] = {task["id"]: contract_hash(task, images, None)
                                      for task in manifest["config"]["tasks"]}
    recorded = 0
    for batch in manifest["plan"]:
        for specification in batch["trials"]:
            result = outcome(specification["task"], batch["repeat"], batch["profile"])
            if result is None:
                continue
            state, duration = result if isinstance(result, tuple) else (result, 1.0)
            record = {**specification, "profile": batch["profile"], "repeat": batch["repeat"],
                      "status": state, "execution_status": state,
                      "container_duration_s": duration, "lifecycle_s": None if duration is None else duration + 0.1,
                      "container_name": "evalnoise-" + specification["id"], "schema_version": 1}
            if contracts:
                record["contract_sha256"] = manifest["task_contracts"][specification["task"]]
            path = directory / "trials" / (specification["id"] + ".json")
            if finite:
                write_json(path, record)
            else:
                # write_json refuses non-finite numbers by design, so a NaN fixture has to
                # bypass it to reach the reader under test.
                path.write_text(json.dumps(record, allow_nan=True))
            recorded += 1
    manifest["planned_trials"] = sum(len(batch["trials"]) for batch in manifest["plan"])
    manifest["recorded_trials"] = recorded
    write_json(directory / "manifest.json", manifest)
    return directory


def all_pass(task, repeat, profile):
    return "passed"


class CompatibilityTests(unittest.TestCase):
    def test_identical_arms_report_zero_with_an_aa_warning(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(profiles=[
                {"id": "baseline", "cpus": 1, "memory_mb": 256, "timeout_s": 10},
                {"id": "candidate", "cpus": 1, "memory_mb": 256, "timeout_s": 10}]), all_pass)
            result = compare(run, "baseline", run, "candidate")
            self.assertEqual(result["contrast"]["scope"], "within_run")
            self.assertIsNone(result["design"]["varied"])
            self.assertEqual(result["estimates"][0]["point"], 0.0)
            self.assertEqual(result["estimates"][0]["evidence"], WITHHELD)
            self.assertIsNone(result["estimates"][0]["bootstrap"])
            self.assertTrue(any("A/A contrast" in text for text in result["warnings"]))

    def test_an_undeclared_difference_is_refused_and_naming_it_succeeds(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate")
            self.assertIn("--treatment memory_mb", str(caught.exception))
            self.assertIn("confounder", str(caught.exception))
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["design"]["varied"], ["memory_mb"])

    def test_identity_fields_are_never_declarable_as_a_treatment(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["task_contracts"])
            self.assertIn("Unknown treatment key", str(caught.exception))

    def test_a_differing_task_set_is_fatal(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(tasks=6), all_pass, run_id="aaa")
            right = build_run(Path(workspace) / "b", raw_config(tasks=5), all_pass, run_id="bbb")
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("task_set", str(caught.exception))

    def test_a_differing_image_id_is_fatal(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa")
            right = build_run(Path(workspace) / "b", raw_config(), all_pass, run_id="bbb",
                              images={IMAGE: {"id": "sha256:" + "b" * 64, "architecture": "arm64"}})
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("task_contracts", str(caught.exception))

    def test_cross_run_refuses_artifacts_without_contract_hashes(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa", contracts=False)
            right = build_run(Path(workspace) / "b", raw_config(), all_pass, run_id="bbb", contracts=False)
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("per-task contract hashes", str(caught.exception))
            self.assertIn("will not be guessed", str(caught.exception))

    def test_within_run_accepts_pre_contract_artifacts_with_a_weaker_attestation(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass, contracts=False)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["compatibility"]["contract_attestation"], "manifest_structural")
            self.assertIn("predate per-trial contract hashes",
                          result["compatibility"]["attestation_note"])

    def test_cross_run_engine_difference_must_be_declared(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa", engine="daemon-a")
            right = build_run(Path(workspace) / "b", raw_config(), all_pass, run_id="bbb", engine="daemon-b")
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("engine_daemon_id", str(caught.exception))
            result = compare(left, "baseline", right, "candidate",
                             treatment=["memory_mb", "engine_daemon_id"])
            self.assertEqual(result["compatibility"]["contract_attestation"], "per_trial_contract_sha256")
            self.assertTrue(any("differ at once" in text for text in result["warnings"]))

    def test_a_profile_cannot_be_contrasted_with_itself(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            with self.assertRaises(CompareError):
                compare(run, "baseline", run, "baseline")

    def test_an_absent_profile_lists_what_the_run_declares(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "nonexistent")
            self.assertIn("'baseline', 'candidate'", str(caught.exception))


class EngineIdentityTests(unittest.TestCase):
    """The engine schema is the one docker.py writes, and it fails closed."""

    def cross(self, workspace, **right):
        left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa")
        right_run = build_run(Path(workspace) / "b", raw_config(), all_pass, run_id="bbb", **right)
        return left, right_run

    def test_a_differing_daemon_is_detected_with_the_real_lowercase_schema(self):
        with tempfile.TemporaryDirectory() as workspace:
            left, right = self.cross(workspace, engine="daemon-b")
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("engine_daemon_id", str(caught.exception))

    def test_the_reader_uses_the_keys_docker_actually_writes(self):
        manifest = {"environment": {"engine_identity": {
            "id": "d", "name": "n", "server_version": "29.1.5",
            "cgroup_version": "2", "ncpu": 12}},
            "engine_identity_final": {"stable": True}}
        self.assertEqual(engine_facts(manifest, "r"), {
            "engine_daemon_id": "d", "engine_server_version": "29.1.5",
            "engine_cgroup_version": "2", "engine_ncpu": 12})

    def test_docker_api_casing_is_not_accepted_as_engine_identity(self):
        manifest = {"environment": {"engine_identity": {
            "ID": "d", "ServerVersion": "29.1.5", "CgroupVersion": "2", "CPUs": 12}},
            "engine_identity_final": {"stable": True}}
        with self.assertRaises(CompareError) as caught:
            engine_facts(manifest, "r")
        self.assertIn("'id'", str(caught.exception))

    def test_absent_engine_identity_refuses_a_cross_run_contrast(self):
        with tempfile.TemporaryDirectory() as workspace:
            left, right = self.cross(workspace, engine_identity=None)
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("no environment.engine_identity", str(caught.exception))

    def test_an_incomplete_engine_identity_refuses_rather_than_reading_none(self):
        with tempfile.TemporaryDirectory() as workspace:
            left, right = self.cross(workspace, engine_identity={"id": "daemon-a", "ncpu": 12})
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("without 'server_version'", str(caught.exception))

    def test_a_null_engine_field_is_refused_rather_than_compared_as_none(self):
        manifest = {"environment": {"engine_identity": {
            "id": "d", "server_version": None, "cgroup_version": "2", "ncpu": 12}},
            "engine_identity_final": {"stable": True}}
        with self.assertRaises(CompareError):
            engine_facts(manifest, "r")

    def test_an_unstable_engine_refuses_the_contrast(self):
        with tempfile.TemporaryDirectory() as workspace:
            left, right = self.cross(workspace, stable=False)
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("is not True", str(caught.exception))

    def test_a_missing_final_stability_record_refuses_the_contrast(self):
        manifest = {"environment": {"engine_identity": {
            "id": "d", "server_version": "29.1.5", "cgroup_version": "2", "ncpu": 12}}}
        with self.assertRaises(CompareError) as caught:
            engine_facts(manifest, "r")
        self.assertIn("no engine_identity_final", str(caught.exception))

    def test_engine_identity_is_irrelevant_to_a_within_run_contrast(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass, engine_identity=None)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["contrast"]["scope"], "within_run")

    def test_a_within_run_contrast_also_refuses_an_explicitly_unstable_engine(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass, stable=False)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("is not True", str(caught.exception))

    def test_a_truthy_non_boolean_stable_flag_is_refused_in_both_scopes(self):
        for flag in ("no", "false", 1, [1]):
            manifest = {"environment": {"engine_identity": {
                "id": "d", "server_version": "29.1.5", "cgroup_version": "2", "ncpu": 12}},
                "engine_identity_final": {"stable": flag}}
            with self.assertRaises(CompareError) as caught:
                engine_facts(manifest, "r")
            self.assertIn("is not True", str(caught.exception))
            with self.assertRaises(CompareError):
                check_engine_stability(manifest, "r", required=False)

    def test_an_absent_stability_record_is_unknown_within_a_run_not_unstable(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            manifest = json.loads((run / "manifest.json").read_text())
            del manifest["engine_identity_final"]
            write_json(run / "manifest.json", manifest)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertTrue(any("is unknown rather than confirmed" in text
                                for text in result["warnings"]))


class SeedIntegrityTests(unittest.TestCase):
    """A repetition index is a label; the seed is what determines the workload."""

    def test_a_differing_configured_seed_is_fatal_and_not_declarable(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa")
            other = raw_config()
            other["seed"] = 99
            right = build_run(Path(workspace) / "b", other, all_pass, run_id="bbb")
            with self.assertRaises(CompareError) as caught:
                compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            self.assertIn("configured_seed", str(caught.exception))
            self.assertIn("not declarable", str(caught.exception))

    def test_seed_cannot_be_declared_as_a_treatment_key(self):
        self.assertNotIn("seed", compare_module.TREATMENT_KEYS)
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb", "seed"])
            self.assertIn("Unknown treatment key", str(caught.exception))

    def test_a_trial_seed_that_contradicts_the_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            path = next((run / "trials").glob("*.json"))
            record = json.loads(path.read_text())
            record["seed"] = record["seed"] + 1000
            write_json(path, record)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("its plan assigns", str(caught.exception))

    def test_a_pair_whose_arms_disagree_on_seed_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            manifest = json.loads((run / "manifest.json").read_text())
            for batch in manifest["plan"]:
                if batch["profile"] == "candidate":
                    for specification in batch["trials"]:
                        specification["seed"] += 7
            write_json(run / "manifest.json", manifest)
            for path in (run / "trials").glob("*.json"):
                record = json.loads(path.read_text())
                if record["profile"] == "candidate":
                    record["seed"] += 7
                    write_json(path, record)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("must share its repetition seed", str(caught.exception))

    def test_the_artifact_records_that_seeds_were_checked(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("matched on its seed", result["design"]["seed_note"])


class ScopeLanguageTests(unittest.TestCase):
    def test_a_within_run_contrast_claims_one_host(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["design"]["host_scope"], "one run on one host")
            self.assertIn("schedule block", result["design"]["dependence_note"])

    def test_a_cross_run_contrast_does_not_claim_a_shared_schedule_block(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass, run_id="aaa")
            right = build_run(Path(workspace) / "b", raw_config(), all_pass, run_id="bbb")
            result = compare(left, "baseline", right, "candidate", treatment=["memory_mb"])
            note = result["design"]["dependence_note"]
            self.assertIn("do NOT share a schedule block", note)
            self.assertIn("separate runs", result["design"]["host_scope"])
            self.assertTrue(any("share no schedule block" in text
                                for text in result["warnings"]))

    def test_a_declared_engine_change_withdraws_the_one_host_claim(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(), all_pass,
                             run_id="aaa", engine="daemon-a")
            right = build_run(Path(workspace) / "b", raw_config(), all_pass,
                              run_id="bbb", engine="daemon-b")
            result = compare(left, "baseline", right, "candidate",
                             treatment=["memory_mb", "engine_daemon_id"])
            self.assertIn("did NOT run on one host", result["design"]["host_scope"])
            self.assertNotIn("on one host.", result["interpretation"])
            self.assertIn("two different engines", result["interpretation"])
            self.assertTrue(any("confounds the resource setting" in text
                                for text in result["warnings"]))
            self.assertNotIn("one host", render(result).replace("did NOT run on one host", ""))


class InputValidationTests(unittest.TestCase):
    def test_an_unknown_trial_status_is_refused_not_scored_as_a_non_pass(self):
        def outcome(task, repeat, profile):
            if task == "task-00" and profile == "baseline":
                return ("quantum_superposition", 1.0)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), outcome)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("quantum_superposition", str(caught.exception))
            self.assertIn("does not classify", str(caught.exception))

    def test_a_nan_duration_is_refused_rather_than_averaged(self):
        def outcome(task, repeat, profile):
            if task == "task-00" and profile == "candidate":
                return ("passed", float("nan"))
            return ("passed", 1.0)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), outcome, finite=False)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("non-finite", str(caught.exception))

    def test_mixed_type_unknown_statuses_raise_a_controlled_error_not_a_typeerror(self):
        def outcome(task, repeat, profile):
            index = int(task.split("-")[1])
            if profile == "baseline" and index == 0:
                return (None, 1.0)
            if profile == "baseline" and index == 1:
                return (7, 1.0)
            if profile == "baseline" and index == 2:
                return ("not_a_status", 1.0)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), outcome)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            message = str(caught.exception)
            self.assertIn("does not classify", message)
            for shown in ("None", "7", "not_a_status"):
                self.assertIn(shown, message)

    def test_an_empty_duration_summary_names_the_duration_eligibility(self):
        def outcome(task, repeat, profile):
            return ("passed", None)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["estimates"][0]["evidence"], WITHHELD)
            duration = result["estimates"][2]
            self.assertEqual(duration["evidence"], "no_jointly_passing_duration_pairs")
            self.assertIn("both recorded a container duration", duration["evidence_detail"])
            self.assertIsNone(duration["point"])
            self.assertEqual(duration["excluded_from_duration"], {"no_container_duration": 18})

    def test_an_infinite_duration_is_refused(self):
        def outcome(task, repeat, profile):
            if task == "task-01" and profile == "baseline":
                return ("passed", float("inf"))
            return ("passed", 1.0)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), outcome, finite=False)
            with self.assertRaises(CompareError):
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])


class MalformedArtifactTests(unittest.TestCase):
    def test_a_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaises(CompareError) as caught:
                compare(workspace, "baseline", workspace, "candidate")
            self.assertIn("not a run directory", str(caught.exception))

    def test_unreadable_json_is_refused_rather_than_skipped(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            (run / "manifest.json").write_text("{not json")
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate")
            self.assertIn("Unreadable manifest", str(caught.exception))

    def test_a_corrupt_trial_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            next((run / "trials").glob("*.json")).write_text("{{{")
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate")
            self.assertIn("Unreadable trial artifact", str(caught.exception))

    def test_evidence_the_reporter_rejects_is_not_comparable(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            path = next((run / "trials").glob("*.json"))
            record = json.loads(path.read_text())
            record["task"] = "task-99"
            write_json(path, record)
            with self.assertRaises(CompareError) as caught:
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertIn("fails the existing report validation", str(caught.exception))

    def test_a_mutated_contract_hash_is_refused(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            manifest = json.loads((run / "manifest.json").read_text())
            manifest["task_contracts"]["task-00"] = "0" * 64
            write_json(run / "manifest.json", manifest)
            with self.assertRaises(CompareError):
                compare(run, "baseline", run, "candidate", treatment=["memory_mb"])


class ClusteringTests(unittest.TestCase):
    def test_repeated_attempts_are_replicates_not_independent_clusters(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=5), all_pass)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["coverage"]["planned_pairs"], 30)
            self.assertEqual(result["coverage"]["complete_pairs"], 30)
            for record in result["estimates"][:2]:
                self.assertEqual(record["tasks_contributing"], 6)
                self.assertIsNone(record["bootstrap"])
                self.assertEqual(record["tasks_total"], 6)
            self.assertEqual(result["design"]["repetitions_per_task"], 5)
            self.assertIn("averaged within the task first", result["design"]["dependence_note"])

    def test_a_known_change_is_recovered_at_the_cluster_level(self):
        def outcome(task, repeat, profile):
            if profile == "baseline" and task in ("task-00", "task-01", "task-02"):
                return ("oom_killed", None)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=3), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            success = result["estimates"][0]
            self.assertAlmostEqual(success["point"], 0.5)
            self.assertEqual(result["descriptives"]["fail_to_pass"], 9)
            self.assertEqual(result["descriptives"]["pass_to_fail"], 0)
            self.assertEqual(success["evidence"], WITHHELD)
            self.assertIsNone(success["bootstrap"])

    def test_every_estimand_withholds_its_interval_at_any_task_count(self):
        for tasks in (1, 3, 6, 40):
            with tempfile.TemporaryDirectory() as workspace:
                run = build_run(Path(workspace) / "run", raw_config(tasks=tasks, repeats=2), all_pass)
                result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
                self.assertFalse(result["uncertainty"]["published"])
                self.assertEqual(result["uncertainty"]["status"], WITHHELD)
                for record in result["estimates"]:
                    self.assertIsNone(record["bootstrap"], tasks)
                    self.assertEqual(record["evidence"], WITHHELD)
                    self.assertNotIn("evidence_policy", record)

    def test_the_comparison_api_exposes_no_uncertainty_knobs(self):
        names = set(inspect.signature(compare).parameters)
        for banned in ("confidence", "resamples", "seed", "min_clusters", "alpha"):
            self.assertNotIn(banned, names)

    def test_the_comparison_module_does_not_import_the_experimental_resampler(self):
        source = Path(compare_module.__file__).read_text()
        self.assertNotIn("resample", source)
        self.assertFalse(hasattr(compare_module, "cluster_bootstrap"))

    def test_results_are_reproducible_because_nothing_is_random(self):
        def outcome(task, repeat, profile):
            index = int(task.split("-")[1])
            if profile == "baseline" and repeat < index % 3:
                return ("workload_failed", 1.0)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=8, repeats=3), outcome)
            first = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            second = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(first["estimates"], second["estimates"])
            self.assertEqual(first["coverage"], second["coverage"])


class CoverageAndMissingnessTests(unittest.TestCase):
    def test_missing_trials_are_excluded_and_never_read_as_improvement(self):
        def outcome(task, repeat, profile):
            if profile == "baseline" and task in ("task-00", "task-01"):
                return None
            return "passed" if profile == "candidate" else ("workload_failed", 1.0)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=3), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            reach = result["coverage"]
            self.assertEqual(reach["planned_pairs"], 18)
            self.assertEqual(reach["complete_pairs"], 12)
            self.assertEqual(reach["exclusions"], {"missing_baseline": 6})
            self.assertEqual(reach["candidate_only_records"], 6)
            self.assertTrue(reach["differential_missingness"])
            self.assertTrue(any("differential by pair identity" in text
                                for text in result["warnings"]))
            self.assertEqual(result["estimates"][0]["tasks_contributing"], 4)
            for entry in result["estimates"][0]["cluster_detail"]:
                if entry["task"] in ("task-00", "task-01"):
                    self.assertIsNone(entry["value"])
                    self.assertEqual(entry["included_pairs"], 0)

    def test_equal_loss_counts_on_different_cells_are_still_differential(self):
        """The bug this guards: aggregate counts call this balanced. It is not."""
        def outcome(task, repeat, profile):
            if profile == "baseline" and task == "task-00":
                return None
            if profile == "candidate" and task == "task-01":
                return None
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=3), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            reach = result["coverage"]
            self.assertEqual(reach["baseline_only_records"], 3)
            self.assertEqual(reach["candidate_only_records"], 3)
            self.assertTrue(reach["loss_counts_balanced"])
            self.assertTrue(reach["differential_missingness"])
            self.assertEqual([entry["task"] for entry in reach["pairs_lost_in_baseline_only"]],
                             ["task-00"] * 3)
            self.assertEqual([entry["task"] for entry in reach["pairs_lost_in_candidate_only"]],
                             ["task-01"] * 3)
            self.assertTrue(any("SAME NUMBER" in text for text in result["warnings"]))

    def test_one_sided_unresolved_status_counts_as_differential_loss(self):
        def outcome(task, repeat, profile):
            if profile == "baseline" and task == "task-02":
                return ("cancelled", None)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=2), outcome)
            reach = compare(run, "baseline", run, "candidate",
                            treatment=["memory_mb"])["coverage"]
            self.assertFalse(reach["loss_counts_balanced"])
            self.assertTrue(reach["differential_missingness"])
            self.assertEqual(len(reach["pairs_lost_in_baseline_only"]), 2)
            self.assertEqual(reach["pairs_lost_in_candidate_only"], [])

    def test_loss_on_the_same_cells_in_both_arms_is_not_differential(self):
        def outcome(task, repeat, profile):
            return None if task == "task-00" else "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=2), outcome)
            reach = compare(run, "baseline", run, "candidate",
                            treatment=["memory_mb"])["coverage"]
            self.assertTrue(reach["loss_counts_balanced"])
            self.assertFalse(reach["differential_missingness"])
            self.assertEqual(reach["pairs_lost_both_arms"], 2)

    def test_each_unresolved_category_is_distinguished_and_excluded(self):
        statuses = sorted(UNRESOLVED_STATUSES)

        def outcome(task, repeat, profile):
            index = int(task.split("-")[1])
            if profile == "baseline" and index < len(statuses):
                return (statuses[index], None)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=8, repeats=1), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["coverage"]["complete_pairs"], 8 - len(statuses))
            reasons = result["coverage"]["unresolved_reasons"]
            self.assertEqual(sorted(reasons), statuses)
            for status in statuses:
                self.assertTrue(any(status in text for text in result["warnings"]))
            self.assertEqual(result["coverage"]["exclusions"],
                             {"unresolved_baseline": len(statuses)})

    def test_infrastructure_errors_are_separated_from_verification_failures(self):
        def outcome(task, repeat, profile):
            index = int(task.split("-")[1])
            if profile == "baseline":
                return ("runtime_error", None) if index < 3 else ("verification_failed", 1.0)
            return "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=2), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["coverage"]["complete_pairs"], 12)
            described = result["descriptives"]
            self.assertEqual(described["baseline_infrastructure_errors"], 6)
            self.assertEqual(described["candidate_infrastructure_errors"], 0)
            categories = result["coverage"]["per_arm"]["baseline"]["categories"]
            self.assertEqual(categories, {"infrastructure_error": 6, "verification_failed": 6})
            infrastructure = result["estimates"][1]
            self.assertAlmostEqual(infrastructure["point"], -0.5)
            self.assertIsNone(infrastructure["bootstrap"])

    def test_every_infrastructure_status_scores_as_an_infrastructure_error(self):
        statuses = sorted(INFRASTRUCTURE_STATUSES)

        def outcome(task, repeat, profile):
            index = int(task.split("-")[1])
            return (statuses[index], None) if profile == "baseline" else "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=len(statuses), repeats=1), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["descriptives"]["baseline_infrastructure_errors"], len(statuses))
            self.assertEqual(result["estimates"][1]["point"], -1.0)

    def test_no_complete_pair_leaves_the_estimate_undefined_rather_than_zero(self):
        def outcome(task, repeat, profile):
            return None if profile == "baseline" else "passed"

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertEqual(result["coverage"]["complete_pairs"], 0)
            for record in result["estimates"][:2]:
                self.assertIsNone(record["point"])
                self.assertEqual(record["evidence"], "no_jointly_resolved_pairs")
                self.assertIn("rather than as zero", record["evidence_detail"])
            duration = result["estimates"][2]
            self.assertIsNone(duration["point"])
            self.assertEqual(duration["evidence"], "no_jointly_passing_duration_pairs")

    def test_unequal_repetition_counts_are_disclosed_not_silently_truncated(self):
        with tempfile.TemporaryDirectory() as workspace:
            left = build_run(Path(workspace) / "a", raw_config(tasks=6, repeats=2), all_pass, run_id="aaa")
            right = build_run(Path(workspace) / "b", raw_config(tasks=6, repeats=5), all_pass, run_id="bbb")
            result = compare(left, "baseline", right, "candidate",
                             treatment=["memory_mb", "repeats"])
            self.assertEqual(result["design"]["repetitions_per_task"], 2)
            self.assertEqual(result["coverage"]["planned_pairs"], 12)
            self.assertTrue(any("outside the paired denominator" in text
                                for text in result["warnings"]))

    def test_an_interrupted_run_is_flagged_as_systematic_missingness(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass, status="interrupted")
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            self.assertTrue(any("not 'completed'" in text for text in result["warnings"]))


class DurationTests(unittest.TestCase):
    def test_duration_is_conditional_on_both_arms_passing(self):
        def outcome(task, repeat, profile):
            if profile == "baseline" and task in ("task-00", "task-01"):
                return ("oom_killed", None)
            return ("passed", 2.0 if profile == "baseline" else 1.5)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=3), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            duration = result["estimates"][2]
            self.assertEqual(duration["estimand"], "jointly_passing_duration_task_weighted")
            self.assertEqual(duration["included_pairs"], 12)
            self.assertEqual(duration["excluded_from_duration"], {"not_both_passed": 6})
            self.assertEqual(duration["tasks_contributing"], 4)
            self.assertAlmostEqual(duration["point"], -0.5)
            self.assertIn("not a speedup of the workload in general", duration["definition"])

    def test_a_trial_without_a_container_duration_is_excluded_not_zeroed(self):
        def outcome(task, repeat, profile):
            duration = None if task == "task-00" else 1.0
            return ("passed", duration)

        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=6, repeats=2), outcome)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            duration = result["estimates"][2]
            self.assertEqual(duration["excluded_from_duration"], {"no_container_duration": 2})
            self.assertEqual(duration["tasks_contributing"], 5)
            self.assertEqual(duration["cluster_detail"][0]["task"], "task-00")
            self.assertIsNone(duration["cluster_detail"][0]["value"])


class ReportTests(unittest.TestCase):
    def test_hostile_values_are_escaped_in_the_html(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            manifest = json.loads((run / "manifest.json").read_text())
            manifest["run_id"] = "<script>alert(1)</script>"
            manifest["config"]["profiles"][0]["cpuset_cpus"] = "<img src=x onerror=bad()>"
            write_json(run / "manifest.json", manifest)
            result = compare(run, "baseline", run, "candidate",
                             treatment=["memory_mb", "cpuset_cpus"])
            page = render(result)
            self.assertNotIn("<script>alert", page)
            self.assertNotIn("<img src=x", page)
            self.assertIn("&lt;script&gt;alert", page)
            self.assertIn("&lt;img src=x", page)

    def test_the_page_is_offline_with_no_script_or_external_reference(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(), all_pass)
            page = render(compare(run, "baseline", run, "candidate", treatment=["memory_mb"]))
            self.assertIn("Content-Security-Policy", page)
            self.assertIn("default-src 'none'", page)
            for forbidden in ("<script", "http://", "https://", "//cdn", "@import", "url("):
                self.assertNotIn(forbidden, page)

    def test_no_interval_or_confidence_language_is_rendered_anywhere(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(profiles=[
                {"id": "baseline", "cpus": 1, "memory_mb": 256, "timeout_s": 10},
                {"id": "candidate", "cpus": 1, "memory_mb": 256, "timeout_s": 10}]), all_pass)
            page = render(compare(run, "baseline", run, "candidate"))
            self.assertIn("No uncertainty published", page)
            self.assertIn(WITHHELD, page)
            lowered = page.lower()
            for banned in ("95% interval", "90% interval", "99% interval", "± ",
                           "margin of error", "interval reported", "interval:"):
                self.assertNotIn(banned, lowered)
            for disclaimer in ("no uncertainty interval, no standard error",
                               "carry no error bar"):
                self.assertIn(disclaimer, lowered)

    def test_artifacts_are_written_with_a_reproducible_method_record(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=8), all_pass)
            result = compare(run, "baseline", run, "candidate", treatment=["memory_mb"])
            out = Path(workspace) / "out"
            paths = write(result, out)
            self.assertEqual(sorted(path.name for path in out.iterdir()),
                             ["comparison.csv", "comparison.html", "comparison.json"])
            stored = json.loads((out / "comparison.json").read_text())
            self.assertEqual(stored["artifact_type"], "paired_comparison")
            self.assertFalse(stored["uncertainty"]["published"])
            rows = (out / "comparison.csv").read_text().splitlines()
            self.assertEqual(rows[0].split(",")[0], "estimand")
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(Path(path).is_file() for path in paths.values()))

    def test_no_affirmative_significance_or_causal_claim_reaches_an_artifact(self):
        with tempfile.TemporaryDirectory() as workspace:
            run = build_run(Path(workspace) / "run", raw_config(tasks=8), all_pass)
            page = render(compare(run, "baseline", run, "candidate", treatment=["memory_mb"])).lower()
            for claim in ("is statistically significant", "statistically significant improvement",
                          "significant difference", "p-value of", "p &lt; 0", "p = 0",
                          "proves that", "was caused by", "establishes a speedup",
                          "% faster", "% slower", "confidence interval for the population"):
                self.assertNotIn(claim, page)
            for disclaimer in ("no p-value", "no causal attribution", "not a significance test",
                               "averaged within the task first", "never counted as independent",
                               "not a speedup of the workload in general",
                               "not a random sample from a population"):
                self.assertIn(disclaimer, page)


if __name__ == "__main__":
    unittest.main()
