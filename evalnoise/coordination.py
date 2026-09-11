"""Advisory single-runner coordination keyed by daemon ID.

This is cooperation between EvalNoise processes run by the same local user against the
same engine. `flock` is host-local kernel state and the container check only sees
EvalNoise-labelled containers, so this is explicitly NOT a distributed lock and does not
exclude other tenants, other users, or other tools sharing the daemon.
"""

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import uuid

LABEL_RUN = "io.evalnoise.run"
LABEL_ENGINE = "io.evalnoise.engine"
LABEL_OWNER = "io.evalnoise.owner"
SCOPE = ("Advisory coordination between cooperating EvalNoise processes for one local user "
         "and one daemon ID. Not a distributed lock; other users, hosts, or tools sharing "
         "this daemon are not excluded and host load remains uncontrolled.")


class CoordinationError(RuntimeError):
    pass


def state_directory():
    root = os.environ.get("EVALNOISE_STATE_DIR") or os.environ.get("XDG_STATE_HOME")
    base = Path(root) if root else Path.home() / ".local" / "state"
    if not root or os.environ.get("XDG_STATE_HOME") == root:
        base = base / "evalnoise"
    directory = base
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def lock_path(engine_id):
    digest = hashlib.sha256(str(engine_id).encode()).hexdigest()[:16]
    return state_directory() / f"engine-{digest}.lock"


class EngineLock:
    def __init__(self, engine_id):
        if not engine_id:
            raise CoordinationError("A daemon ID is required to coordinate runs on one engine")
        self.engine_id = engine_id
        self.path = lock_path(engine_id)
        self.token = uuid.uuid4().hex
        self.held = False
        self._handle = None

    def acquire(self, run_id, output):
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.seek(0)
            existing = handle.read()
            handle.close()
            if error.errno not in (errno.EACCES, errno.EAGAIN):
                raise CoordinationError(f"Engine lock unavailable: {error}") from error
            raise CoordinationError(
                f"Another EvalNoise process holds this engine ({self.engine_id}). "
                f"Holder record: {existing.strip() or 'unreadable'}. {SCOPE}") from error
        # The kernel releases this lock if the process dies, including SIGKILL.
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"owner_token": self.token, "pid": os.getpid(),
                                     "host": socket.gethostname(), "run_id": run_id,
                                     "output": str(output), "engine_id": self.engine_id}))
            handle.flush()
        except OSError as error:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            raise CoordinationError(f"Could not record the engine lock holder: {error}") from error
        self._handle = handle
        self.held = True
        return self

    def release(self):
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        self.held = False
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()

    def record(self):
        return {"engine_id": self.engine_id, "owner_token": self.token,
                "lock_path": str(self.path), "held": self.held, "scope": SCOPE}


def live_holder(engine_id):
    path = lock_path(engine_id)
    if not path.exists():
        return None
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.seek(0)
            try:
                return json.loads(handle.read() or "{}")
            except ValueError:
                return {"unreadable": True}
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return None


def engine_containers(backend, engine_id, running_only=True):
    argv = ["ps", "--filter", f"label={LABEL_ENGINE}={engine_id}",
            "--format", "{{json .}}", "--no-trunc"]
    if not running_only:
        argv.insert(1, "--all")
    found = []
    for line in backend.call(argv, 20).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            found.append(json.loads(line))
        except ValueError:
            raise CoordinationError("Unreadable engine container listing; refusing to proceed") from None
    return found


def refuse_orphans(backend, engine_id, run_id):
    others = [c for c in engine_containers(backend, engine_id)
              if (c.get("Labels") or "").find(f"{LABEL_RUN}={run_id}") < 0]
    if others:
        names = ", ".join(sorted(str(c.get("Names") or c.get("ID")) for c in others)[:10])
        raise CoordinationError(
            f"Active EvalNoise containers from another run are present on this engine: {names}. "
            "Inspect them with `evalnoise diagnose`, then remove them with their own run's "
            "`evalnoise cleanup --confirm` before starting a new measurement.")
    return others
