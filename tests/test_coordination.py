"""Advisory per-engine coordination and read-only diagnosis / owned-only cleanup."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from evalnoise.config import parse
from evalnoise.coordination import (CoordinationError, EngineLock, SCOPE, engine_containers,
                                    lock_path, live_holder, refuse_orphans)
from evalnoise.docker import DockerError
from evalnoise.recovery import (RecoveryError, cleanup, diagnose, expected_names, load_manifest,
                                stage_image, survey)
from evalnoise.runner import execute
from test_core import FakeDocker, config

ROOT = Path(__file__).resolve().parents[1]

HOLDER = """
import json, sys, time
sys.path.insert(0, {root!r})
import os
os.environ["EVALNOISE_STATE_DIR"] = {state!r}
from evalnoise.coordination import EngineLock
lock = EngineLock({engine!r}).acquire("aaaaaaaaaaaa", "/tmp")
print("held", flush=True)
time.sleep(30)
"""


class EngineLockTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="evalnoise-lock-")
        os.environ["EVALNOISE_STATE_DIR"] = self.state
        self.engine = uuid.uuid4().hex

    def test_lock_lives_outside_any_output_directory(self):
        path = lock_path(self.engine)
        self.assertEqual(path.parent, Path(self.state))
        self.assertNotIn(self.engine, str(path))

    def test_same_engine_is_exclusive_across_different_output_directories(self):
        first = EngineLock(self.engine).acquire("run-a", "/tmp/output-a")
        try:
            with self.assertRaises(CoordinationError) as raised:
                EngineLock(self.engine).acquire("run-b", "/tmp/output-b")
            self.assertIn("run-a", str(raised.exception))
            self.assertIn("Not a distributed lock", str(raised.exception))
        finally:
            first.release()
        EngineLock(self.engine).acquire("run-c", "/tmp/output-c").release()

    def test_different_engines_do_not_exclude_each_other(self):
        first = EngineLock(self.engine).acquire("a", "/tmp")
        second = EngineLock(uuid.uuid4().hex).acquire("b", "/tmp")
        self.assertTrue(first.held and second.held)
        first.release()
        second.release()

    def test_missing_daemon_id_is_refused(self):
        for value in (None, ""):
            with self.assertRaises(CoordinationError):
                EngineLock(value)

    def test_lock_releases_when_the_holding_process_dies(self):
        script = HOLDER.format(root=str(ROOT), state=self.state, engine=self.engine)
        child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaises(CoordinationError):
                EngineLock(self.engine).acquire("mine", "/tmp")
            self.assertIsNotNone(live_holder(self.engine))
            child.kill()
            child.wait(10)
        finally:
            if child.poll() is None:
                child.kill()
            child.stdout.close()
        acquired = EngineLock(self.engine).acquire("after-death", "/tmp")
        self.assertTrue(acquired.held)
        acquired.release()

    def test_live_holder_reports_none_when_free(self):
        self.assertIsNone(live_holder(self.engine))
        held = EngineLock(self.engine).acquire("run-x", "/tmp")
        self.assertEqual(live_holder(self.engine)["run_id"], "run-x")
        held.release()
        self.assertIsNone(live_holder(self.engine))

    def test_write_failure_releases_the_lock_instead_of_leaking_it(self):
        lock = EngineLock(self.engine)
        with patch("json.dumps", side_effect=OSError("disk full")):
            with self.assertRaises(CoordinationError):
                lock.acquire("run-a", "/tmp")
        self.assertFalse(lock.held)
        self.assertIsNone(live_holder(self.engine))
        EngineLock(self.engine).acquire("run-b", "/tmp").release()

    def test_scope_statement_refuses_a_distributed_claim(self):
        self.assertIn("Not a distributed lock", SCOPE)
        self.assertIn("one local user", SCOPE)


class ListingDocker(FakeDocker):
    def __init__(self, listing="", **kwargs):
        super().__init__(**kwargs)
        self.listing = listing

    def call(self, args, timeout=20, merge=False):
        if args[0] == "ps":
            return self.listing
        return super().call(args, timeout, merge)


class OrphanTests(unittest.TestCase):
    def setUp(self):
        os.environ["EVALNOISE_STATE_DIR"] = tempfile.mkdtemp(prefix="evalnoise-orphan-")

    def test_foreign_running_container_refuses_a_new_run(self):
        listing = json.dumps({"ID": "d" * 64, "Names": "evalnoise-oldrun-r000",
                              "Labels": "io.evalnoise.run=oldrun,io.evalnoise.engine=e"})
        backend = ListingDocker(listing)
        with self.assertRaises(CoordinationError) as raised:
            refuse_orphans(backend, backend.engine_id, "newrun")
        self.assertIn("evalnoise-oldrun-r000", str(raised.exception))
        self.assertIn("evalnoise diagnose", str(raised.exception))

    def test_our_own_run_label_does_not_block_us(self):
        listing = json.dumps({"ID": "d" * 64, "Names": "evalnoise-mine-r000",
                              "Labels": "io.evalnoise.run=mine,io.evalnoise.engine=e"})
        self.assertEqual(refuse_orphans(ListingDocker(listing), "e", "mine"), [])

    def test_unreadable_listing_refuses_rather_than_assuming_empty(self):
        with self.assertRaises(CoordinationError):
            engine_containers(ListingDocker("not-json"), "e")

    def test_orphans_are_rechecked_while_the_lock_is_held(self):
        listing = json.dumps({"ID": "d" * 64, "Names": "evalnoise-other-r000",
                              "Labels": "io.evalnoise.run=other"})
        backend = ListingDocker(listing)
        observed = []
        original = backend.call
        def watching(args, timeout=20, merge=False):
            if args[0] == "ps":
                observed.append(live_holder(backend.engine_id) is not None)
            return original(args, timeout, merge)
        backend.call = watching
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(CoordinationError):
                execute(parse(config()), root, backend)
        self.assertEqual(observed, [True])
        self.assertIsNone(live_holder(backend.engine_id))

    def test_failure_after_acquiring_the_lock_still_releases_it(self):
        backend = FakeDocker()
        with tempfile.TemporaryDirectory() as root:
            with patch("evalnoise.runner.write_json", side_effect=OSError("read-only volume")):
                with self.assertRaises(OSError):
                    execute(parse(config()), root, backend)
        self.assertIsNone(live_holder(backend.engine_id))
        with tempfile.TemporaryDirectory() as root:
            execute(parse(config()), root, backend)

    def test_directory_collision_after_acquire_releases_the_lock(self):
        backend = FakeDocker()
        with tempfile.TemporaryDirectory() as root:
            with patch("pathlib.Path.mkdir", side_effect=FileExistsError("already there")):
                with self.assertRaises(FileExistsError):
                    execute(parse(config()), root, backend)
        self.assertIsNone(live_holder(backend.engine_id))

    def test_execute_refuses_when_an_orphan_is_present(self):
        listing = json.dumps({"ID": "d" * 64, "Names": "evalnoise-other-r000",
                              "Labels": "io.evalnoise.run=other"})
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(CoordinationError):
                execute(parse(config()), root, ListingDocker(listing))
            self.assertEqual(list(Path(root).iterdir()), [])


class InspectDocker(FakeDocker):
    """Serves structural `ps` listings and inspections the way the real engine does."""

    def __init__(self, containers, listing_error=None, inspect_error=None, **kwargs):
        super().__init__(**kwargs)
        self.containers = containers
        self.listing_error, self.inspect_error = listing_error, inspect_error
        self.removed_ids = []

    def call(self, args, timeout=20, merge=False):
        if args[0] == "ps" and "--filter" in args:
            if self.listing_error:
                raise DockerError(self.listing_error)
            wanted = args[args.index("--filter") + 1].removeprefix("name=^/").removesuffix("$")
            entry = self.containers.get(wanted)
            if entry is None:
                return ""
            return json.dumps({"ID": entry["Id"], "Names": wanted}) + "\n"
        if args[0] == "inspect":
            if self.inspect_error:
                raise DockerError(self.inspect_error)
            entry = self.containers.get(args[1])
            if entry is None:
                raise DockerError("No such object")
            return json.dumps([entry])
        if args[0] == "rm":
            if args[-1] not in self.removed_ids:
                self.removed_ids.append(args[-1])
            return ""
        if args[0] == "ps":
            return ""
        return super().call(args, timeout, merge)


WORKLOAD_IMAGE = "sha256:" + __import__("hashlib").sha256(b"evalnoise-workloads:local").hexdigest()


def container(run_id, name, image=WORKLOAD_IMAGE, engine=None, container_id=None, running=False,
              reported_name=None):
    labels = {"io.evalnoise.run": run_id}
    if engine:
        labels["io.evalnoise.engine"] = engine
    return {"Id": container_id or ("a" * 64), "Image": image, "Config": {"Labels": labels},
            "Name": reported_name or ("/" + name),
            "State": {"Status": "exited", "Running": running}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        os.environ["EVALNOISE_STATE_DIR"] = tempfile.mkdtemp(prefix="evalnoise-recovery-")
        self.root = tempfile.mkdtemp(prefix="evalnoise-runs-")
        self.backend = FakeDocker()
        self.directory = execute(parse(config()), self.root, self.backend)
        self.manifest = json.loads((self.directory / "manifest.json").read_text())
        self.run_id = self.manifest["run_id"]
        self.engine = self.backend.engine_id

    def survivor(self, **kwargs):
        name = f"evalnoise-{self.run_id}-r000-p00-t000"
        return name, {name: container(self.run_id, name, engine=self.engine, **kwargs)}

    def test_manifest_validation_rejects_tampered_identity(self):
        broken = dict(self.manifest, run_id="../../escape")
        (self.directory / "manifest.json").write_text(json.dumps(broken))
        with self.assertRaises(RecoveryError):
            load_manifest(self.directory)

    def test_expected_names_come_only_from_the_validated_plan(self):
        names = expected_names(self.manifest)
        self.assertEqual(len(names), 18)
        self.assertTrue(all(name.startswith(f"evalnoise-{self.run_id}-") for name in names))
        self.assertEqual({stage for _, stage in names.values()}, {"workload"})

    def test_verifier_names_appear_only_for_verifier_tasks(self):
        data = json.loads((ROOT / "experiments/verified.json").read_text())
        data["repeats"], data["tasks"] = 1, data["tasks"][:1]
        with tempfile.TemporaryDirectory() as root:
            directory = execute(parse(data), root, FakeDocker())
            manifest = json.loads((directory / "manifest.json").read_text())
        stages = expected_names(manifest)
        self.assertEqual(sorted(stage for _, stage in stages.values()), ["verifier", "workload"])

    def test_plan_inconsistent_with_the_config_is_refused(self):
        broken = json.loads((self.directory / "manifest.json").read_text())
        broken["plan"][0]["trials"][0]["id"] = "r000-p00-t999"
        (self.directory / "manifest.json").write_text(json.dumps(broken))
        with self.assertRaises(RecoveryError) as raised:
            load_manifest(self.directory)
        self.assertIn("does not match the schedule", str(raised.exception))

    def test_malformed_and_missing_manifests_fail_closed(self):
        for payload in ("[]", "{}", '{"run_id": "abc123", "config": 5, "plan": [], "images": {}}'):
            (self.directory / "manifest.json").write_text(payload)
            with self.assertRaises(RecoveryError):
                load_manifest(self.directory)
        (self.directory / "manifest.json").unlink()
        with self.assertRaises(RecoveryError):
            load_manifest(self.directory)

    def test_missing_or_malformed_image_identity_fails_closed(self):
        for identity in (None, "", "abc", "sha256:xyz"):
            broken = json.loads(json.dumps(self.manifest))
            broken["images"]["evalnoise-workloads:local"]["id"] = identity
            with self.assertRaises(RecoveryError) as raised:
                stage_image(broken, "cpu", "workload")
            self.assertIn("no valid image identity", str(raised.exception))

    def test_engine_outage_never_reads_as_a_clean_run(self):
        _, containers = self.survivor()
        backend = InspectDocker(containers, listing_error="daemon unreachable", engine_id=self.engine)
        with self.assertRaises(DockerError):
            survey(backend, self.manifest)
        report = diagnose(backend, self.directory)
        self.assertIn("daemon unreachable", report["survey_error"])
        self.assertEqual(report["containers"], [])

    def test_corrupt_listing_or_inspection_is_not_treated_as_absent(self):
        _, containers = self.survivor()
        listing = InspectDocker(containers, engine_id=self.engine)
        listing.containers = containers
        broken = InspectDocker(containers, inspect_error="inspect exploded", engine_id=self.engine)
        with self.assertRaises(DockerError):
            survey(broken, self.manifest)

    def test_missing_engine_label_is_refused(self):
        name = f"evalnoise-{self.run_id}-r000-p00-t000"
        containers = {name: container(self.run_id, name)}
        found = survey(InspectDocker(containers, engine_id=self.engine), self.manifest)
        self.assertFalse(found[0]["owned"])
        self.assertIn("io.evalnoise.engine", " ".join(found[0]["refusals"]))

    def test_engine_reported_name_must_match_exactly(self):
        name = f"evalnoise-{self.run_id}-r000-p00-t000"
        containers = {name: container(self.run_id, name, engine=self.engine, reported_name="/other")}
        found = survey(InspectDocker(containers, engine_id=self.engine), self.manifest)
        self.assertFalse(found[0]["owned"])
        self.assertIn("engine reports name", " ".join(found[0]["refusals"]))

    def test_cleanup_holds_the_engine_lock_for_validation_and_removal(self):
        name, containers = self.survivor(container_id="b" * 64)
        seen = []
        class LockObserving(InspectDocker):
            def call(inner, args, timeout=20, merge=False):
                if args[0] in ("ps", "inspect", "rm"):
                    seen.append((args[0], live_holder(self.engine) is not None))
                return super().call(args, timeout, merge)
        backend = LockObserving(containers, engine_id=self.engine)
        cleanup(backend, self.directory, confirm=True)
        self.assertTrue(seen)
        self.assertTrue(all(held for _, held in seen), seen)
        self.assertIsNone(live_holder(self.engine))

    def test_cleanup_refuses_while_its_own_run_is_still_live(self):
        _, containers = self.survivor()
        own = EngineLock(self.engine).acquire(self.run_id, self.root)
        try:
            backend = InspectDocker(containers, engine_id=self.engine)
            with self.assertRaises(RecoveryError) as raised:
                cleanup(backend, self.directory, confirm=True)
            self.assertIn("coordinating this engine", str(raised.exception))
            self.assertEqual(backend.removed_ids, [])
        finally:
            own.release()

    def test_diagnose_is_read_only(self):
        before = {p.name: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        report = diagnose(self.backend, self.directory)
        after = {p.name: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["recorded_trials"], 18)
        self.assertEqual(report["missing_trials"], [])
        self.assertTrue(report["engine_matches"])

    def test_diagnose_reports_missing_trials_and_a_changed_engine(self):
        for path in list((self.directory / "trials").glob("*.json"))[:5]:
            path.unlink()
        self.backend.engine_id_at_end = uuid.uuid4().hex
        report = diagnose(self.backend, self.directory)
        self.assertEqual(len(report["missing_trials"]), 5)
        self.assertFalse(report["engine_matches"])
        self.assertIn("no container survey attempted", report["survey_error"])

    def test_cleanup_requires_confirmation(self):
        with self.assertRaises(RecoveryError):
            cleanup(self.backend, self.directory, confirm=False)
        self.assertFalse((self.directory / "cleanup.json").exists())

    def test_cleanup_removes_owned_containers_by_full_id(self):
        name, containers = self.survivor(container_id="b" * 64)
        backend = InspectDocker(containers, engine_id=self.engine)
        audit = cleanup(backend, self.directory, confirm=True)
        self.assertEqual(backend.removed_ids, ["b" * 64])
        self.assertEqual([entry["name"] for entry in audit["removed"]], [name])
        self.assertIn("Container removal only", audit["note"])

    def test_cleanup_never_changes_trial_or_manifest_evidence(self):
        _, containers = self.survivor()
        before = {p.name: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        cleanup(InspectDocker(containers, engine_id=self.engine), self.directory, confirm=True)
        after = {p.name: p.read_bytes() for p in self.directory.rglob("*") if p.is_file()}
        self.assertEqual(before, {k: v for k, v in after.items() if k != "cleanup.json"})
        self.assertIn("cleanup.json", after)

    def test_cleanup_refuses_every_candidate_when_one_is_not_owned(self):
        first = f"evalnoise-{self.run_id}-r000-p00-t000"
        second = f"evalnoise-{self.run_id}-r000-p00-t001"
        containers = {first: container(self.run_id, first, engine=self.engine, container_id="b" * 64),
                      second: container("someone-else", second, engine=self.engine, container_id="c" * 64)}
        backend = InspectDocker(containers, engine_id=self.engine)
        with self.assertRaises(RecoveryError) as raised:
            cleanup(backend, self.directory, confirm=True)
        self.assertIn("not owned by this run", str(raised.exception))
        self.assertEqual(backend.removed_ids, [])

    def test_cleanup_refuses_a_replaced_image_identity(self):
        name = f"evalnoise-{self.run_id}-r000-p00-t000"
        containers = {name: container(self.run_id, name, image="sha256:replaced", engine=self.engine)}
        backend = InspectDocker(containers, engine_id=self.engine)
        with self.assertRaises(RecoveryError) as raised:
            cleanup(backend, self.directory, confirm=True)
        self.assertIn("image identity does not match", str(raised.exception))
        self.assertEqual(backend.removed_ids, [])

    def test_cleanup_refuses_a_different_engine(self):
        _, containers = self.survivor()
        backend = InspectDocker(containers, engine_id=uuid.uuid4().hex)
        with self.assertRaises(RecoveryError) as raised:
            cleanup(backend, self.directory, confirm=True)
        self.assertIn("does not match the manifest engine", str(raised.exception))
        self.assertEqual(backend.removed_ids, [])

    def test_cleanup_refuses_underneath_another_live_coordinator(self):
        _, containers = self.survivor()
        holder = EngineLock(self.engine).acquire("otherrun1234", "/tmp")
        try:
            with self.assertRaises(RecoveryError) as raised:
                cleanup(InspectDocker(containers, engine_id=self.engine), self.directory, confirm=True)
            self.assertIn("coordinating this engine", str(raised.exception))
        finally:
            holder.release()

    def test_survey_flags_a_short_container_id(self):
        name = f"evalnoise-{self.run_id}-r000-p00-t000"
        containers = {name: container(self.run_id, name, engine=self.engine, container_id="abc123")}
        found = survey(InspectDocker(containers, engine_id=self.engine), self.manifest)
        self.assertFalse(found[0]["owned"])
        self.assertIn("full container ID", " ".join(found[0]["refusals"]))


class ManifestWarningTests(unittest.TestCase):
    def setUp(self):
        os.environ["EVALNOISE_STATE_DIR"] = tempfile.mkdtemp(prefix="evalnoise-warn-")

    def run_once(self, backend):
        with tempfile.TemporaryDirectory() as root:
            directory = execute(parse(config()), root, backend)
            return json.loads((directory / "manifest.json").read_text())

    def test_stable_engine_is_recorded_without_a_warning(self):
        manifest = self.run_once(FakeDocker())
        self.assertTrue(manifest["engine_identity_final"]["stable"])
        self.assertFalse(any("Engine identity changed" in w for w in manifest["warnings"]))
        self.assertIn(SCOPE, manifest["warnings"])

    def test_changed_engine_warns_without_touching_trials(self):
        backend = FakeDocker()
        backend.engine_id_at_end = uuid.uuid4().hex
        manifest = self.run_once(backend)
        self.assertFalse(manifest["engine_identity_final"]["stable"])
        self.assertTrue(any("Engine identity changed" in w for w in manifest["warnings"]))
        self.assertEqual(manifest["status"], "completed")

    def test_unreachable_engine_at_the_end_warns(self):
        backend = FakeDocker()
        backend.identity_error = "daemon unreachable"
        manifest = self.run_once(backend)
        self.assertFalse(manifest["engine_identity_final"]["stable"])
        self.assertIn("unreachable", manifest["engine_identity_final"]["note"])

    def test_lock_is_released_after_a_completed_run(self):
        backend = FakeDocker()
        self.run_once(backend)
        self.assertIsNone(live_holder(backend.engine_id))


if __name__ == "__main__":
    unittest.main()
