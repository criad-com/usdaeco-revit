"""Serialized, fail-stop REPL transport. A mutation is never replayed.

The endpoint lock covers status, execution and every reply page, across both
threads and processes. A transport failure latches the endpoint closed until an
operator has inspected the native document and removed the reported stop file.
"""

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import io
import gzip
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


class ReplError(RuntimeError):
    pass


class ReplStopped(ReplError):
    """No more calls are safe until the native outcome has been inspected."""


class ReplBusy(ReplError):
    """Busy deadline expired without submitting the requested script."""


_locks = {}
_guard = threading.Lock()


def http(method, url, payload, timeout):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None and len(data) >= 1024 * 1024:
        raise ValueError("REPL request exceeds 1 MiB")
    request = Request(url, data, {"Content-Type": "application/json"}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if "Too many pending" in body:
            return {"error": "Too many pending"}
        raise ReplError(f"REPL HTTP {exc.code}: {body[:300]}") from exc


def busy(status):
    if "too many pending" in json.dumps(status).lower():
        return True
    if not isinstance(status, dict):
        raise ReplError("REPL status must be a JSON object")
    if status.get("error"):
        raise ReplError(f"REPL status error: {status['error']}")
    return any(
        status.get(key) is True
        or isinstance(status.get(key), (int, float))
        and status[key] > 0
        for key in ("busy", "isBusy", "pending", "pendingCount", "pendingRequests")
    ) or str(status.get("status", "")).lower() in ("busy", "pending", "executing")


def script_pack(request, key):
    pack = files(__package__).joinpath("scripts")
    # The REPL compiles each request as one C# script; assemblies beyond its defaults need #r at the top.
    hashes = json.loads(pack.joinpath("manifest.json").read_text())
    if set(hashes) != {p.name for p in pack.iterdir() if p.name.endswith(".csx")}:
        raise ReplError("Script pack manifest differs from available scripts")
    for name, expected in hashes.items():
        if hashlib.sha256(pack.joinpath(name).read_bytes()).hexdigest() != expected:
            raise ReplError("Script pack checksum mismatch: " + name)
    source = "#r \"System.Security.Cryptography\"\n#r \"System.IO.Compression\"\n" + "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(pack.iterdir(), key=lambda p: p.name)
        if p.name.endswith(".csx")
    )
    encoded = base64.b64encode(json.dumps(request, allow_nan=False).encode()).decode()
    return source + (
        "\nreturn AecoRevit.Transport(uiapp, "
        f'System.Text.Encoding.UTF8.GetString(System.Convert.FromBase64String("{encoded}")), "{key}");'
    )


class ReplClient:
    def __init__(
        self,
        endpoint=None,
        *,
        request=http,
        sleep=time.sleep,
        clock=time.monotonic,
        lock_directory=None,
        busy_timeout=180,
        status_timeout=45,
        health_timeout=180,
        eval_timeout=60,
        max_reply=256 * 1024 * 1024,
        poll_interval=120,
        session="aeco-revit",
        workdir=None,
    ):
        self.endpoint = (endpoint or os.environ.get("AECO_REVIT_ENDPOINT", "")).rstrip(
            "/"
        )
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Set AECO_REVIT_ENDPOINT to the REPL URL")
        self.session = session
        self.workdir = workdir or os.environ.get("AECO_REVIT_WORKDIR", "")
        self.request, self.sleep, self.clock = request, sleep, clock
        self.busy_timeout, self.status_timeout = busy_timeout, max(45, status_timeout)
        self.health_timeout = health_timeout
        self.eval_timeout, self.max_reply = eval_timeout, max_reply
        self.poll_interval = max(120 if request is http else 0, poll_interval)
        directory = Path(
            lock_directory or Path(tempfile.gettempdir()) / "usdaeco-revit-locks"
        )
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha256(self.endpoint.encode()).hexdigest()
        self.lock_path, self.stop_path = directory / (key + ".lock"), directory / (
            key + ".stopped"
        )
        self.poll_path = directory / (key + ".status-time")
        with _guard:
            self.thread_lock = _locks.setdefault(str(self.lock_path), threading.Lock())
        self.calls = 0

    @contextmanager
    def serialized(self):
        with self.thread_lock, self.lock_path.open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if self.stop_path.exists():
                    raise ReplStopped(
                        f"REPL stopped; inspect the native outcome before removing {self.stop_path}"
                    )
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _stop(self, exc):
        self.stop_path.write_text(
            "REPL outcome requires inspection. No automatic retry.\n" + str(exc) + "\n"
        )
        raise ReplStopped(f"REPL stopped: {exc}; stop file: {self.stop_path}") from exc

    def _ready(self):
        deadline, delay = self.clock() + self.busy_timeout, 1.0
        failed_since = None
        while True:
            # Co-tenant rule: after ANY client instance has seen the REPL busy,
            # every instance waits poll_interval before the next status poll.
            # An idle REPL is polled once per step without delay (reply pages
            # would otherwise cost poll_interval each).
            if self.poll_path.exists():
                last = min(self.clock(), float(self.poll_path.read_text()))
                remaining = self.poll_interval - (self.clock() - last)
                while remaining > 0:
                    self.sleep(min(60, remaining))
                    remaining = self.poll_interval - (self.clock() - last)
            started = self.clock()
            try:
                status = self.request(
                    "GET", self.endpoint + "/status", None, self.status_timeout
                )
                waiting = busy(status)
            except Exception as exc:
                if failed_since is None:
                    failed_since = started
                elapsed = self.clock() - failed_since
                # A slow status is expected. Only a continuous failure window
                # longer than three minutes, with no eval in flight, stops us.
                if elapsed > self.health_timeout:
                    self._stop(exc)
                self.sleep(
                    min(delay, max(0.001, self.health_timeout - elapsed + 0.001))
                )
                delay = min(delay * 2, 10)
                continue
            failed_since = None
            if not waiting:
                return status
            self.poll_path.write_text(str(self.clock()))
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ReplBusy("REPL remained busy; no script submitted")
            self.sleep(min(delay, remaining))
            delay = min(delay * 2, 10)

    def _eval(self, code, session):
        self._ready()
        try:
            self.calls += 1
            response = self.request(
                "POST",
                self.endpoint + "/eval",
                {"session": session, "code": code},
                self.eval_timeout,
            )
            if (
                not isinstance(response, dict)
                or response.get("error")
                or "result" not in response
            ):
                # Even a queue race is not replayed: only the status poll backs off.
                raise ReplError(f"Unsuccessful REPL evaluation: {str(response)[:600]}")
            return response["result"]
        except Exception as exc:
            self._stop(exc)

    def exchange(self, request):
        """One JSON mutation/snapshot request, one checksummed logical JSON reply.

        Further evaluations only read byte ranges from the completed receipt.
        No Revit API is used by those page scripts.
        """
        key = "usdaeco-reply-" + uuid.uuid4().hex
        source = script_pack(request, key)  # validate/build before touching transport
        with self.serialized():
            try:
                header = self._eval(source, key)
                header = json.loads(header) if isinstance(header, str) else header
                size = header["bytes"]
                if (
                    not isinstance(size, int)
                    or not 0 < size <= self.max_reply
                    or header["key"] != key
                ):
                    raise ReplError("Invalid reply envelope")
                chunks = []
                if request.get("compactReply"):
                    print(f"== stage: Revit reply {size} bytes, {(size + 2399) // 2400} pages", flush=True)
                for offset in range(0, size, 2400):
                    length = min(2400, size - offset)
                    code = (
                        f'var bytes = (byte[])System.AppDomain.CurrentDomain.GetData("{key}");\n'
                        f'if (bytes == null || bytes.Length != {size}) throw new System.Exception("Reply cache missing");\n'
                        f"var page = System.Convert.ToBase64String(bytes, {offset}, {length});\n"
                        + (
                            f'System.AppDomain.CurrentDomain.SetData("{key}", null);\n'
                            if offset + length == size
                            else ""
                        )
                        + "return page;"
                    )
                    part = base64.b64decode(self._eval(code, key), validate=True)
                    if len(part) != length:
                        raise ReplError("Truncated reply page")
                    chunks.append(part)
                body = b"".join(chunks)
                if hashlib.sha256(body).hexdigest() != header["sha256"]:
                    raise ReplError("Reply checksum mismatch")
                if header.get("encoding", "json") == "gzip":
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                        body = stream.read(self.max_reply + 1)
                    if len(body) > self.max_reply:
                        raise ReplError("Expanded reply exceeds size limit")
                elif header.get("encoding", "json") != "json":
                    raise ReplError("Unknown reply encoding")
                receipt = json.loads(body)
                if not isinstance(receipt, dict):
                    raise ReplError("Reply must be a JSON object")
                return receipt
            except (ReplStopped, ReplBusy):
                raise
            except Exception as exc:
                self._stop(exc)


    def evaluate(self, code):
        """One small evaluation using the same endpoint lock and stop latch."""
        with self.serialized():
            return self._eval(code, self.session)

    def phase(self, source, *, library=False):
        """Run a builder script once and verify its UTF-8 result by SHA-256."""
        key = "usdaeco-phase-" + uuid.uuid4().hex
        lines = source.rstrip().splitlines()
        expression = '\"helpers loaded\"' if library else lines.pop().strip().rstrip(";")
        prefix = source if library else "\n".join(lines)
        code = '#r "System.Security.Cryptography"\n' + prefix + "\n" + (
            'var phaseBytes = System.Text.Encoding.UTF8.GetBytes(System.Convert.ToString(' + expression + '));\n'
            f'System.AppDomain.CurrentDomain.SetData("{key}", phaseBytes);\n'
            'return System.Text.Json.JsonSerializer.Serialize(new {bytes=phaseBytes.Length, '
            'sha256=System.Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(phaseBytes)).ToLowerInvariant()});'
        )
        with self.serialized():
            try:
                header = self._eval(code, self.session)
                header = json.loads(header) if isinstance(header, str) else header
                size = header["bytes"]
                if type(size) is not int or not 0 <= size <= self.max_reply:
                    raise ReplError("Invalid phase reply length")
                chunks = []
                for offset in range(0, size, 2400):
                    length = min(2400, size - offset)
                    page = self._eval(f'return System.Convert.ToBase64String((byte[])System.AppDomain.CurrentDomain.GetData("{key}"), {offset}, {length});', self.session)
                    chunk = base64.b64decode(page, validate=True)
                    if len(chunk) != length:
                        raise ReplError("Truncated phase reply page")
                    chunks.append(chunk)
                body = b"".join(chunks)
                if hashlib.sha256(body).hexdigest() != header["sha256"]:
                    raise ReplError("Phase reply checksum mismatch")
                self._eval(f'System.AppDomain.CurrentDomain.SetData("{key}", null); return "released";', self.session)
                return body.decode("utf-8")
            except (ReplBusy, ReplStopped):
                raise
            except Exception as exc:
                self._stop(exc)

    def upload(self, data, filename):
        """Upload to AECO_REVIT_WORKDIR and verify the complete remote bytes."""
        if not self.workdir:
            raise ValueError("Set AECO_REVIT_WORKDIR before uploading")
        if not filename or filename in (".", "..") or any(c in filename for c in ("/", "\\", ":")):
            raise ValueError("Upload filename must be a basename")
        data = bytes(data)
        target = 'System.IO.Path.Combine(' + json.dumps(self.workdir) + ', ' + json.dumps(filename) + ')'
        with self.serialized():
            self._eval('System.IO.Directory.CreateDirectory(' + json.dumps(self.workdir) + '); System.IO.File.WriteAllBytes(' + target + ', new byte[0]); return "created";', self.session)
            for offset in range(0, len(data), 400000):
                encoded = base64.b64encode(data[offset:offset + 400000]).decode()
                self._eval('using (var upload = new System.IO.FileStream(' + target + ', System.IO.FileMode.Append)) { var chunk = System.Convert.FromBase64String(' + json.dumps(encoded) + '); upload.Write(chunk, 0, chunk.Length); } return "appended";', self.session)
            observed = self._eval('#r "System.Security.Cryptography"\nreturn System.Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.IO.File.ReadAllBytes(' + target + '))).ToLowerInvariant();', self.session)
            if observed != hashlib.sha256(data).hexdigest():
                self._stop(ReplError("Upload checksum mismatch"))
        return {"filename": filename, "bytes": len(data), "sha256": observed}


class Client(ReplClient):
    """Builder compatibility facade over the canonical serialized transport."""
    def __init__(self, endpoint=None, session="dc-build", **kwargs):
        super().__init__(endpoint, session=session, **kwargs)
