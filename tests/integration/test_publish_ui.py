import json

from jsonschema import Draft202012Validator

from gs.common.interfaces.sse import EventView, Link
from gs.common.interfaces.ui_event import Resync
from gs.common.uievents import UI_EVENTS, publish_ui
from tests.support.env import vid
from tests.support.redisx import aclient, load_schema, stream_entries


async def test_publish_ui_one_entry_per_message_matching_schema() -> None:
    v = vid()
    r = aclient("ingest")
    await publish_ui(r, [])  # no-op
    await publish_ui(
        r,
        [
            Resync(vehicle=v),
            Link(
                event=EventView(
                    key=f"GAP/{v}/x/1", vehicle=v, kind="GAP", t_onboard=None, details={"lost": 3}
                )
            ),
        ],
    )
    mine = [json.loads(d["data"]) for _, d in stream_entries(UI_EVENTS) if v in d["data"]][-2:]
    assert [m["kind"] for m in mine] == ["resync", "link"]
    for m in mine:
        Draft202012Validator(load_schema("ui-event")).validate(m)
