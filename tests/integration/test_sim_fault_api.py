import asyncio
import json

from tests.support.env import vid
from tests.support.sim import delete_fault, post_fault, run_sim_with, running_sim, seqs_of


async def test_dropout_10s_drops_100_frames_seq_continues() -> None:
    v = vid()
    seqs = seqs_of(await run_sim_with(v, 13, 1.0, ("dropout", {"duration_s": 10})))
    jump = [b - a for a, b in zip(seqs, seqs[1:], strict=False) if b - a != 1]
    assert len(jump) == 1 and abs(jump[0] - 101) <= 1


async def test_duplicate_is_byte_identical() -> None:
    raws = await run_sim_with(vid(), 2.5, 1.0, ("duplicate", {"count": 2}))
    dups = [i for i in range(2, len(raws)) if raws[i] == raws[i - 1] == raws[i - 2]]
    assert len(dups) == 1 and len(set(raws)) == len(raws) - 2


async def test_malformed_extra_entry_seq_unchanged() -> None:
    raws = await run_sim_with(vid(), 2.5, 1.0, ("malformed", {}))
    bad = [r for r in raws if not r.endswith("}")]
    assert len(bad) == 1 and bad[0].endswith('"seq": ')
    seqs = seqs_of(raws)
    assert seqs == list(range(len(seqs)))


async def test_malformed_schema_kind_has_string_seq() -> None:
    raws = await run_sim_with(vid(), 2.5, 1.0, ("malformed", {"kind": "schema"}))
    strs = [r for r in raws if isinstance(json.loads(r)["seq"], str)]
    assert len(strs) == 1 and json.loads(strs[0])["seq"] == "5"
    seqs = seqs_of(raws)
    assert seqs == list(range(len(seqs)))


async def test_seq_jump_by_1000() -> None:
    seqs = seqs_of(await run_sim_with(vid(), 2.5, 1.0, ("seq_jump", {})))
    assert [b - a for a, b in zip(seqs, seqs[1:], strict=False) if b - a != 1] == [1001]


async def test_freeze_repeats_values_while_seq_advances() -> None:
    raws = await run_sim_with(vid(), 3, 1.0, ("freeze", {"duration_s": 1}))
    frames = [json.loads(r) for r in raws]
    same = [
        (a, b)
        for a, b in zip(frames, frames[1:], strict=False)
        if a["cabin_temp_c"] == b["cabin_temp_c"] and b["seq"] == a["seq"] + 1
    ]
    assert len(same) >= 8


async def test_post_422_and_delete_204() -> None:
    v = vid()
    async with running_sim(v) as port:
        t = asyncio.to_thread
        assert await t(post_fault, v, "dropout", port) == 422  # duration required
        assert await t(post_fault, v, "pause", port, n=1) == 422  # day 3
        assert await t(post_fault, "NYX-OTHER", "freeze", port) == 422  # not in SIM_VEHICLES
        assert await t(post_fault, v, "freeze", port) == 204
        assert await t(delete_fault, v, "freeze", port) == 204
        assert await t(delete_fault, v, "freeze", port) == 204  # idempotent
        assert await t(delete_fault, "NYX-OTHER", "freeze", port) == 422
