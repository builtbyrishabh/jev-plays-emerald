"""Route Jev decisions through the TypeScript decision service.

The service is a `node` child speaking newline-delimited JSON on stdin/stdout,
started and owned by this process. It inherits `AI_GATEWAY_API_KEY` from the
environment, so the key never appears in an argument list, a log line, or this
module.

Blocking reads are deliberate. The mode already confines every request to one
worker thread with at most one call in flight, so a blocking exchange under a
lock is simpler and easier to reason about than a second async runtime.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from time import monotonic, perf_counter
from types import MappingProxyType

from jev_plays_emerald.jev import (
    JevChoice,
    JevGateway,
    JevGatewayError,
    JevTimeoutError,
    JsonValue,
    TokenUsage,
    validate_choice,
)
from jev_plays_emerald.planner import PlannerAdvice, planner_model

SERVICE_FLAG = "JEV_DECISION_SERVICE"
SERVICE_DIR = Path(__file__).resolve().parents[2] / "service"
DEFAULT_TIMEOUT_SECONDS = 30.0
# Startup only has to prove the child runs and agrees about the action schema.
HANDSHAKE_TIMEOUT_SECONDS = 20.0


class DecisionService:
    """A `ChoiceClient` backed by the supervised TypeScript service."""

    def __init__(
        self,
        *,
        command: tuple[str, ...] = ("node", "src/index.ts"),
        service_dir: Path = SERVICE_DIR,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._command = command
        self._service_dir = service_dir
        self._timeout_seconds = timeout_seconds
        self._process: subprocess.Popen[bytes] | None = None
        self._selector = selectors.DefaultSelector()
        self._buffer = b""
        self._counter = 0
        self._lock = Lock()

    def start(self) -> int:
        """Launch the child and confirm it speaks the expected action schema.

        Failing here is the point: a decision layer that cannot start should
        stop the launcher, not the first time the game needs a choice.
        """

        if self._process is not None:
            raise RuntimeError("the decision service is already running")
        self._process = subprocess.Popen(
            list(self._command),
            cwd=self._service_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=os.environ.copy(),
        )
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        response = self._exchange({"type": "ping"}, HANDSHAKE_TIMEOUT_SECONDS)
        if response.get("type") != "pong":
            raise JevGatewayError("the decision service did not answer its handshake")
        version = response.get("schemaVersion")
        if not isinstance(version, int):
            raise JevGatewayError("the decision service reported no action schema version")
        return version

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        self._selector.unregister(process.stdout)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    async def choose(
        self,
        *,
        state: JsonValue,
        options: Mapping[str, JsonValue | None],
        instructions: JsonValue,
    ) -> JevChoice:
        if not options:
            raise ValueError("at least one choice option is required")

        started = perf_counter()
        response = self._exchange(
            {
                "type": "choose",
                "state": state,
                "options": dict(options),
                "instructions": instructions,
                "timeoutMs": int(self._timeout_seconds * 1000),
            },
            self._timeout_seconds,
        )
        latency_ms = (perf_counter() - started) * 1000

        if response.get("type") == "error":
            raise _service_error(response)
        if response.get("type") != "choice":
            raise JevGatewayError(f"unexpected response type: {response.get('type')!r}")

        # The same acceptance rules as the direct gateway client, so switching
        # implementations cannot quietly widen what counts as a valid answer.
        choice = validate_choice(dict(response), set(options))
        probabilities = MappingProxyType(
            {action_id: float(value) for action_id, value in response["probabilities"].items()}
        )
        confidence = response.get("confidence")
        usage = response.get("usage") or {}
        return JevChoice(
            choice=choice,
            probabilities=probabilities,
            confidence=float(confidence) if confidence is not None else None,
            usage=TokenUsage(usage.get("inputTokens"), usage.get("outputTokens")),
            latency_ms=latency_ms,
        )

    async def plan(self, *, state: JsonValue, options: dict, instructions: str) -> PlannerAdvice:
        response = self._exchange({
            "type": "plan", "state": state, "options": options,
            "instructions": instructions, "timeoutMs": int(self._timeout_seconds * 1000),
        }, self._timeout_seconds)
        if response.get("type") == "error":
            raise _service_error(response)
        required = ("hint", "destinationActionId", "location", "avoid", "successSignal")
        model = response.get("model")
        if (response.get("type") != "plan"
                or any(not isinstance(response.get(key), str)
                       or not response[key].strip() for key in required)
                or model != planner_model()):
            raise ValueError("invalid planner advice")
        if response["destinationActionId"] not in options:
            raise ValueError("planner destination is not a legal action")
        usage = response.get("usage") or {}
        return PlannerAdvice(
            hint=response["hint"],
            destination_action_id=response["destinationActionId"],
            location=response["location"],
            avoid=response["avoid"],
            success_signal=response["successSignal"],
            model=model,
            usage=TokenUsage(usage.get("inputTokens"), usage.get("outputTokens")),
            latency_ms=response.get("latencyMs", 0),
        )

    def _exchange(self, payload: dict[str, object], timeout_seconds: float) -> dict:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise JevGatewayError("the decision service is not running")

        with self._lock:
            self._counter += 1
            request_id = str(self._counter)
            line = json.dumps({"id": request_id, **payload}, separators=(",", ":"))
            try:
                process.stdin.write(f"{line}\n".encode())
                process.stdin.flush()
            except (BrokenPipeError, ValueError) as error:
                raise JevGatewayError("the decision service stopped accepting requests") from error

            deadline = monotonic() + timeout_seconds
            while True:
                response = json.loads(self._read_line(deadline, timeout_seconds))
                # A late answer to an abandoned request must not be mistaken for
                # this one's; the mode's staleness check is the layer above.
                if response.get("id") == request_id:
                    return response

    def _read_line(self, deadline: float, timeout_seconds: float) -> bytes:
        process = self._process
        assert process is not None and process.stdout is not None
        while b"\n" not in self._buffer:
            remaining = deadline - monotonic()
            if remaining <= 0 or not self._selector.select(remaining):
                raise JevTimeoutError(
                    f"the decision service did not answer within {timeout_seconds:g} seconds"
                )
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise JevGatewayError("the decision service exited")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line


def _service_error(response: dict) -> Exception:
    message = str(response.get("message") or "the decision service reported an error")
    kind = response.get("kind")
    if kind == "timeout":
        return JevTimeoutError(message)
    if kind == "invalid":
        return ValueError(message)
    status_code = response.get("statusCode")
    return JevGatewayError(message, status_code=status_code if isinstance(status_code, int) else None)


_SERVICE: DecisionService | None = None


def service_enabled() -> bool:
    """Keep the direct Python gateway client the default while both exist."""

    return os.environ.get(SERVICE_FLAG) == "1" or planner_model() is not None


def default_choice_client():
    """The client the mode uses when it was not given one explicitly."""

    global _SERVICE
    if not service_enabled():
        return JevGateway()
    if _SERVICE is None:
        service = DecisionService()
        service.start()
        _SERVICE = service
    return _SERVICE
