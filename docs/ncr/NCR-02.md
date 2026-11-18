# NCR-02: ENG-06 timed "add a parameter" run not done

- **Requirement:** ENG-06 (S): a new team member adds a parameter in under 15 min using the guide. Method D.
- **What is missing:** nobody who did not write the guide has followed it with a stopwatch. The spec's pass condition is that timed run.
- **What is shown instead:** `tests/meta/test_add_parameter.py` follows the guide's steps (one `mission/parameters.yaml` entry, its checks in `mission/limits.yaml`, `make migrate`) on a copy of `mission/` and a throwaway database, and passed in the Gate 5 pytest run (1743 passed). The steps work and need no code edit; their duration for a newcomer is not measured.
- **Impact on the user:** none on operation. The risk is a guide that is correct but slow to follow.
- **Severity:** minor.
- **Decision (proposed, the owner confirms):** accept with a known limitation. ENG-06 is one of 7 S requirements; the other 6 pass, so the acceptance rule (at least 5 of 7) holds without it. Do the timed run when a second person is available and record the time in this file.
