from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, Lock

from device.recovery_port import Evidence, Exposure, FenceLost, ReplyLost


@dataclass
class ExposureGate:
    ready: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)


class ScriptedHead:
    """An independent device with only the latest exposure visible on its port."""

    def __init__(self, device_id: str = "head-Q", *, generation: str = "G",
                 sequence: int = 0, latest: Exposure | None = None) -> None:
        self._device_id = device_id
        self._evidence = Evidence(generation, sequence, latest)
        self._generations = {generation}
        self._lease = 0
        self._lock = Lock()
        self._loss: str | None = None
        self._gate: ExposureGate | None = None
        self._after: Callable[[], None] | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def takeover(self) -> int:
        with self._lock:
            self._lease += 1
            return self._lease

    def _require_lease(self, lease: int) -> None:
        if type(lease) is not int or lease <= 0 or lease != self._lease:
            raise FenceLost("device lease was superseded")

    def observe(self, lease: int) -> Evidence:
        with self._lock:
            self._require_lease(lease)
            return self._evidence

    def expose(self, lease: int, exposure: Exposure) -> Evidence:
        with self._lock:
            self._require_lease(lease)
            gate = self._gate
            self._gate = None
        if gate is not None:
            gate.ready.set()
            if not gate.release.wait(5):
                raise AssertionError("exposure gate was never released")

        with self._lock:
            self._require_lease(lease)
            loss = self._loss
            self._loss = None
            if loss == "before":
                raise ReplyLost("device reply lost")
            previous = self._evidence
            proof = Evidence(previous.generation, previous.sequence + 1, exposure)
            self._evidence = proof
            after = self._after
            self._after = None
        if after is not None:
            after()
        if loss == "after":
            raise ReplyLost("device reply lost")
        return proof

    def arm_loss(self, phase: str) -> None:
        if phase not in ("before", "after"):
            raise ValueError("loss phase must be before or after exposure")
        with self._lock:
            self._loss = phase

    def hold_next(self) -> ExposureGate:
        with self._lock:
            gate = ExposureGate()
            self._gate = gate
            return gate

    def after_next_exposure(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._after = callback

    def reset(self, generation: str) -> None:
        with self._lock:
            if not generation or generation in self._generations:
                raise ValueError("reset requires a fresh generation")
            self._generations.add(generation)
            self._lease += 1
            self._evidence = Evidence(generation, 0, None)


def check_fixture() -> None:
    previous = Exposure("setup", 8, "Q")
    sample = Exposure("day", 9, "Q")
    start = Evidence("G", 40, previous)
    executed = Evidence("G", 41, sample)
    for phase, expected in (("before", start), ("after", executed)):
        head = ScriptedHead(sequence=40, latest=previous)
        lease = head.takeover()
        head.arm_loss(phase)
        try:
            head.expose(lease, sample)
        except ReplyLost:
            assert head.observe(head.takeover()) == expected
        else:
            raise AssertionError(f"expected a lost reply {phase} exposure")

    head = ScriptedHead(sequence=40, latest=previous)
    old_lease = head.takeover()
    gate = head.hold_next()
    with ThreadPoolExecutor(max_workers=1) as pool:
        delayed = pool.submit(head.expose, old_lease, sample)
        try:
            assert gate.ready.wait(5), "old command never reached the gate"
            new_lease = head.takeover()
            assert head.observe(new_lease) == start
        finally:
            gate.release.set()
        try:
            delayed.result(timeout=5)
        except FenceLost:
            assert head.observe(new_lease) == start
        else:
            raise AssertionError("late command survived takeover")

    seen = []
    head.after_next_exposure(lambda: seen.append(head.observe(new_lease)))
    assert head.expose(new_lease, sample) == executed
    assert seen == [executed]
    assert head.observe(head.takeover()) == executed
    assert head.expose(head.takeover(), sample) == Evidence("G", 42, sample)
    head.reset("H")
    assert head.observe(head.takeover()) == Evidence("H", 0, None)
    print("Device fixture: lost replies preserve sequence 40 or 41; takeover fences the late command.")


if __name__ == "__main__":
    check_fixture()
