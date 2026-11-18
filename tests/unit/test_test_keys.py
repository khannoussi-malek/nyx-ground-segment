from fnmatch import fnmatchcase

import pytest

from tests.support import keys

KEYS = [
    "tm:NYX-TA1", "tm:NYX-TA2", "tm:NYX-TA9", "hb:veh:NYX-TA1", "tm:NYX-TDE036594",
    "tcack:NYX-T5D3E73E4", "cmdlock:NYX-T6145D806:isolate_valve", "tm:dlq", "ui:events",
    "tm:NYX-TDE03659", "frame_seq:NYX-TAB12CD34:7", "tm:NYX-Tde036594",
]  # fmt: skip
WANTED = {
    "tm:NYX-TDE036594", "tcack:NYX-T5D3E73E4", "cmdlock:NYX-T6145D806:isolate_valve",
    "frame_seq:NYX-TAB12CD34:7",
}  # fmt: skip


class FakeRedis:
    def __init__(self) -> None:
        self.keys = set(KEYS)
        self.batches: list[int] = []

    def scan_iter(self, match: str, count: int = 0) -> list[str]:
        return [k for k in sorted(self.keys) if fnmatchcase(k, match)]

    def delete(self, *keys: str) -> int:
        self.batches.append(len(keys))
        self.keys -= set(keys)
        return len(keys)


def test_purge_matches_only_test_vehicles() -> None:
    assert set(keys.vehicle_keys(FakeRedis())) == WANTED  # type: ignore[arg-type]


def test_purge_deletes_in_batches_of_500() -> None:
    r = FakeRedis()
    r.keys |= {f"tm:NYX-T{i:08X}" for i in range(1200)}
    assert keys.purge_test_keys(r) == 1200 + len(WANTED)  # type: ignore[arg-type]
    assert r.batches == [500, 500, 204]
    assert {"tm:NYX-TA1", "tm:NYX-TA9", "tm:dlq"} <= r.keys


class FakeStream:
    def __init__(self, pending: int, lag: int) -> None:
        self.group = {"name": "ingest", "pending": pending, "lag": lag}
        self.trims: list[tuple[str, int, bool]] = []

    def xinfo_groups(self, stream: str) -> list[dict[str, object]]:
        return [self.group]

    def xtrim(self, stream: str, maxlen: int, approximate: bool) -> int:
        self.trims.append((stream, maxlen, approximate))
        return 0


def test_trim_only_when_drained(capsys: pytest.CaptureFixture[str]) -> None:
    drained, busy = FakeStream(0, 0), FakeStream(0, 3)
    assert keys.trim_if_drained(drained, "tm:NYX-TA9")  # type: ignore[arg-type]
    assert drained.trims == [("tm:NYX-TA9", 0, False)]
    assert not keys.trim_if_drained(busy, "tm:NYX-TA9")  # type: ignore[arg-type]
    assert busy.trims == [] and "not drained" in capsys.readouterr().err


def test_purge_quietly_survives_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import redis

    from tests.support import redisx

    def down(user: str) -> None:
        raise redis.ConnectionError("down")

    monkeypatch.setattr(redisx, "client", down)
    keys.purge_quietly()  # warns, does not raise
