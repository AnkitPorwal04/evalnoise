"""Pinned Docker endpoint identity and raw Engine telemetry over a local Unix socket.

Endpoint precedence follows the documented Docker CLI rules: DOCKER_CONTEXT overrides
DOCKER_HOST, which overrides the context selected by `docker context use`, which falls
back to the platform default socket. The resolved endpoint is pinned onto every later
command so an environment change mid-run cannot silently move engines.
"""

from datetime import datetime, timezone
import codecs
import http.client
import json
import os
import socket
import threading
import time

# Floor for the documented container stats response fields this module records.
# The pinned version is the daemon's own advertised API version, which is never
# below its minimum; we only refuse engines older than this floor.
STATS_API_FLOOR = (1, 41)
DEFAULT_SOCKETS = ("unix:///var/run/docker.sock",)
MAX_FRAME_BYTES = 1 << 20
MAX_SAMPLES = 3600
MAX_TOTAL_BYTES = 8 << 20
READ_TIMEOUT_S = 30


class EndpointError(RuntimeError):
    pass


class _Cancelled(Exception):
    pass


def parse_version(value):
    try:
        major, _, minor = str(value).partition(".")
        return int(major), int(minor)
    except (ValueError, AttributeError):
        raise EndpointError(f"Unreadable API version: {value!r}") from None


def resolve(call, environment=None):
    environment = os.environ if environment is None else environment
    context_name = environment.get("DOCKER_CONTEXT")
    if context_name:
        return _from_context(call, context_name, "DOCKER_CONTEXT")
    host = environment.get("DOCKER_HOST")
    if host:
        return _endpoint("DOCKER_HOST", None, host)
    try:
        current = call(["context", "show"], 10).strip()
    except Exception as error:
        raise EndpointError(
            f"Could not determine the selected Docker context: {error}. Refusing to assume the "
            "default socket, which could target a different engine.") from error
    if current and current != "default":
        return _from_context(call, current, "context")
    endpoint = _endpoint("default", "default", DEFAULT_SOCKETS[0])
    if not os.path.exists(endpoint["socket_path"]):
        raise EndpointError(
            f"The default Docker socket {endpoint['socket_path']} does not exist. Set DOCKER_HOST "
            "or select a context instead of falling back to a socket that is not there.")
    return endpoint


def _from_context(call, name, source):
    try:
        entries = json.loads(call(["context", "inspect", name, "--format", "{{json .}}"], 10))
    except Exception as error:
        raise EndpointError(f"Docker context {name!r} could not be inspected: {error}") from error
    entry = entries[0] if isinstance(entries, list) else entries
    docker = (entry.get("Endpoints") or {}).get("docker") or {}
    host = docker.get("Host")
    if not host:
        raise EndpointError(f"Docker context {name!r} has no docker endpoint host")
    # Only a boolean is retained. TLS paths, certificates, and keys are never recorded.
    return _endpoint(source, entry.get("Name", name), host, bool(docker.get("SkipTLSVerify")))


def _endpoint(source, context_name, host, skip_tls_verify=False):
    scheme = str(host).split("://", 1)[0] if "://" in str(host) else ""
    return {"source": source, "context": context_name, "host": host, "scheme": scheme,
            "skip_tls_verify": skip_tls_verify,
            "socket_path": host.split("://", 1)[1] if scheme == "unix" else None,
            "local_unix_socket": scheme == "unix"}


def cli_flags(endpoint):
    # A local socket is pinned by explicit host so a later context change cannot split the
    # CLI and socket targets. Only a remote context is pinned by name, to keep its TLS material.
    if endpoint.get("local_unix_socket"):
        return ["--host", endpoint["host"]]
    if endpoint["source"] in ("DOCKER_CONTEXT", "context") and endpoint.get("context"):
        return ["--context", endpoint["context"]]
    return ["--host", endpoint["host"]]


def child_environment(environment, endpoint):
    """DOCKER_CONTEXT would conflict with an explicit --host, so both variables are dropped."""
    return {key: value for key, value in environment.items()
            if key not in ("DOCKER_CONTEXT", "DOCKER_HOST")}


def mutable_remote_context(endpoint):
    return (not endpoint.get("local_unix_socket")
            and endpoint["source"] in ("DOCKER_CONTEXT", "context"))


def require_stable_endpoint(endpoint):
    """A remote context name can be repointed between any two CLI calls, and revalidating
    before every operation would double the command count. Remote engines are supported
    through an explicit DOCKER_HOST, which is a literal address snapshotted at startup."""
    if mutable_remote_context(endpoint):
        raise EndpointError(
            f"Docker context {endpoint['context']!r} names a remote engine ({endpoint['host']}). "
            "A context is a mutable name that could be repointed mid-run, so it is refused for "
            f"measurement. Set DOCKER_HOST={endpoint['host']} to pin that engine explicitly.")
    return endpoint


def revalidate(call, endpoint):
    """A remote context is a mutable name; confirm it still points where it did at resolution."""
    if endpoint.get("local_unix_socket") or endpoint["source"] not in ("DOCKER_CONTEXT", "context"):
        return endpoint
    current = _from_context(call, endpoint["context"], endpoint["source"])
    if current["host"] != endpoint["host"] or current["skip_tls_verify"] != endpoint["skip_tls_verify"]:
        raise EndpointError(
            f"Docker context {endpoint['context']!r} changed from {endpoint['host']!r} to "
            f"{current['host']!r} after it was pinned; refusing to continue against a different engine")
    return endpoint


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=READ_TIMEOUT_S):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock = sock
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)


def ping(endpoint, timeout=10):
    """Unversioned /_ping negotiation: HEAD first, GET fallback, read the Api-Version header."""
    if not endpoint.get("local_unix_socket"):
        raise EndpointError("Version negotiation requires a local Unix socket endpoint")
    for method in ("HEAD", "GET"):
        connection = UnixHTTPConnection(endpoint["socket_path"], timeout)
        try:
            connection.request(method, "/_ping")
            response = connection.getresponse()
            response.read()
            if response.status == 200:
                return {"api_version": response.getheader("Api-Version"),
                        "os_type": response.getheader("Ostype"),
                        "experimental": response.getheader("Docker-Experimental") == "true",
                        "method": method}
        except (OSError, http.client.HTTPException) as error:
            if method == "GET":
                raise EndpointError(f"Docker socket ping failed: {error}") from error
        finally:
            connection.close()
    raise EndpointError("Docker daemon did not return a successful ping")


def negotiate(endpoint, server_api, server_min):
    """Pin the daemon's own advertised version; never invent one below its minimum."""
    status = {"api_version_pinned": None, "api_version_server": server_api,
              "api_version_server_minimum": server_min, "ping_api_version": None,
              "telemetry_source": None, "reason": None}
    if not endpoint.get("local_unix_socket"):
        status["telemetry_source"] = "cli_snapshot"
        status["reason"] = f"Endpoint scheme {endpoint['scheme']!r} is not a local Unix socket"
        return status
    try:
        pinged = ping(endpoint)
    except EndpointError as error:
        status["telemetry_source"] = "cli_snapshot"
        status["reason"] = str(error)
        return status
    status["ping_api_version"] = pinged["api_version"]
    chosen = pinged["api_version"] or server_api
    try:
        version = parse_version(chosen)
        minimum = parse_version(server_min) if server_min else STATS_API_FLOOR
    except EndpointError as error:
        status["telemetry_source"] = "cli_snapshot"
        status["reason"] = str(error)
        return status
    if version < STATS_API_FLOOR:
        status["telemetry_source"] = "cli_snapshot"
        status["reason"] = (f"Engine API {chosen} is below the supported streaming floor "
                            f"{STATS_API_FLOOR[0]}.{STATS_API_FLOOR[1]}")
        return status
    if version < minimum:
        status["telemetry_source"] = "cli_snapshot"
        status["reason"] = f"Engine API {chosen} is below the daemon minimum {server_min}"
        return status
    status["api_version_pinned"] = f"{version[0]}.{version[1]}"
    status["telemetry_source"] = "engine_stream"
    return status


UNAVAILABLE = {
    "2": ["cpu_stats.cpu_usage.percpu_usage", "memory_stats.max_usage",
          "blkio_stats.io_serviced_recursive", "blkio_stats.io_queue_recursive",
          "blkio_stats.io_service_time_recursive", "blkio_stats.io_wait_time_recursive",
          "blkio_stats.io_merged_recursive", "blkio_stats.io_time_recursive",
          "blkio_stats.sectors_recursive"],
    "1": [],
}
SEMANTICS = {
    "2": ["memory_stats.failcnt counts memory.events oom, not cgroup v1 failcnt",
          "memory_stats.stats uses cgroup v2 key names (anon/file/inactive_file)"],
    "1": [],
}


def availability(cgroup_version):
    version = str(cgroup_version) if cgroup_version is not None else ""
    if version not in UNAVAILABLE:
        return {"cgroup_version": cgroup_version or None, "known": False, "unavailable": [],
                "semantics_changed": [],
                "note": "Unrecognised cgroup version; field availability was not asserted"}
    return {"cgroup_version": version, "known": True, "unavailable": list(UNAVAILABLE[version]),
            "semantics_changed": list(SEMANTICS[version]),
            "note": "Absent fields are recorded as null and listed here; they are never zero"}


def _read_time(sample):
    value = sample.get("read")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _path(sample, *keys):
    for key in keys:
        if not isinstance(sample, dict) or key not in sample:
            return None
        sample = sample[key]
    return sample


def derive(samples):
    usable = [s for s in samples if not s["degraded"]]
    def counter(value):
        return value if type(value) is int else None
    def observed(*keys):
        values = [counter(_path(s["raw"], *keys)) for s in usable]
        return max((v for v in values if v is not None), default=None)
    result = {"basis": "first_last_retained", "usable_samples": len(usable),
              "note": "Raw counter deltas only. Not a scheduler audit and not a CPU reservation claim.",
              "memory_usage_max_observed": observed("memory_stats", "usage"),
              "pids_max_observed": observed("pids_stats", "current"),
              "periods_delta": None, "throttled_periods_delta": None,
              "throttled_time_ns_delta": None, "throttled_fraction": None, "cpu_total_ns_delta": None}
    if len(usable) < 2:
        return result
    first, last = usable[0]["raw"], usable[-1]["raw"]
    def delta(*keys):
        start, end = counter(_path(first, *keys)), counter(_path(last, *keys))
        if start is None or end is None or end < start:
            return None
        return end - start
    periods = delta("cpu_stats", "throttling_data", "periods")
    throttled = delta("cpu_stats", "throttling_data", "throttled_periods")
    result.update(periods_delta=periods, throttled_periods_delta=throttled,
                  throttled_time_ns_delta=delta("cpu_stats", "throttling_data", "throttled_time"),
                  throttled_fraction=(throttled / periods) if periods and throttled is not None else None,
                  cpu_total_ns_delta=delta("cpu_stats", "cpu_usage", "total_usage"))
    return result


class StatsStream:
    """Bounded, cancel-safe reader for GET /containers/{id}/stats?stream=true.

    The daemon controls the sampling cadence; `interval` is a retention filter applied
    to the engine-reported `read` timestamps, never a request for a rate and never
    interpolation.
    """

    def __init__(self, socket_path, api_version, container_id, interval=0, started=None,
                 max_samples=MAX_SAMPLES, max_total_bytes=MAX_TOTAL_BYTES,
                 max_frame_bytes=MAX_FRAME_BYTES):
        self.socket_path, self.api_version, self.container_id = socket_path, api_version, container_id
        self.interval = interval or 0
        self.started = started if started is not None else time.monotonic()
        self.max_samples, self.max_total_bytes, self.max_frame_bytes = max_samples, max_total_bytes, max_frame_bytes
        self.samples, self.errors = [], []
        self.received = 0
        self.bytes_read = 0
        self.truncated = False
        self.closed_before_cleanup = False
        self.thread_leaked = False
        self._connection = None
        self._thread = None
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self._last_kept = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _open(self):
        connection = UnixHTTPConnection(self.socket_path, READ_TIMEOUT_S)
        with self._guard:
            if self._stop.is_set():
                raise _Cancelled()
            self._connection = connection
        connection.request(
            "GET", f"/v{self.api_version}/containers/{self.container_id}/stats?stream=true")
        return connection.getresponse()

    def _run(self):
        text = codecs.getincrementaldecoder("utf-8")()
        decoder = json.JSONDecoder()
        buffer = ""
        try:
            response = self._open()
            if response.status != 200:
                self.errors.append(f"stats stream returned HTTP {response.status}")
                return
            while not self._stop.is_set():
                chunk = response.read1(65536)
                if not chunk:
                    buffer += text.decode(b"", final=True)
                    if buffer.strip():
                        self.errors.append("stats stream ended inside an incomplete record")
                    return
                self.bytes_read += len(chunk)
                if self.bytes_read > self.max_total_bytes:
                    self.truncated = True
                    return
                buffer += text.decode(chunk)
                while True:
                    stripped = buffer.lstrip()
                    if not stripped:
                        buffer = ""
                        break
                    try:
                        value, index = decoder.raw_decode(stripped)
                    except ValueError:
                        if len(stripped.encode("utf-8")) > self.max_frame_bytes:
                            self.errors.append("stats frame exceeded the buffer bound")
                            self.truncated = True
                            return
                        buffer = stripped
                        break
                    if len(stripped[:index].encode("utf-8")) > self.max_frame_bytes:
                        self.errors.append("stats frame exceeded the buffer bound")
                        self.truncated = True
                        return
                    buffer = stripped[index:]
                    if not self._record(value):
                        return
        except _Cancelled:
            return
        except (OSError, http.client.HTTPException, ValueError, UnicodeDecodeError) as error:
            if not self._stop.is_set():
                self.errors.append(f"{type(error).__name__}: {error}")
        finally:
            self._shutdown_connection(close=True)

    def _record(self, value):
        self.received += 1
        if len(self.samples) >= self.max_samples:
            self.truncated = True
            return False
        if not isinstance(value, dict):
            self.errors.append("stats stream produced a non-object record")
            return True
        # The daemon publishes an otherwise-empty response when its own collection fails.
        degraded = not isinstance(value.get("cpu_stats"), dict) or not isinstance(value.get("memory_stats"), dict)
        read_at = _read_time(value)
        if self.interval and self._last_kept is not None and read_at is not None:
            if read_at - self._last_kept < self.interval:
                return True
        if read_at is not None:
            self._last_kept = read_at
        self.samples.append({"received_elapsed_s": time.monotonic() - self.started,
                             "engine_read": value.get("read"), "engine_preread": value.get("preread"),
                             "degraded": degraded, "raw": value})
        return True

    def _shutdown_connection(self, close=False):
        with self._guard:
            connection = self._connection
            if close:
                self._connection = None
        if connection is None:
            return
        try:
            if connection.sock is not None:
                connection.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        # HTTPResponse owns buffered state that is not thread-safe. The owner reader
        # closes it; the cancelling thread only interrupts the underlying socket.
        if close:
            try:
                connection.close()
            except OSError:
                pass

    def close(self, timeout=5):
        """Always called before container removal so a live stream cannot block cleanup."""
        self._stop.set()
        deadline = time.monotonic() + timeout
        # A connection opened by the reader after the first shutdown must also be closed.
        while True:
            self._shutdown_connection()
            if self._thread is None:
                break
            self._thread.join(.05)
            if not self._thread.is_alive() or time.monotonic() >= deadline:
                break
        self._shutdown_connection()
        alive = self._thread is not None and self._thread.is_alive()
        if alive:
            self.thread_leaked = True
            self.errors.append("stats reader thread did not stop within the join timeout")
        self.closed_before_cleanup = not alive
        return self.result()

    def result(self):
        samples = list(self.samples)
        return {"samples": samples, "errors": list(self.errors),
                "samples_received": self.received, "samples_retained": len(samples),
                "bytes_read": self.bytes_read, "truncated": self.truncated,
                "thread_leaked": self.thread_leaked,
                "closed_before_cleanup": self.closed_before_cleanup,
                "retention_policy": (
                    "All engine samples retained" if not self.interval else
                    f"Retained when the engine read timestamp advanced at least {self.interval}s; "
                    "dropped samples are counted, never interpolated"),
                "derived": derive(samples)}


def utc():
    return datetime.now(timezone.utc).isoformat()
