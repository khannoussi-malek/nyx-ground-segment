# Gate 5 demo

[gate5-demo.mp4](gate5-demo.mp4), 3 min 33 s, no sound: the Playwright recordings of the ATP-02, ATP-04 and ATP-07 specs, run once on a fresh stack on 2026-11-18 (all 4 passed), joined in order. Real time, not sped up; the replay itself runs at 10x.

| From | Spec | What it shows |
|---|---|---|
| 0:00 | `e2e/atp02-leak.spec.ts` (ATP-02) | Login, then a cabin leak on NYX-TA1: YELLOW, then RED LOW with a tone, ACK, the hazardous isolate command with its confirmation, VERIFIED, the alarm clears |
| 0:35 | `e2e/atp04-los.spec.ts` (ATP-04) | A 10 s dropout: every value STALE after 2 s, LOSS OF SIGNAL with one tone, AOS reporting 100 frames lost, recovery |
| 0:47 | `e2e/atp04-los.spec.ts` (D-UI-25) | Rejected frames before a silence: FRAMES REJECTED instead of LOSS OF SIGNAL, cleared by the AOS |
| 0:58 | `e2e/replay.spec.ts` (ATP-07) | Replay of archived NYX-TA9 hours at 10x: purple REPLAY banner, the recorded leak alarm and dropout on replay time, a live RED counted in the banner, and a 409 on a corrupt hour |

Reproduce on a stack with unused NYX-TA9 history: `make up`, then `cd e2e && DEMO_VIDEO=1 npx playwright test atp02-leak atp04-los replay`; the videos land in `e2e/test-results/*/video.webm`.
