# Research scripts

Reproduce the measurements and simulations quoted in the research notes. Run from the repo root.

| Script | Note | What it shows | Run |
| --- | --- | --- | --- |
| `r20_xu_chain.py` | R20 F1–F2 | Real engine vs Xu 2012's n-sample on/off-delay Markov chain; noise margins per parameter | `PYTHONPATH=. uv run python docs/research/scripts/r20_xu_chain.py` |
| `r20_chatter_at_limit.py` | R20 F3 | Clear/re-raise cycles per 8 h with the value parked on the limit, at 1×/2×/4× simulator noise | same pattern |
| `r21_redis_count_probe.sh` | R21 F2 | `times_delivered` after each Redis command (throwaway container) | `sh docs/research/scripts/r21_redis_count_probe.sh` |
| `r21_drain_sim.py` | R21 F2 | Single / batch / adaptive drain vs process-killing frames | `python3 docs/research/scripts/r21_drain_sim.py` |
| `r22_slow_source.py` | R22 F2 | A source slower than 2 s per frame never raises an alarm | `PYTHONPATH=. uv run python docs/research/scripts/r22_slow_source.py` |

All five reproduce their notes' numbers on `main` at v1.0.1 (checked 2026-11-19). The engine scripts call the private `_slot_step` (it returns `(slot, value)` since Gate 3b); update them if its signature changes.

`build_sources.py` re-exports the PaperLab PDFs into `../papers/` (git-ignored) and regenerates `../SOURCES.md`. It needs a running PaperLab stack and two environment variables. It finds the repo from its own location, so it runs from any directory:

```sh
export PAPERLAB_COMPOSE=<path to PaperLab's docker-compose.yml>   # required, no default
export PAPERLAB_MCP_CMD="[\"docker\",\"compose\",\"-f\",\"$PAPERLAB_COMPOSE\",\"exec\",\"-T\",\"api\",\"python\",\"-m\",\"mcp_server\"]"
python3 docs/research/scripts/build_sources.py
```

`PLAB_DIR` overrides where the paperlab-research skill's `plab.py` lives (default `~/.claude/skills/paperlab-research/scripts`). Web documents missing from `../papers/web/` are downloaded again from their URLs.
