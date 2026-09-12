"""Endpoint pinning, API negotiation, and bounded stats streaming over a real Unix socket."""

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evalnoise import endpoint as endpoints
from evalnoise.config import ConfigError, Profile, cpuset, parse
from evalnoise.docker import Docker, DockerError, audit

SAMPLE = {"read": "2026-09-11T10:00:00.000000000Z", "preread": "2026-09-11T09:59:59.000000000Z",
          "cpu_stats": {"cpu_usage": {"total_usage": 1000, "usage_in_kernelmode": 1, "usage_in_usermode": 2},
                        "throttling_data": {"periods": 10, "throttled_periods": 2, "throttled_time": 500},
                        "system_cpu_usage": 90, "online_cpus": 4},
          "memory_stats": {"usage": 2048, "limit": 4096, "stats": {"anon": 1024, "inactive_file": 16}},
          "pids_stats": {"current": 3, "limit": 128}}


def later(seconds, usage=2000, periods=30, throttled=9):
    value = json.loads(json.dumps(SAMPLE))
    value["read"] = f"2026-09-11T10:00:{seconds:02d}.000000000Z"
    value["cpu_stats"]["cpu_usage"]["total_usage"] = usage
    value["cpu_stats"]["throttling_data"].update(periods=periods, throttled_periods=throttled)
    return value


class SocketServer:
    """Minimal chunked HTTP server on a Unix socket; frames may be split or coalesced."""

    def __init__(self, frames, api_version="1.52", head_status=200, status=200, hold=False):
        self.directory = tempfile.mkdtemp(prefix="evalnoise-sock-")
        self.path = os.path.join(self.directory, "docker.sock")
        self.frames, self.api_version = frames, api_version
        self.head_status, self.status, self.hold = head_status, status, hold
        self.requests = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.path)
        self.server.listen(8)
        self.server.settimeout(.05)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        try:
            self.server.close()
        except OSError:
            pass
        self.thread.join(5)

    def endpoint(self):
        return {"source": "DOCKER_HOST", "context": None, "host": f"unix://{self.path}",
                "scheme": "unix", "skip_tls_verify": False, "socket_path": self.path,
                "local_unix_socket": True}

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection):
        try:
            request = connection.recv(65536).decode("utf-8", "replace")
            self.requests.append(request.split("\r\n")[0])
            if " /_ping" in request:
                status = self.head_status if request.startswith("HEAD") else 200
                headers = f"Api-Version: {self.api_version}\r\nOstype: linux\r\n" if self.api_version else ""
                connection.sendall(f"HTTP/1.1 {status} OK\r\n{headers}Content-Length: 2\r\n\r\n".encode())
                if not request.startswith("HEAD"):
                    connection.sendall(b"OK")
                return
            if self.status != 200:
                connection.sendall(f"HTTP/1.1 {self.status} Error\r\nContent-Length: 0\r\n\r\n".encode())
                return
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                               b"Transfer-Encoding: chunked\r\n\r\n")
            for frame in self.frames:
                payload = frame if isinstance(frame, bytes) else json.dumps(frame).encode()
                connection.sendall(b"%x\r\n%s\r\n" % (len(payload), payload))
            while self.hold and not self.stopped.wait(.05):
                pass
            connection.sendall(b"0\r\n\r\n")
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass


class EndpointResolutionTests(unittest.TestCase):
    def resolve(self, environment, responses):
        calls = []
        def call(args, timeout=20):
            calls.append(args)
            return responses[args[0] + ":" + (args[1] if len(args) > 1 else "")]
        with patch.dict(os.environ, environment, clear=False):
            for name in ("DOCKER_CONTEXT", "DOCKER_HOST"):
                if name not in environment:
                    os.environ.pop(name, None)
            return endpoints.resolve(call), calls

    def test_docker_context_overrides_docker_host(self):
        payload = json.dumps({"Name": "desktop", "Endpoints": {"docker": {"Host": "unix:///ctx.sock"}}})
        resolved, _ = self.resolve({"DOCKER_CONTEXT": "desktop", "DOCKER_HOST": "tcp://ignored:2375"},
                                   {"context:inspect": payload})
        self.assertEqual(resolved["source"], "DOCKER_CONTEXT")
        self.assertEqual(resolved["host"], "unix:///ctx.sock")
        self.assertTrue(resolved["local_unix_socket"])

    def test_docker_host_overrides_selected_context(self):
        resolved, calls = self.resolve({"DOCKER_HOST": "tcp://remote:2376"}, {})
        self.assertEqual(resolved["source"], "DOCKER_HOST")
        self.assertEqual(resolved["scheme"], "tcp")
        self.assertFalse(resolved["local_unix_socket"])
        self.assertEqual(calls, [])

    def test_selected_context_then_default(self):
        payload = json.dumps([{"Name": "desktop-linux", "Endpoints": {"docker": {"Host": "unix:///d.sock"}}}])
        resolved, _ = self.resolve({}, {"context:show": "desktop-linux\n", "context:inspect": payload})
        self.assertEqual(resolved["source"], "context")
        self.assertEqual(resolved["context"], "desktop-linux")
        with patch("evalnoise.endpoint.os.path.exists", return_value=True):
            resolved, _ = self.resolve({}, {"context:show": "default\n"})
        self.assertEqual(resolved["source"], "default")
        self.assertEqual(resolved["host"], endpoints.DEFAULT_SOCKETS[0])

    def test_tls_material_is_never_recorded(self):
        payload = json.dumps({"Name": "remote", "Endpoints": {"docker": {"Host": "tcp://h:2376", "SkipTLSVerify": True}},
                              "TLSMaterial": {"docker": ["ca.pem", "key.pem"]},
                              "Storage": {"TLSPath": "/secret/tls"}})
        resolved, _ = self.resolve({"DOCKER_CONTEXT": "remote"}, {"context:inspect": payload})
        serialised = json.dumps(resolved)
        self.assertTrue(resolved["skip_tls_verify"])
        for secret in ("TLSMaterial", "key.pem", "/secret/tls", "TLSPath"):
            self.assertNotIn(secret, serialised)

    def test_every_local_socket_is_pinned_by_explicit_host(self):
        for source, context in (("DOCKER_CONTEXT", "d"), ("context", "d"), ("default", "default")):
            endpoint = {"source": source, "context": context, "host": "unix:///x.sock",
                        "local_unix_socket": True}
            self.assertEqual(endpoints.cli_flags(endpoint), ["--host", "unix:///x.sock"], source)

    def test_remote_context_keeps_its_name_and_tls_material(self):
        self.assertEqual(endpoints.cli_flags({"source": "context", "context": "remote",
                                              "host": "tcp://h:2376", "local_unix_socket": False}),
                         ["--context", "remote"])
        self.assertEqual(endpoints.cli_flags({"source": "DOCKER_HOST", "context": None,
                                              "host": "tcp://h:1", "local_unix_socket": False}),
                         ["--host", "tcp://h:1"])

    def test_default_endpoint_is_pinned_not_left_ambient(self):
        backend = Docker(endpoints._endpoint("default", "default", endpoints.DEFAULT_SOCKETS[0]))
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "{}"
            run.return_value.stderr = ""
            backend.call(["ps"])
        self.assertEqual(run.call_args.args[0][:3],
                         ["docker", "--host", endpoints.DEFAULT_SOCKETS[0]])

    def test_failed_call_reports_the_exit_code_and_never_an_empty_diagnostic(self):
        # A nonzero exit with empty stderr previously raised a message ending in a bare
        # ": ", which is unattributable in a captured log.
        backend = Docker(endpoints._endpoint("default", "default", endpoints.DEFAULT_SOCKETS[0]))
        for code, stderr, expected in (
                (1, "Error: No such image: evalnoise-tools:local",
                 "docker image failed (exit 1): Error: No such image: evalnoise-tools:local"),
                (125, "   \n", "docker image failed (exit 125): no stderr diagnostics"),
                (137, "", "docker image failed (exit 137): no stderr diagnostics")):
            with self.subTest(code=code):
                with patch("subprocess.run") as run:
                    run.return_value.returncode = code
                    run.return_value.stdout = ""
                    run.return_value.stderr = stderr
                    with self.assertRaises(DockerError) as raised:
                        backend.call(["image", "inspect", "evalnoise-tools:local"])
                self.assertEqual(str(raised.exception), expected)

    def test_docker_env_is_snapshotted_and_stripped_for_children(self):
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "at-init", "PATH": os.environ["PATH"]}):
            backend = Docker(endpoints._endpoint("DOCKER_HOST", None, "unix:///x.sock"))
        os.environ["DOCKER_CONTEXT"] = "changed-later"
        try:
            with patch("subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "{}"
                run.return_value.stderr = ""
                backend.call(["ps"])
            passed = run.call_args.kwargs["env"]
            self.assertNotIn("DOCKER_CONTEXT", passed)
            self.assertNotIn("DOCKER_HOST", passed)
            self.assertIn("PATH", passed)
        finally:
            os.environ.pop("DOCKER_CONTEXT", None)

    def test_unresolvable_context_is_explicit(self):
        def call(args, timeout=20):
            raise DockerError("no such context")
        with patch.dict(os.environ, {"DOCKER_CONTEXT": "missing"}):
            with self.assertRaises(endpoints.EndpointError):
                endpoints.resolve(call)

    def test_unreadable_context_show_never_falls_through_to_default(self):
        def call(args, timeout=20):
            raise DockerError("docker context show failed")
        for name in ("DOCKER_CONTEXT", "DOCKER_HOST"):
            os.environ.pop(name, None)
        with self.assertRaises(endpoints.EndpointError) as raised:
            endpoints.resolve(call)
        self.assertIn("Refusing to assume the default socket", str(raised.exception))

    def test_remote_context_mutation_after_pinning_is_refused(self):
        pinned = {"source": "context", "context": "remote", "host": "tcp://original:2376",
                  "scheme": "tcp", "skip_tls_verify": False, "local_unix_socket": False}
        moved = json.dumps({"Name": "remote", "Endpoints": {"docker": {"Host": "tcp://elsewhere:2376"}}})
        with self.assertRaises(endpoints.EndpointError) as raised:
            endpoints.revalidate(lambda args, timeout=20: moved, pinned)
        self.assertIn("refusing to continue against a different engine", str(raised.exception))
        same = json.dumps({"Name": "remote", "Endpoints": {"docker": {"Host": "tcp://original:2376"}}})
        self.assertEqual(endpoints.revalidate(lambda args, timeout=20: same, pinned), pinned)

    def test_resolution_uses_the_snapshot_not_the_live_environment(self):
        payload = json.dumps({"Name": "snap", "Endpoints": {"docker": {"Host": "unix:///snap.sock"}}})
        backend = Docker()
        backend.environment = {"DOCKER_CONTEXT": "snap", "PATH": os.environ["PATH"]}
        os.environ["DOCKER_CONTEXT"] = "live-and-different"
        try:
            with patch.object(backend, "_ambient", return_value=payload):
                endpoint = backend.pin()
        finally:
            os.environ.pop("DOCKER_CONTEXT", None)
        self.assertEqual(endpoint["context"], "snap")
        self.assertEqual(endpoint["host"], "unix:///snap.sock")

    def test_remote_context_is_refused_for_measurement_but_docker_host_works(self):
        remote = {"source": "context", "context": "remote", "host": "tcp://h:2376",
                  "scheme": "tcp", "local_unix_socket": False}
        with self.assertRaises(endpoints.EndpointError) as raised:
            Docker(remote).pin()
        self.assertIn("DOCKER_HOST=tcp://h:2376", str(raised.exception))
        explicit = endpoints._endpoint("DOCKER_HOST", None, "tcp://h:2376")
        self.assertEqual(Docker(explicit).pin(), explicit)

    def test_missing_default_socket_is_not_an_endless_fallback(self):
        for name in ("DOCKER_CONTEXT", "DOCKER_HOST"):
            os.environ.pop(name, None)
        with patch("evalnoise.endpoint.os.path.exists", return_value=False):
            with self.assertRaises(endpoints.EndpointError) as raised:
                endpoints.resolve(lambda args, timeout=20: "default\n", {})
        self.assertIn("does not exist", str(raised.exception))
        with patch("evalnoise.endpoint.os.path.exists", return_value=True):
            self.assertEqual(endpoints.resolve(lambda args, timeout=20: "default\n", {})["source"],
                             "default")

    def test_local_socket_needs_no_context_revalidation(self):
        local = endpoints._endpoint("context", "desktop", "unix:///x.sock")
        def refuse(args, timeout=20):
            raise AssertionError("a pinned local socket must not re-read the context")
        self.assertEqual(endpoints.revalidate(refuse, local), local)


class NegotiationTests(unittest.TestCase):
    def test_pins_the_daemon_reported_version(self):
        with SocketServer([]) as server:
            status = endpoints.negotiate(server.endpoint(), "1.52", "1.44")
        self.assertEqual(status["telemetry_source"], "engine_stream")
        self.assertEqual(status["api_version_pinned"], "1.52")
        self.assertEqual(status["ping_api_version"], "1.52")

    def test_head_failure_falls_back_to_get(self):
        with SocketServer([], head_status=405) as server:
            status = endpoints.negotiate(server.endpoint(), "1.52", "1.44")
            self.assertEqual(status["telemetry_source"], "engine_stream")
        self.assertTrue(any(line.startswith("HEAD") for line in server.requests))
        self.assertTrue(any(line.startswith("GET") for line in server.requests))

    def test_never_pins_below_the_daemon_minimum_or_floor(self):
        with SocketServer([], api_version="1.24") as server:
            status = endpoints.negotiate(server.endpoint(), "1.24", "1.24")
        self.assertIsNone(status["api_version_pinned"])
        self.assertEqual(status["telemetry_source"], "cli_snapshot")
        self.assertIn("below the supported streaming floor", status["reason"])
        with SocketServer([], api_version="1.41") as server:
            status = endpoints.negotiate(server.endpoint(), "1.41", "1.44")
        self.assertIsNone(status["api_version_pinned"])
        self.assertIn("below the daemon minimum", status["reason"])

    def test_remote_endpoint_keeps_cli_snapshot_without_switching_engines(self):
        remote = {"source": "DOCKER_HOST", "host": "tcp://h:2375", "scheme": "tcp",
                  "local_unix_socket": False, "socket_path": None}
        status = endpoints.negotiate(remote, "1.52", "1.44")
        self.assertEqual(status["telemetry_source"], "cli_snapshot")
        self.assertIn("not a local Unix socket", status["reason"])
        self.assertIsNone(status["api_version_pinned"])

    def test_unreachable_socket_is_recorded_not_raised(self):
        broken = {"source": "default", "host": "unix:///nonexistent.sock", "scheme": "unix",
                  "local_unix_socket": True, "socket_path": "/nonexistent-evalnoise.sock"}
        status = endpoints.negotiate(broken, "1.52", "1.44")
        self.assertEqual(status["telemetry_source"], "cli_snapshot")
        self.assertIsNotNone(status["reason"])


class StreamGuardTests(unittest.TestCase):
    def test_engine_stream_without_a_pinned_endpoint_raises_instead_of_streaming(self):
        backend = Docker(endpoint=None)
        backend.telemetry = {"telemetry_source": "engine_stream", "api_version_pinned": "1.52"}
        with self.assertRaises(DockerError) as caught:
            backend.stream("c" * 64, 0.1, time.time())
        self.assertIn("Streaming requires a pinned Docker endpoint", str(caught.exception))

    def test_snapshot_telemetry_still_declines_streaming_without_raising(self):
        backend = Docker(endpoint=None)
        backend.telemetry = {"telemetry_source": "cli_snapshot", "api_version_pinned": None,
                             "reason": "Endpoint not resolved yet"}
        self.assertIsNone(backend.stream("c" * 64, 0.1, time.time()))


class StreamTests(unittest.TestCase):
    def collect(self, frames, interval=0, **kwargs):
        with SocketServer(frames) as server:
            stream = endpoints.StatsStream(server.path, "1.52", "c" * 64, interval, **kwargs).start()
            deadline = time.monotonic() + 5
            while stream._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            return stream.close()

    def test_split_and_coalesced_frames_both_decode(self):
        payload = json.dumps(SAMPLE).encode()
        result = self.collect([payload[:20], payload[20:], payload + payload])
        self.assertEqual(result["samples_received"], 3)
        self.assertEqual(result["samples_retained"], 3)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["samples"][0]["raw"], SAMPLE)

    def test_raw_throttling_is_preserved_and_derived_values_are_deltas(self):
        result = self.collect([SAMPLE, later(5)])
        derived = result["derived"]
        self.assertEqual(result["samples"][0]["raw"]["cpu_stats"]["throttling_data"],
                         {"periods": 10, "throttled_periods": 2, "throttled_time": 500})
        self.assertEqual(derived["periods_delta"], 20)
        self.assertEqual(derived["throttled_periods_delta"], 7)
        self.assertEqual(derived["cpu_total_ns_delta"], 1000)
        self.assertAlmostEqual(derived["throttled_fraction"], 0.35)
        self.assertEqual(derived["memory_usage_max_observed"], 2048)

    def test_derived_values_are_null_without_two_usable_samples(self):
        result = self.collect([SAMPLE])
        self.assertIsNone(result["derived"]["throttled_fraction"])
        self.assertIsNone(result["derived"]["periods_delta"])
        zeroed = later(5, periods=10, throttled=2)
        result = self.collect([SAMPLE, zeroed])
        self.assertEqual(result["derived"]["periods_delta"], 0)
        self.assertIsNone(result["derived"]["throttled_fraction"])

    def test_degraded_engine_sample_is_recorded_not_discarded(self):
        result = self.collect([{"id": "abc", "name": "/x", "os_type": "linux"}, SAMPLE, later(5)])
        self.assertEqual(result["samples_retained"], 3)
        self.assertTrue(result["samples"][0]["degraded"])
        self.assertFalse(result["samples"][1]["degraded"])
        self.assertEqual(result["derived"]["usable_samples"], 2)
        self.assertEqual(result["errors"], [])

    def test_retention_uses_engine_read_timestamps(self):
        frames = [SAMPLE, later(1), later(2), later(4), later(9)]
        result = self.collect(frames, interval=3)
        self.assertEqual(result["samples_received"], 5)
        self.assertEqual(result["samples_retained"], 3)
        self.assertIn("engine read timestamp", result["retention_policy"])

    def test_sample_count_and_byte_bounds_truncate_without_error(self):
        result = self.collect([SAMPLE] * 10, max_samples=4)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["samples_retained"], 4)
        result = self.collect([SAMPLE] * 10, max_total_bytes=1)
        self.assertTrue(result["truncated"])
        self.assertLess(result["samples_retained"], 10)

    def test_oversized_frame_is_bounded(self):
        result = self.collect([b'{"read": "' + b"x" * 5000], max_frame_bytes=1024)
        self.assertTrue(result["truncated"])
        self.assertIn("stats frame exceeded the buffer bound", result["errors"])

    def test_end_of_stream_is_not_an_error(self):
        result = self.collect([SAMPLE])
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["closed_before_cleanup"])

    def test_non_success_status_is_reported(self):
        with SocketServer([], status=409) as server:
            stream = endpoints.StatsStream(server.path, "1.52", "c" * 64).start()
            time.sleep(.3)
            result = stream.close()
        self.assertIn("stats stream returned HTTP 409", result["errors"])
        self.assertEqual(result["samples"], [])

    def test_close_stops_a_live_stream_and_leaves_no_thread(self):
        before = set(threading.enumerate())
        with patch("threading.excepthook") as uncaught, SocketServer([SAMPLE], hold=True) as server:
            stream = endpoints.StatsStream(server.path, "1.52", "c" * 64).start()
            time.sleep(.3)
            result = stream.close()
            uncaught.assert_not_called()
        self.assertFalse(result["thread_leaked"])
        self.assertTrue(result["closed_before_cleanup"])
        self.assertEqual(result["samples_retained"], 1)
        time.sleep(.2)
        self.assertEqual({t for t in threading.enumerate() if t.is_alive()} - before, set())

    def test_total_byte_cap_counts_dropped_frames(self):
        frames = [later(second) for second in range(1, 40)]
        result = self.collect(frames, interval=30, max_total_bytes=2000)
        self.assertTrue(result["truncated"])
        self.assertLess(result["bytes_read"], 8000)
        self.assertLessEqual(result["samples_retained"], 2)

    def test_single_complete_oversize_frame_is_rejected(self):
        big = dict(SAMPLE, filler="x" * 4000)
        result = self.collect([big], max_frame_bytes=1024)
        self.assertTrue(result["truncated"])
        self.assertIn("stats frame exceeded the buffer bound", result["errors"])
        self.assertEqual(result["samples"], [])

    def test_truncated_record_at_end_of_stream_is_an_error(self):
        payload = json.dumps(SAMPLE).encode()
        result = self.collect([payload, payload[:30]])
        self.assertIn("stats stream ended inside an incomplete record", result["errors"])
        self.assertEqual(result["samples_retained"], 1)

    def test_multibyte_codepoint_split_across_chunks_is_not_corrupted(self):
        value = dict(SAMPLE, name="/caf\u00e9-\u4e2d\u6587")
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        cut = payload.index(b"\xc3")
        result = self.collect([payload[:cut + 1], payload[cut + 1:]])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["samples"][0]["raw"]["name"], "/caf\u00e9-\u4e2d\u6587")

    def test_sample_cap_counts_before_the_retention_filter(self):
        frames = [later(second) for second in range(1, 30)]
        result = self.collect(frames, interval=25, max_samples=1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["samples_retained"], 1)

    def test_boolean_counters_are_not_treated_as_integers(self):
        broken = json.loads(json.dumps(SAMPLE))
        broken["cpu_stats"]["throttling_data"]["periods"] = True
        result = self.collect([broken, later(5)])
        self.assertIsNone(result["derived"]["periods_delta"])
        self.assertIsNone(result["derived"]["throttled_fraction"])

    def test_one_usable_sample_still_reports_observed_maxima(self):
        result = self.collect([SAMPLE])
        self.assertEqual(result["derived"]["memory_usage_max_observed"], 2048)
        self.assertEqual(result["derived"]["pids_max_observed"], 3)
        self.assertIsNone(result["derived"]["periods_delta"])

    def test_close_during_connect_does_not_leak_a_reader(self):
        with SocketServer([SAMPLE], hold=True) as server:
            stream = endpoints.StatsStream(server.path, "1.52", "c" * 64).start()
            result = stream.close(timeout=3)
        self.assertFalse(result["thread_leaked"])
        self.assertTrue(result["closed_before_cleanup"])

    def test_returned_evidence_is_not_mutated_after_close(self):
        with SocketServer([SAMPLE], hold=True) as server:
            stream = endpoints.StatsStream(server.path, "1.52", "c" * 64).start()
            time.sleep(.3)
            result = stream.close()
            retained = len(result["samples"])
            stream.samples.append({"degraded": True, "raw": {}})
        self.assertEqual(len(result["samples"]), retained)

    def test_close_reports_a_leak_rather_than_claiming_success(self):
        stream = endpoints.StatsStream("/nonexistent-evalnoise.sock", "1.52", "c" * 64)
        stream._thread = threading.Thread(target=lambda: time.sleep(2), daemon=True)
        stream._thread.start()
        result = stream.close(timeout=.2)
        self.assertTrue(result["thread_leaked"])
        self.assertFalse(result["closed_before_cleanup"])

    def test_versioned_path_is_requested(self):
        with SocketServer([SAMPLE]) as server:
            endpoints.StatsStream(server.path, "1.52", "a" * 64).start()
            time.sleep(.3)
        self.assertTrue(any("/v1.52/containers/" + "a" * 64 + "/stats?stream=true" in line
                            for line in server.requests), server.requests)


class AvailabilityTests(unittest.TestCase):
    def test_cgroup_v2_lists_unavailable_fields_instead_of_zeroing(self):
        support = endpoints.availability("2")
        self.assertTrue(support["known"])
        self.assertIn("cpu_stats.cpu_usage.percpu_usage", support["unavailable"])
        self.assertIn("memory_stats.max_usage", support["unavailable"])
        self.assertTrue(support["semantics_changed"])

    def test_unknown_cgroup_version_asserts_nothing(self):
        support = endpoints.availability(None)
        self.assertFalse(support["known"])
        self.assertEqual(support["unavailable"], [])


class ResourceAuditTests(unittest.TestCase):
    def profile(self, **kwargs):
        return Profile("p", kwargs.pop("cpus", 1), kwargs.pop("memory_mb", 48), 10, **kwargs)

    def observed(self, profile, **overrides):
        return {"NanoCpus": round(profile.cpus * 10**9), "Memory": profile.memory_mb * 1024**2,
                "MemorySwap": profile.memory_mb * 1024**2, "PidsLimit": 128, "NetworkMode": "none",
                "ReadonlyRootfs": True, "CpusetCpus": profile.cpuset_cpus or "", **overrides}

    def test_matching_echo_passes_without_claiming_a_reservation(self):
        profile = self.profile()
        report = audit(profile, self.observed(profile))
        self.assertTrue(report["enforced_as_requested"])
        self.assertIn("no dedicated CPU reservation", report["scope"])

    def test_each_rewritten_field_is_reported(self):
        profile = self.profile()
        for field, value in (("NanoCpus", 2 * 10**9), ("Memory", 1), ("MemorySwap", -1),
                             ("PidsLimit", 0), ("NetworkMode", "bridge"), ("ReadonlyRootfs", False)):
            report = audit(profile, self.observed(profile, **{field: value}))
            self.assertFalse(report["enforced_as_requested"])
            self.assertEqual([m["field"] for m in report["mismatches"]], [field])

    def test_cpuset_request_is_audited(self):
        profile = self.profile(cpuset_cpus="0-1")
        self.assertTrue(audit(profile, self.observed(profile))["enforced_as_requested"])
        self.assertFalse(audit(profile, self.observed(profile, CpusetCpus="0"))["enforced_as_requested"])

    def test_equivalent_cpuset_notations_are_the_same_mask(self):
        profile = self.profile(cpus=2, cpuset_cpus="0-1")
        for echoed in ("0-1", "0,1", "1,0"):
            report = audit(profile, self.observed(profile, CpusetCpus=echoed))
            self.assertTrue(report["enforced_as_requested"], echoed)

    def test_malformed_or_missing_cpuset_echo_fails_closed(self):
        profile = self.profile(cpus=2, cpuset_cpus="0-1")
        for echoed in ("0-", "not-a-mask", "", None, 5, "0-1,"):
            report = audit(profile, self.observed(profile, CpusetCpus=echoed))
            self.assertFalse(report["enforced_as_requested"], echoed)
            self.assertEqual([m["field"] for m in report["mismatches"]], ["CpusetCpus"], echoed)
            self.assertIn("reason", report["mismatches"][0])

    def test_unrequested_mask_applied_by_the_engine_is_a_mismatch(self):
        profile = self.profile()
        self.assertTrue(audit(profile, self.observed(profile, CpusetCpus=""))["enforced_as_requested"])
        report = audit(profile, self.observed(profile, CpusetCpus="0-3"))
        self.assertFalse(report["enforced_as_requested"])
        self.assertIn("not requested", report["mismatches"][0]["reason"])


class CpusetTests(unittest.TestCase):
    def test_valid_masks_expand(self):
        self.assertEqual(cpuset("0"), (0,))
        self.assertEqual(cpuset("0-3"), (0, 1, 2, 3))
        self.assertEqual(cpuset("1,3-4"), (1, 3, 4))

    def test_invalid_masks_rejected(self):
        for value in ("", "0-", "-1", "a", "0,,1", "3-1", "0,0", "0 1", None, 3):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                cpuset(value)

    def test_mask_must_cover_the_cpu_ceiling(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "experiments/calibration.json").read_text())
        data["profiles"][0]["cpuset_cpus"] = "0-1"
        self.assertEqual(parse(data).profiles[0].cpuset_cpus, "0-1")
        data["profiles"][0]["cpus"] = 3
        with self.assertRaises(ConfigError):
            parse(data)

    def test_index_beyond_engine_count_is_refused_with_an_unknown_mask_caveat(self):
        from evalnoise.runner import check_cpuset
        profile = Profile("p", 1, 48, 10, 1, "6-7")
        with self.assertRaises(DockerError) as raised:
            check_cpuset(profile, {"NCPU": 4})
        self.assertIn("narrower mask", str(raised.exception))
        check_cpuset(Profile("p", 1, 48, 10, 1, "0-3"), {"NCPU": 4})
        with self.assertRaises(DockerError) as raised:
            check_cpuset(profile, {"NCPU": None})
        self.assertIn("cannot be validated", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
