import copy
import unittest

from scripts.static_demo import PROFILES, TASKS, project, render


def fixture():
    return {"manifest": {"private": "DO_NOT_EXPORT"}, "trials": [
        {"task": task, "profile": profile, "repeat": repeat, "status": "passed",
         "container_duration_s": 0.2, "logs": {"text": "DO_NOT_EXPORT"},
         "inspection": {"state": {"ExitCode": 0, "OOMKilled": False}},
         "verification": {"verdict": {"passed": True}}}
        for task in TASKS for profile in PROFILES for repeat in range(3)]}


class StaticDemoTests(unittest.TestCase):
    def test_export_is_fixed_projection_not_recursive_redaction(self):
        data = project(fixture())
        self.assertEqual(len(data["trials"]), 48)
        self.assertNotIn("DO_NOT_EXPORT", str(data))
        page = render(data)
        self.assertNotIn("<script", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertIn("default-src 'none'", page)

    def test_arbitrary_labels_and_invalid_values_are_refused(self):
        for field, value in (("task", "private-device"), ("profile", "<script>"),
                             ("status", "secret"), ("repeat", True),
                             ("container_duration_s", float("nan")),
                             ("container_duration_s", 10 ** 1000)):
            run = fixture()
            run["trials"][0][field] = value
            with self.subTest(field=field, value=repr(value)[:30]), self.assertRaises(ValueError):
                project(run)

    def test_missing_duplicate_and_untyped_evidence_are_refused(self):
        run = fixture()
        run["trials"].pop()
        with self.assertRaises(ValueError):
            project(run)
        run = fixture()
        run["trials"][-1] = copy.deepcopy(run["trials"][0])
        with self.assertRaises(ValueError):
            project(run)
        for field in ("ExitCode", "OOMKilled"):
            run = fixture()
            run["trials"][0]["inspection"]["state"][field] = "private"
            with self.assertRaises(ValueError):
                project(run)
