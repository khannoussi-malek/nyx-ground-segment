# EXT-01 Mission Database (after the 47) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Load `ponytail:ponytail` first: every task asks for the minimal solution (stdlib first, no new class or file the interface below does not name).

**Goal:** Build the parts of GS-EXT-01 that are still open after the core `mission/` loader: table calibration (and optionally polynomial), derived parameters, `checks_by_mode`, FROZEN over raw parameters, the calibration/unit audit with the export/compare window flag, the P6 property and the derived/mode parts of P3/P4, and the D-EXT-20 re-run.

**Architecture:** Two pure modules (`gs.common.calibration`, `gs.common.expr`) are validated at load by `gs.common.missiondb` and applied by one function, `gs.ingest.compute.compute`, called by `fold` before the alarm engine. The engine learns the frame `mode` (active check set per parameter) and a raw-side built-in check `calibration_range`. Stored engineering values keep the `<name>` column; calibrated parameters also get `<name>_raw`.

**Tech Stack:** Python 3.14, pydantic (strict, `extra="forbid"`), psycopg, Hypothesis, pytest. No new dependency.

**Spec:** `docs/design.md` §11.3 (D-EXT-01..09, D-EXT-20, GS-EXT-01 acceptance a-c in §11.1/§11.7), §11.5 (P3, P4, P6, D-EXT-14); research `docs/research/R23-mission-database-standards.md` (M1, M3, M4, S1, S2). Task 1 records which R23 items are accepted; later tasks argue from the edited rows.

## Global Constraints

- Test first and watch it fail; every change ships with its test in the same commit (D-ENG-14/15). A change to a `D-*` row updates `docs/design.md` in the same commit.
- Commits: `feat|fix|refactor|docs|test|chore|perf|ci: ...`; no Co-Authored-By, no Claude/AI mention (`scripts/check_commits.py` enforces).
- `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src tests scripts perf chaos` clean. Once the assurance PR has merged, ruff also runs `S` and `C901` (max-complexity 15): keep every new function under it.
- Coverage gate 80 %, branch coverage on; new `except` handlers must be executed by a test (after_coverage check).
- Ponytail: minimal, stdlib first, no new dependency (a new one only if 14 days old); mark deliberate shortcuts with a `ponytail:` comment naming the ceiling.
- Generated files only through `make gen`; CI fails on a diff. Run `make gen` in every task that touches a pydantic model under `src/gs/common/` or `src/gs/common/interfaces/` and commit the result.
- Tests never leave `mission/` edited: mission-db variants are written into `tmp_path` (`tests/support/mission.py`) or a throwaway database; the shipped `mission/` changes only in Tasks 6, 7 and 8, by the task's own commit.
- Must paths change (ingest frame path, alarm engine). Every task touching them names its guard tests; each of those must stay green: `tests/unit/test_fold.py`, `test_engine.py`, `test_frozen_detector.py`, `test_restore_state.py`, `tests/integration/test_frame_txn.py`, `test_alarm_txn.py`, `test_limits_reload.py`, `test_replay_equality.py`, `tests/system` (ATP-02/07/08 flows) and `tests/property`. The chaos soak (`make chaos`) is the regression net for ingest and alarm changes (Task 13).
- Stack ports for this worktree when one is needed: offset +16000 (DB 21432, REDIS 22379, MINIO 25000, SIM 23081, API 24000), compose project `nyx-ext01`. System and e2e runs go through `sh /Users/malek/try/nyx/.superpowers/syslock.sh <cmd>`. Never `down -v`, never touch the paperlab stack.
- Test vehicles come from `tests.support.env.vid()`; live vehicles are `NYX-TA1`, `NYX-TA2`; `NYX-TA9` is recorded-only.
- Hypothesis profile for the property tests is `nyx-ci` (`tests/property/profiles.py`), applied per test with `@profiles.PROFILE`, never loaded globally.
- Name rule `^[a-z][a-z0-9_]{0,62}$`, unique across `parameters`, `derived` and every `<name>_raw`; frame header names are reserved (D-EXT-03). Floats are IEEE 754; `+ - * /` only (no libm) in the base grammar, so recompute is identical on arm64 and amd64 (D-EXT-05, R16).

## Not in this plan

R23 M2 (simulator enforces `allowed_modes`), D-EXT-07/08 (`allowed_modes`, `MODE_*` rejections), A-R23-5, and everything in R24 belong to the separate commanding plan. The `allowed_modes` clause of GS-EXT-01 acceptance (a) and the "mode-restricted command" step of ATP-E1 therefore stay open until that plan lands (Task 12 and 13 say so). XTCE export (R23 S3), `valid_modes` (C1), context calibration (C4) and reload dry run (C5) are not built.

## Review Focus

1. A raw value exactly on a table end, one ULP outside it, NaN or +-inf as a raw sample: on the end it calibrates, outside or non-finite gives engineering NULL + RED OUT OF CALIBRATION RANGE, never an exception and never a DLQ'd frame. Task 2 `test_boundary_table`, Task 5 `test_nonfinite_raw_is_null_not_dlq`.
2. A derived parameter whose input is NULL (out of table, missing, or itself NULL) is NULL, never the last value, and its `rate_of` window resets. Task 6 `test_null_input_gives_null_not_last`, Task 7 `test_window_resets_on_null_input`.
3. A hostile or oversized `expr` (deep nesting, huge literal, `**`, attribute, call outside the whitelist, `sqrt(-1)`, `1/0`, overflow): the set is rejected at load or the value is NULL; ingest never raises. Task 3 and Task 6 `test_bad_expr_never_dlqs`.
4. A mode change, a reload and a restart landing on the same batch: the active check set, the debounced level and the open alarm are identical to the uninterrupted run. Task 8 `test_mode_switch_slot_rules`, Task 11 P4.
5. A recalibration or unit change inside an export or compare window: the result carries both `parameters_sha256` values and says so; stored values are not rewritten. Task 9 `test_window_spanning_two_hashes_flagged`.

---

### Task 1: Design dispositions (docs only, before any code)

**Files:**
- Modify: `docs/design.md` rows D-EXT-04, D-EXT-05, D-EXT-06, D-EXT-09 (lines ~886-891), the §4 cut-list row at ~523 (polynomial, functions), §12 rows EX-22 and EX-11 (disposition note), and the §11.3 "Planned tests" bullets for `test_calibration.py` / `test_derived_expr.py` / `test_mode_checks.py`.

**Interfaces:** Produces the written decisions every later task cites. No code.

- [ ] **Step 1: Edit the rows.** Each edit names its R23 reference in the row's Src cell and adds to the "We build" text:
  - D-EXT-04: M1 (a calibration table or `unit` change is audited and flagged, Task 9); A-R23-1 (stored engineering values are never recomputed, raw is the repair path); M4 (an out-of-range input gives NULL derived, never held); S1 **proposed**: `poly: {coefficients: [c0, c1, ...], raw_range: [lo, hi]}` with `raw_range` mandatory, outside it the same NULL + RED path as a table; this reverses EX-22 "table only", so mark it **"owner decision"** and add "built in Task 10 only if accepted". Keep M5's reason text (table outside = ECSS `invalid`).
  - D-EXT-05: S2 whitelist `abs`, `min`, `max`, `sqrt` as `ast.Call` on a fixed name set (`Call` joins the node whitelist; same length/node/depth limits; non-finite to NULL; `sqrt` of a negative = NULL, reason `nonfinite`); note that these four are exact on IEEE 754 (`sqrt` is correctly rounded), so the cross-platform note still holds; M4 NULL propagation pinned by tests.
  - D-EXT-06: M3 `checks_by_mode: {MODE: []}` is legal and means "not monitored in that mode" (slots leave, forced NOMINAL, `MODE_CHANGED`); an absent mode falls back to `checks`; A-R23-3.
  - D-EXT-09: M5 reason kept; FROZEN runs over the raw names only (`MissionDb.raw_names`).
  - Anything that would change a must requirement's verdict: none expected; if any appears while editing, write "owner decision" in the row and add it to the plan's open questions instead of a code task.
- [ ] **Step 2: Run the docs checks.** Run: `uv run pytest tests/meta -q` (the meta tests keep the docs honest). Expected: PASS.
- [ ] **Step 3: Commit.** `git add docs/design.md && git commit -m "docs: record the R23 mission-database dispositions in D-EXT-04/05/06/09"`

---

### Task 2: Calibration pure module

**Files:**
- Create: `src/gs/common/calibration.py`
- Test: `tests/unit/test_calibration.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Table: points: tuple[tuple[float, float], ...]` with `Table.from_pairs(pairs: Sequence[Sequence[float]]) -> Table` (raises `ValueError`: fewer than 2 points, raw not strictly increasing, any non-finite number).
  - `def apply(t: Table, raw: float | None) -> float | None`: linear interpolation, every point exact; `None` for `None`, non-finite, below the first or above the last raw.
  - `def side(t: Table, raw: float | None) -> Literal["LOW", "HIGH"] | None`: `LOW` below, `HIGH` above, `None` inside (end points are inside) or when `raw` is `None`; NaN and -inf are `LOW`, +inf is `HIGH`.
  - `def eng_range(t: Table) -> tuple[float, float]`: min and max of the engineering column (used by the load check in Task 4).

- [ ] **Step 1: Write the failing tests.** `test_boundary_table` (parametrized): a table `[[0, 0], [10, 100], [20, 150]]`: every point exact, `apply(5) == 50.0`, `apply(15) == 125.0`; `apply(-1e-9)` and `apply(20.000000000000004)` are `None`; `apply(nan)`, `apply(inf)` are `None`. `test_side`: 0 and 20 give `None`, `-0.1` `LOW`, `20.1` `HIGH`, `nan` `LOW`, `inf` `HIGH`. `test_table_rejected`: one point, equal raw, decreasing raw, NaN in a point each raise `ValueError`. `test_eng_range_decreasing_table`: `[[0, 5], [1, 1]]` gives `(1.0, 5.0)`.
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_calibration.py -v` Expected: FAIL (module missing).
- [ ] **Step 3: Implement** the four names above in `src/gs/common/calibration.py`. Interpolate with `bisect` over the raw column; one expression `e0 + (raw - r0) * (e1 - e0) / (r1 - r0)`; return the stored engineering value on an exact point (so points are bit-exact).
- [ ] **Step 4: Run.** Same command. Expected: PASS. Also `uv run mypy --strict src/gs/common/calibration.py tests/unit/test_calibration.py`.
- [ ] **Step 5: Commit.** `git add src/gs/common/calibration.py tests/unit/test_calibration.py && git commit -m "feat: table calibration, pure (EXT-01, D-EXT-04)"`

---

### Task 3: Derived-expression pure module

**Files:**
- Create: `src/gs/common/expr.py`
- Test: `tests/unit/test_derived_expr.py`

**Interfaces:**
- Produces:
  - `class ExprError(ValueError)`.
  - `@dataclass(frozen=True) class Expr: names: tuple[str, ...]; def eval(self, values: Mapping[str, float | None]) -> tuple[float | None, str | None]`: `(value, None)` or `(None, reason)` with reason in `missing | div0 | nonfinite | error`.
  - `def compile_expr(text: str) -> Expr`: raises `ExprError` for anything outside the grammar.
- Grammar (D-EXT-05 + S2): `ast.parse(mode="eval")`; node whitelist `Expression, BinOp, UnaryOp, Add, Sub, Mult, Div, USub, Name, Load, Constant, Call`; `Call` only with `func` a `Name` in `{abs, min, max, sqrt}`, no keywords, positional args only (`min`/`max` 2+ args, `abs`/`sqrt` 1); a `Constant` must be `int` or `float`, not `bool`, and converts to a finite `float` at compile time; limits at load: <= 256 characters, <= 64 nodes, depth <= 16. Compile to a closure tree; never `eval`/`exec`. A missing or `None` name -> reason `missing`; `x / 0` -> `div0`; non-finite result or `sqrt(<0)` -> `nonfinite`; any other exception inside `eval` -> `error` (one `except Exception`, ponytail comment, executed by a test).

- [ ] **Step 1: Write the failing tests.** `test_grammar_whitelist` (rejects `a ** 2`, `a.b`, `a < b`, `__import__('os')`, `[a]`, `lambda: 1`, `a if b else c`, `foo(a)`, `abs(a, key=1)`, `min(a)`); `test_load_accepted` (`-(a + 2.5) * b / 3`, `sqrt(a*a + b*b)`, `max(a, abs(b))`); `test_precedence`; `test_div0_gives_div0`; `test_bool_and_huge_literal_rejected` (`True`, `1e999`, `10**400` as literal digits); `test_length_nodes_depth_limits` (257 chars, 65 nodes, depth 17 each rejected, boundary values accepted); `test_overflow_gives_nonfinite` (`a * b` with 1e308 twice); `test_sqrt_negative_nonfinite`; `test_missing_input` (`None` or absent name -> `missing`); `test_any_exception_gives_error` (monkeypatch one closure to raise `RuntimeError`).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_derived_expr.py -v` Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `gs/common/expr.py`; `names` is the ordered, de-duplicated tuple of `Name` ids (the callable names are not inputs).
- [ ] **Step 4: Run** the test file, mypy and ruff (C901: split the node compiler into one small function per node kind). Expected: PASS.
- [ ] **Step 5: Commit.** `git add src/gs/common/expr.py tests/unit/test_derived_expr.py && git commit -m "feat: derived-parameter expression compiler and evaluator (EXT-01, D-EXT-05)"`

---

### Task 4: Mission-db model: calibration, derived, checks_by_mode, names, columns

**Files:**
- Modify: `src/gs/common/missiondb.py` (`ParameterEntry`, `ParametersFile`, `LimitEntry`, `MissionDb`, `_check_floor`, `load`), `src/gs/common/migrate.py:add_columns`, `src/gs/ingest/mission.py:validate_set` (the `telemetry_columns` check at ~line 37-48), regenerated `interfaces/schemas/mission/parameters.schema.json` and `limits.schema.json`.
- Test: `tests/unit/test_missiondb_schema.py`, `tests/unit/test_calibration.py` (load-level cases), `tests/unit/test_mode_checks.py` (load part only), `tests/integration/test_migrate.py`.

**Interfaces:**
- Consumes: `Table.from_pairs`, `eng_range` (Task 2); `compile_expr`, `Expr.names` (Task 3).
- Produces on `MissionDb` (all new, `param_names` keeps its meaning "every stored engineering column", now raw followed by derived):
  - `raw_names: tuple[str, ...]` (the `parameters:` keys = frame fields = FROZEN set);
  - `derived_names: tuple[str, ...]` (file order);
  - `param_names = raw_names + derived_names`;
  - `raw_columns: tuple[str, ...]` (`f"{n}_raw"` for each calibrated raw parameter);
  - `calibrations: Mapping[str, Table]`;
  - `derived: Mapping[str, DerivedSpec]` with `@dataclass(frozen=True) class DerivedSpec: unit: str; expr: Expr | None; rate_of: str | None; window_s: float | None`;
  - `LimitEntry.checks_by_mode: dict[Mode, list[CheckConfig]]` with `Mode = Literal["SAFE","STANDBY","TEST","FAULT"]` (reuse the frame model's `Mode` type), resolved like `checks`; `{MODE: []}` allowed.
- Load rules (each a failing test first): `calibration: {table: [[raw, eng], ...]}` only on a `parameters` entry, any other key (`poly` until Task 10, `frozen_check`) rejected; a table whose engineering range does not cover `red_*` limits plus the deadband of that parameter's checks (default and per mode) rejects the set; `derived:` entries need `unit` and exactly one of `expr` or `rate_of`+`window_s` (`window_s > 0`); every input (`Expr.names`, `rate_of`) names a raw parameter or a derived one **earlier in the file**; a `rate_of` of a derived parameter is allowed, a self reference or forward reference rejects; `limits` keys may be raw or derived; names unique across `parameters`, `derived` and every `<name>_raw` (reserved header names rejected); a set with no raw parameter rejected (D-EXT-09); `checks_by_mode` slots follow the same one-check-per-type and deadband-floor rules as `checks`; `{SAFE: []}` loads and resolves to an empty tuple. Hash and `set_sha256` formulas unchanged (the new keys are inside the same bytes).
- `add_columns` adds, for every `param_names` entry and every `raw_columns` entry, `ADD COLUMN IF NOT EXISTS ... double precision`; `validate_set` requires all of them to exist (existing missing-column behaviour, extended).

- [ ] **Step 1: Write the failing tests** (names): `test_missiondb_schema.py::test_calibration_table_loads`, `::test_unknown_calibration_key_rejected`, `::test_derived_forward_reference_rejected`, `::test_derived_cycle_rejected` (`a: rate_of b`, `b: expr a`), `::test_derived_needs_exactly_one_of_expr_rate_of`, `::test_raw_name_collision_rejected` (`x_raw` as a parameter next to calibrated `x`), `::test_no_raw_parameter_rejected`, `::test_checks_by_mode_empty_list_loads`, `::test_checks_by_mode_floor_checked`; `test_calibration.py::test_table_must_cover_red_limits_plus_deadband`; `tests/integration/test_migrate.py::test_derived_and_raw_columns_added` (throwaway database, existing helper `throwaway_db`).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_missiondb_schema.py tests/unit/test_calibration.py tests/unit/test_mode_checks.py -q` Expected: FAIL.
- [ ] **Step 3: Implement** the model changes and `add_columns`; keep `load` under C901 by moving the new validations into `_check_calibration(p, limits)`, `_check_derived(p)` helpers.
- [ ] **Step 4: Run** the three unit files, the integration test, then `make gen` and the generated-diff check (`git status` shows only the two schema files); guards: `tests/unit/test_fold.py`, `tests/unit/test_frame_model.py`, `tests/integration/test_mission_start.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git add src/gs/common/missiondb.py src/gs/common/migrate.py src/gs/ingest/mission.py interfaces tests && git commit -m "feat: mission db models calibration, derived parameters and checks_by_mode (EXT-01, D-EXT-04/05/06)"`

---

### Task 5: Calibrate in the frame path, store raw, FROZEN over raw, `calibration_range` check

**Files:**
- Create: `src/gs/ingest/compute.py`
- Modify: `src/gs/ingest/fold.py:62-116` (`fold`: values and frozen input), `src/gs/alarms/engine.py` (`CompiledSet`, `compile_set`, `step`), `src/gs/alarms/checks/` (new `calibration_range.py`, registered in `checks/__init__.py`), `src/gs/ingest/store.py:insert_frames`, `src/gs/ingest/worker.py` (`_frame_model(params)` and `ctx.stats.observe` use `raw_names`; `insert_frames` call; `to_tm` keeps `param_names`), `src/gs/replay/run.py` and `src/gs/archiver` readers unchanged (they read `param_names`).
- Test: `tests/unit/test_calibrate_fold.py`, `tests/unit/test_calibration_range_check.py`, `tests/unit/test_frozen_participation.py`, `tests/integration/test_calibrated_param.py`.

**Interfaces:**
- Consumes: `MissionDb.raw_names/calibrations/raw_columns` (Task 4), `calibration.apply/side` (Task 2).
- Produces:
  - `gs.ingest.compute.Computed` = `@dataclass(frozen=True)`: `values: Mapping[str, float | None]` (engineering, every `param_names` entry), `raws: Mapping[str, float | None]` (every raw name), `nulls: tuple[tuple[str, str], ...]` (`(param, reason)` pairs, for counters).
  - `def compute(mdb: MissionDb, frame: FrameHeader) -> Computed`: calibrates calibrated raw parameters, passes uncalibrated ones through, derived parameters are `None` for now (Task 6). Each calibration call is wrapped so any exception gives `None` + reason `error` (ponytail: one `except Exception`, executed by a test).
  - `engine.step(..., values, reset, raws: Mapping[str, float | None] | None = None)`: a check class attribute `on_raw: ClassVar[bool] = False`; a check with `on_raw` is fed `raws[param]`. `calibration_range` (`kind = "calibration_range"`, `on_raw = True`) is appended by `compile_set` to every calibrated parameter, whether or not it has limits, with the parameter's persistence (default from `limits.defaults`); returns `Reading(RED, "LOW"|"HIGH")` from `calibration.side`, NOMINAL inside; trigger text "RED · OUT OF CALIBRATION RANGE LOW/HIGH"; part of the parameter's one alarm (D-ALM-06). `step` with a `None` value keeps the existing NULL rule (D-ALM-04): hold level, reset ring, never clear.
  - `insert_frames(conn, frames, computed: Sequence[Computed], mdb: MissionDb)`: writes `<name>` from `values` and `<name>_raw` from `raws` for `raw_columns`; replaces the `params` argument. FROZEN input in `fold` is the tuple of `raws` in `raw_names` order (bit-identical test as today).
  - ingest counter `calibration_out_of_range{param}` bumped in `ctx.counters` for each `side` hit (key `calibration_out_of_range:<param>`).

- [ ] **Step 1: Write the failing tests.** `test_calibrate_fold.py::test_engineering_values_fed_to_engine` (table `[[0,0],[10,100]]`, raw 5 -> engine sees 50.0); `::test_out_of_table_raw_is_null_not_held`; `test_calibration_range_check.py::test_red_after_n_frames_and_clear_after_n` (persistence 3: RED LOW on the 3rd out-of-table sample, NOMINAL after 3 in-table ones, one alarm shared with a limit check on the same parameter); `::test_no_limits_still_alarms`; `test_frozen_participation.py::test_freeze_detected_on_raw`, `::test_calibration_change_does_not_flap_frozen` (same raw, different table between two polls: FROZEN unaffected), `::test_derived_excluded`; `tests/integration/test_calibrated_param.py::test_raw_and_eng_stored_limits_on_eng_and_out_of_table_red` (throwaway database, test parameter on `NYX-TA9`-style `vid()` vehicle); `::test_nonfinite_raw_is_null_not_dlq` (a `1e999`-shaped value never reaches here: the strict frame model already DLQs non-finite, assert the DLQ reason stays `schema`; and a finite raw outside the table is stored with `<name>` NULL and `<name>_raw` kept).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_calibrate_fold.py tests/unit/test_calibration_range_check.py tests/unit/test_frozen_participation.py -q` Expected: FAIL.
- [ ] **Step 3: Implement** `compute.py`, the check, the engine/fold/store/worker edits as specified. `fold` keeps its signature plus the computed values it now derives itself by calling `compute(mdb, f)`; `insert_frames` receives the same `Computed` list, so the batch computes once (return them from `fold`'s `Outcome` as `computed: tuple[Computed, ...]`, one per new frame, and insert only new frames' computed; LATE frames also insert, so also carry `computed` for them: compute is pure, call it again for those).
- [ ] **Step 4: Run** the new tests, then the guards: `tests/unit/test_fold.py test_engine.py test_frozen_detector.py test_restore_state.py`, `tests/integration/test_frame_txn.py test_alarm_txn.py test_replay_equality.py test_add_parameter` and `tests/property`. Expected: PASS, shipped `mission/` unchanged so no behaviour change for the 47.
- [ ] **Step 5: Commit.** `git add src tests && git commit -m "feat: calibrate in the frame path, store raw, FROZEN over raw, OUT OF CALIBRATION RANGE alarm (EXT-01, D-EXT-04/09)"`

---

### Task 6: Derived `expr` values in the frame path

**Files:**
- Modify: `src/gs/ingest/compute.py` (`compute`), `src/gs/ingest/worker.py` (counter bump), shipped `mission/parameters.yaml` and `mission/limits.yaml` (add `power_w` with the spec's limits, `yellow_high: 250.0, red_high: 300.0, hysteresis_abs: 4.5`), `tests/unit/test_sim_matches_mission_db.py` if it enumerates `param_names` (derived have no simulator nominal: iterate `raw_names`).
- Test: `tests/unit/test_derived_values.py`, `tests/integration/test_derived_store.py`.

**Interfaces:**
- Consumes: `DerivedSpec.expr` (Task 4), `Computed` (Task 5).
- Produces: `compute` fills every `derived` entry with `expr` in file order from `values` (so a derived can use an earlier derived); `nulls` gets `(name, reason)` with `reason in missing | div0 | nonfinite | error`; ingest counter `derived_null:<name>:<reason>`. A frame field named like a derived parameter is already ignored and counted by the frame model's extra handling (D-ING-19): the test below pins it.

- [ ] **Step 1: Write the failing tests.** `test_derived_values.py::test_power_w_bit_equal_product` (float64 `bus_voltage_v * bus_current_a`, 1000 random pairs, `==` not `approx`); `::test_null_input_gives_null_not_last` (frame 1 valid, frame 2 input out of table: derived NULL, not frame 1's value; M4); `::test_chained_derived`; `::test_bad_expr_values_null_with_reason` (div0, overflow, sqrt of negative); `::test_derived_in_frame_is_ignored_and_counted`; `tests/integration/test_derived_store.py::test_power_w_every_row` (one stored row per frame carries `power_w`), `::test_derived_alarm_raised` (drive `bus_current_a` high: RED on `power_w` through the engine, new frames only), `::test_bad_expr_never_dlqs` (a throwaway mission with `expr: a / b`, `b = 0` frames: stored with NULL, `tm:dlq` empty).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_derived_values.py -q` Expected: FAIL.
- [ ] **Step 3: Implement** the `expr` branch in `compute` (one loop over `mdb.derived`); add `power_w` to the shipped mission files (the throwaway-database tests apply `make migrate` equivalent through `migrate.add_columns`).
- [ ] **Step 4: Run** new tests plus guards of Task 5 plus `tests/system` ATP-02/07/08 flows through the syslock wrapper (the shipped set changed). Expected: PASS.
- [ ] **Step 5: Commit.** `git add mission src tests && git commit -m "feat: derived expr parameters computed and stored per frame, power_w shipped (EXT-01, D-EXT-05)"`

---

### Task 7: `rate_of` derived parameters with snapshot state

**Files:**
- Modify: `src/gs/ingest/compute.py`, `src/gs/ingest/fold.py` (`VehicleState`, `dump_vehicle`, `load_vehicle`, `state_version`), `src/gs/alarms/checks/roc.py` (extract the window update + slope into a function the derived path reuses: `def slope(state: RocState, window_s: float, period_s: float) -> float | None`, same 80 % rule), shipped `mission/parameters.yaml` and `limits.yaml` (add `cabin_leak_rate_kpa_s`, `rate_of: cabin_pressure_kpa`, `window_s: 1.0`, no checks).
- Test: `tests/unit/test_derived_rate.py`, `tests/integration/test_derived_store.py::test_leak_rate_during_leak`.

**Interfaces:**
- Produces: `VehicleState.derived: Mapping[str, RocState]` keyed by derived name, persisted under `derived:<name>` in the `engine_state` snapshot and part of `shape_version` (bumping `schema_version` is the existing mechanism: an old snapshot is discarded and rebuilt as today); `compute(mdb, frame, windows: Mapping[str, RocState], late: bool) -> tuple[Computed, Mapping[str, RocState]]` (new windows returned, input untouched). A frame classed new updates the windows; a stored late frame (DUP_OR_LATE) gets `None` with reason `late` and leaves the windows unchanged (EX-13). A NULL input resets that parameter's window (M4). Gap, SEQ_RESET and data-STALE reset windows exactly like the ROC check (`reset` flag from `fold`).

- [ ] **Step 1: Write the failing tests.** `test_derived_rate.py::test_equals_roc_slope_on_same_window` (the derived value equals `statistics.linear_regression` over the same samples and equals the ROC check's reading), `::test_under_80_percent_samples_is_null` (reason `samples`), `::test_window_resets_on_null_input`, `::test_late_frame_null_window_unchanged`, `::test_snapshot_round_trip` (`dump_vehicle` then `load_vehicle` continues the window exactly), `::test_reset_on_gap`; `test_derived_store.py::test_leak_rate_during_leak` (a `leak` of >= 60 s, only windows fully inside it, value near -0.4 kPa/s, EX-28).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_derived_rate.py -q` Expected: FAIL.
- [ ] **Step 3: Implement** the extraction in `roc.py` (existing `tests/unit/test_checks_roc.py` stays green unchanged) and the windows in `fold`.
- [ ] **Step 4: Run** the new tests and guards: `tests/unit/test_checks_roc.py test_fold.py test_restore_state.py test_engine_state.py`, `tests/integration/test_snapshot.py test_replay_equality.py`, `tests/property`, plus the syslock system flows. Expected: PASS.
- [ ] **Step 5: Commit.** `git add mission src tests && git commit -m "feat: rate_of derived parameters with snapshot state and late-frame NULL (EXT-01, D-EXT-05)"`

---

### Task 8: `checks_by_mode` in the engine, the folder and the simulator

**Files:**
- Modify: `src/gs/alarms/engine.py` (`CompiledSet`, `compile_set`, `initial_state`, `step`, `switch_set`, `EngineState`), `src/gs/alarms/contract.py` (`Transition.check_set: str = "default"`), `src/gs/ingest/store.py:write_transitions` (write `check_set`; the column exists in `migrations/02-tables.sql`), `src/gs/ingest/fold.py` (pass `f.mode`), `src/gs/alarms/state.py` (snapshot dump/rebuild of the new field), `src/gs/simulator` (`POST /sim/mode {vehicle, mode}` beside `/sim/fault`, default `TEST`, `frames.py:39` reads it), shipped `mission/limits.yaml` (the SAFE cabin-pressure set from §11.3: `red_low: 90.0, yellow_low: 95.0, yellow_high: 104.0, red_high: 106.0, hysteresis_pct: 15`), `interfaces/` via `make gen` if a model changed.
- Test: `tests/unit/test_mode_checks.py`, `tests/unit/test_engine.py::test_noise_one_alarm` (widened), `tests/unit/test_sim_faults.py` or a new `tests/unit/test_sim_mode.py`, `tests/integration/test_mode_limits.py`.

**Interfaces:**
- Produces: `CompiledSet.by_mode: Mapping[tuple[str, str], tuple[tuple[Check, int], ...]]` (key `(param, MODE)`; an entry with `()` = not monitored); `active(cset, param, mode) -> tuple[tuple[Check, int], ...]`; `EngineState.mode: str | None` (the mode the slots were last judged under). `step(..., mode: str)`: on the first frame whose mode differs from `state.mode` per slot `(param, kind)` apply the reload rule (D-ALM-12): in both sets keep level/side/state and reset the ring; leaves -> forced NOMINAL, the alarm de-escalates or closes with `note = "MODE_CHANGED"`; joins -> `initial()`. Every transition records `check_set` = `"default"` or the mode name; the alarm level text names it ("RED HIGH · SAFE limits"). A mode with `[]` leaves every slot of that parameter. `calibration_range` slots (Task 5) are in every set. Mode comes from the frame, so live, replay and the determinism test choose the same set. The simulator `set_mode` is validated with the frame `Mode` type, `extra="forbid"`, strict.

- [ ] **Step 1: Write the failing tests.** `test_mode_checks.py::test_set_selection_and_fallback` (absent mode uses `checks`), `::test_mode_switch_slot_rules` (a slot in both sets keeps its level and open alarm, ring reset; a leaving slot is NOMINAL with `MODE_CHANGED`; a joining slot starts `initial()`), `::test_empty_mode_list_is_not_monitored` (M3: slot leaves, parameter NOMINAL, alarm closes with `MODE_CHANGED`, no new alarm while in that mode), `::test_check_set_on_rows_and_text`, `::test_deadband_floor_every_set`; `test_engine.py::test_noise_one_alarm` runs over every limit of every check set (both readings); `test_sim_mode.py::test_set_mode_changes_frame_mode_and_defaults_to_test`; `tests/integration/test_mode_limits.py::test_switch_at_first_safe_frame` (sim `set_mode` SAFE: the first stored SAFE frame applies the SAFE set, rows carry `check_set='SAFE'`).
- [ ] **Step 2: Run.** `uv run pytest tests/unit/test_mode_checks.py -q` Expected: FAIL.
- [ ] **Step 3: Implement.** Reuse `switch_set`'s per-slot logic through one shared helper instead of a second state-change path (D-EXT-06: "no second path to test").
- [ ] **Step 4: Run** the new tests and guards: `tests/unit/test_engine.py test_alarm_lifecycle.py test_alarm_tuning.py test_restore_state.py test_engine_state.py`, `tests/integration/test_alarm_txn.py test_limits_reload.py test_replay_equality.py`, `tests/property`, the syslock system flows, then `make gen` shows no diff. Expected: PASS.
- [ ] **Step 5: Commit.** `git add mission src interfaces tests && git commit -m "feat: mode-dependent checks in the engine, check_set on rows, simulator set_mode (EXT-01, D-EXT-06)"`

---

### Task 9: M1 audit of calibration/unit changes and the export/compare window flag

**Files:**
- Modify: `src/gs/api/app.py:105-125` (the `LIMITS_RELOADED` details), `src/gs/api/export.py` and `src/gs/api/export_calc.py`, `src/gs/api/compare.py` and `src/gs/api/compare_calc.py`, `src/gs/common/interfaces/` models for the export header / compare response (new field), regenerated `interfaces/` and `ui/src/server/schema.d.ts` via `make gen`; UI shows the flag only if the existing compare/export UI renders a header (otherwise out of scope: list in the report).
- Test: `tests/unit/test_export_window.py`, `tests/unit/test_compare_buckets.py`, `tests/integration/test_limits_reload.py` (or `test_mission_db_api.py`), `tests/integration/test_export.py`, `tests/integration/test_compare_api.py`.

**Interfaces:**
- Produces: `gs.common.missiondb.changed_parameters(old: MissionDb, new: MissionDb) -> dict[str, list[str]]` (parameter -> subset of `["calibration", "unit"]`); `LIMITS_RELOADED.details["parameters_changed"]` = that dict (empty omitted); `def parameter_hashes(conn, vehicle, t_from, t_to) -> list[str]` reading the `LIMITS_APPLIED` events (`details.parameters_sha256`) in force over the window (the one in force at `t_from` plus any applied inside it); export CSV/Parquet header comments and the compare response carry `parameters_sha256: [..]` and `spans_parameters_change: bool` (true when more than one value). Stored engineering values are never rewritten (A-R23-1).

- [ ] **Step 1: Write the failing tests.** `test_limits_reload.py` (or `test_mission_db_api.py`)`::test_calibration_and_unit_change_audited` (reload with a changed table and a changed `unit`: `LIMITS_RELOADED` lists both parameters and what changed; a `limits.yaml`-only edit lists none); `test_export_window.py::test_window_spanning_two_hashes_flagged`, `::test_single_hash_not_flagged`; `test_compare_buckets.py::test_compare_flags_two_hashes`; `integration/test_export.py::test_header_carries_parameters_sha256`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `changed_parameters` (compare `Table.points` and `unit`), the audit field, and the window helper; reuse the existing window reader, no second reader.
- [ ] **Step 4: Run** the new tests, `make gen` (no diff after committing), the export and compare integration files, `tests/unit/test_replay_rows.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git add src interfaces ui/src/server tests && git commit -m "feat: audit calibration and unit changes and flag export/compare windows spanning two parameter sets (EXT-01, R23 M1)"`

---

### Task 10: Polynomial calibration with mandatory `raw_range` (only if the owner accepts R23 S1)

**Files:**
- Modify: `src/gs/common/calibration.py`, `src/gs/common/missiondb.py` (`calibration` accepts `poly`), `docs/design.md` D-EXT-04 (the "owner decision" mark becomes "accepted <date>").
- Test: `tests/unit/test_calibration.py`.

**Interfaces:**
- Consumes: Task 2 `apply/side/eng_range`, Task 4 load. Produces: `@dataclass(frozen=True) class Poly: coefficients: tuple[float, ...]; raw_range: tuple[float, float]` with the same three functions accepting `Table | Poly` (a `Calibration = Table | Poly` alias); evaluated by Horner (`+ *` only, so platform-deterministic); `raw_range` mandatory with `lo < hi`, coefficients finite, 1 to 6 of them; outside `raw_range` the same `None` + `LOW/HIGH` path as a table (so `calibration_range` works unchanged); `eng_range` computed at load by sampling the endpoints plus the real roots of the derivative in range is **not** built: ponytail, it samples 1024 evenly spaced points (ceiling: a narrow spike between samples; upgrade: exact extrema) and the load check keeps its margin through the red-limit coverage rule.

- [ ] **Step 1: Write the failing tests.** `test_calibration.py::test_poly_boundary_table` (ends exact, mid, just outside -> `None`), `::test_poly_without_raw_range_rejected`, `::test_poly_raw_range_inverted_rejected`, `::test_poly_and_table_together_rejected`, `::test_poly_must_cover_red_limits`, `::test_poly_horner_bit_exact` (cubic against `sum(c * x**i)` via `math.fsum`-free reference within 0 ULP on exactly representable inputs).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** in `calibration.py`; the load rule in `missiondb.py` is `exactly one of table, poly`.
- [ ] **Step 4: Run** `tests/unit/test_calibration.py tests/unit/test_missiondb_schema.py`, `make gen`. Expected: PASS.
- [ ] **Step 5: Commit.** `git add src docs/design.md interfaces tests && git commit -m "feat: polynomial calibration with a mandatory raw_range (EXT-01, R23 S1)"`

---

### Task 11: Properties P6 and the derived/mode parts of P3 and P4

**Files:**
- Modify: `tests/property/test_derived_props.py` (remove the skip), `tests/property/test_engine_props.py` (P3/P4 header note and tests), `tests/property/strategies.py` (derived and mode draws), `tests/property/folding.py` (mode per frame), `tests/unit/test_property_strategies.py` (`test_tuning_draws_respect_floor` covers the new draws).
- Test: those files.

**Interfaces:**
- Consumes: `compile_expr` (Task 3), `fold` with derived and modes (Tasks 5-8), existing `tunings()`, `mission_for(t)`, `runs`, `play`.
- Produces: `exprs()` strategy generating grammar-valid expression strings over a name set (with a Python reference evaluator written independently in the test: it uses `operator` and `math`, not `ast`); `mode_schedule()` strategy giving a mode per frame (`st.sampled_from(["SAFE","STANDBY","TEST","FAULT"])` runs with switch points); `Tuning` gains a `mode_checks` flag so SAFE sets are drawn with the same floor rule.

- [ ] **Step 1: Write the failing tests.** `test_derived_props.py::test_p6_expr_matches_reference` (`@given(exprs(), inputs)`: `compile_expr(e).eval(v)` equals the reference bit-for-bit, or both are NULL for div0/nonfinite), `::test_p6_rejects_everything_outside_grammar` (generated strings built from forbidden nodes plus random junk text, `ExprError` always, nothing else raised); `test_engine_props.py::test_p3_same_input_same_keys_with_derived_and_modes` (same frames, same ACKs, same mission-db timeline, different batch splits: same transition keys and same derived values; a late frame gets `rate_of` NULL and leaves the window unchanged), `::test_p4_snapshot_any_split_with_set_change_and_mode_switch` (snapshot/restore at random batch boundaries including a mission-db change and a mode switch equals the uninterrupted run); `test_property_strategies.py::test_tuning_draws_respect_floor` extended.
- [ ] **Step 2: Run.** `uv run pytest tests/property tests/unit/test_property_strategies.py -q` Expected: FAIL (new tests) until the strategies exist.
- [ ] **Step 3: Implement** the strategies; all tests carry `@profiles.PROFILE`. P6's reference evaluator is deliberately a second implementation (restricted to `+ - * /`, unary minus, the four functions).
- [ ] **Step 4: Run** `uv run pytest tests/property tests/unit/test_property_strategies.py` at `HYPOTHESIS_PROFILE=nyx-ci` (default), then once at `HYPOTHESIS_PROFILE=nightly HYPOTHESIS_SEED=1` for a wider search. Any counter-example found is pinned with `@example(...)` in the commit that fixes it and logged in `docs/bug-log.md` (D-ENG-19). Expected: PASS.
- [ ] **Step 5: Commit.** `git add tests docs/bug-log.md && git commit -m "test: P6 and the derived/mode parts of P3 and P4 (EXT-01, EXT-03)"` (add `docs/bug-log.md` only if a counter-example was pinned).

---

### Task 12: Extend-without-code meta test and the replay recompute

**Files:**
- Modify: `tests/meta/test_add_parameter.py` (or create `tests/meta/test_extend_without_code.py` if that file is not the one named in design §11.3: design names the latter; rename with `git mv` if the owner agrees, else add the new file and keep the old), `tests/integration/test_replay_equality.py`.
- Test: the same.

**Interfaces:**
- Consumes: everything above. Produces nothing new.

- [ ] **Step 1: Write the failing tests.** `test_extend_without_code.py::test_raw_derived_and_mode_limit_by_mission_edit_only` (throwaway database like D-ENG-20: copy `mission/` to `tmp_path`, add a raw parameter, a derived `power2_w`, a SAFE-mode limit; `make migrate` equivalent `migrate.add_columns`; assert `tree_digest(src)` unchanged, `write_all` leaves no generated file to commit, each new parameter is stored, charted (appears in `param_names`/frame schema) and alarmed). `test_replay_equality.py::test_recompute_reproduces_recorded_keys` widened: a run with mode switches and a derived alarm, re-derived from stored raw inputs by the same rules gives exactly the recorded keys (D-REP-05 b).
- [ ] **Step 2: Run.** Expected: FAIL until the throwaway-mission helper covers derived/mode keys.
- [ ] **Step 3: Implement** the test helpers only (no `src/` change; if `src/` needs a change the test just found a gap: fix it in the owning earlier task's module and say so in the commit).
- [ ] **Step 4: Run** `uv run pytest tests/meta tests/integration/test_replay_equality.py -q`. Expected: PASS. The `allowed_modes` clause of acceptance (a) is left to the commanding plan.
- [ ] **Step 5: Commit.** `git add tests && git commit -m "test: extending the mission db needs no code change; recompute reproduces keys with modes and derived (EXT-01)"`

---

### Task 13: D-EXT-20 re-run of ATP-02, ATP-07, ATP-08 on the extension build

**Files:**
- Modify: `docs/atp-report.md` (section "Extensions", next to the `v1.0` results), `docs/ncr/` only if a run fails.

**Interfaces:** Consumes the whole branch at its final commit. This task runs commands and records results; it changes no code. **The owner signs the result**; the implementer records, never signs.

- [ ] **Step 1: Start the stack** for this worktree with ports offset +16000 and compose project `nyx-ext01` (`make up`), `make migrate` (adds `power_w`, `cabin_leak_rate_kpa_s` columns).
- [ ] **Step 2: Run the three ATPs exactly as in the core procedure** (`docs/atp/` procedures for ATP-02, ATP-07, ATP-08, through `sh /Users/malek/try/nyx/.superpowers/syslock.sh`). The ATP-02 expected timeline (§3.4) must hold with the SAFE set and `power_w` present; ATP-07/08 are the ingest and measurement runs. During a measurement run nothing else runs on the machine.
- [ ] **Step 3: Run `make chaos SEED=<n>`** for the 30-minute soak (background, do not poll) as the regression net for the ingest and alarm changes; I1-I3 and I5-I8 must pass. If a derived value or a mode set causes an I3 failure, that is a defect in Tasks 5-8: stop and fix there.
- [ ] **Step 4: Record** under "Extensions" in `docs/atp-report.md`: the build commit, the three ATP results beside their `v1.0` rows, the chaos seed and verdict, and which GS-EXT-01 acceptance parts (a, b, c) are covered; `allowed_modes` rows marked "commanding plan". A failed ATP-E item gets an NCR in `docs/ncr/`; a failed core re-run blocks `v1.1` (never weaken a must).
- [ ] **Step 5: Commit.** `git add docs/atp-report.md docs/ncr && git commit -m "docs: ATP-02/07/08 re-run on the EXT-01 build (D-EXT-20)"` and hand the report to the owner for the signature.

---

## Self-review

- Spec coverage: D-EXT-04 (Tasks 2, 4, 5, 10), D-EXT-05 (3, 4, 6, 7), D-EXT-06 + M3 (4, 8), D-EXT-09 (4, 5), M1 + window flag (9), M4 (6, 7), S1/S2 (10, 3), P6/P3/P4 (11), acceptance a (12, partial), b (2, 5), c (6, 7), D-EXT-20 (13). D-EXT-07/08/M2/R24: Not in this plan.
- Type consistency: `Computed` (Task 5) is consumed by Tasks 6, 7; `compute` gains `windows`/`late` in Task 7 and its Task 5/6 callers are updated in that task; `Calibration = Table | Poly` only exists after Task 10.
