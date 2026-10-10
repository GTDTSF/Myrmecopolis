# AGENTS.md

Ant-colony ecosystem simulator. Pure Python 3.12, **zero dependencies** (stdlib only: `http.server`, `random`, `threading`). No build step, no lint/test/formatter tooling — the smoke check below is the only verification gate.

## Commands

```bash
uv sync
uv run myrmecopolis                 # live server → http://127.0.0.1:8000  (flags: --port, --seed, default seed 5)
uv run myrmecopolis --smoke         # headless regression: 3 worlds w/ assertions (last world = 35k ticks, takes minutes)
uv run tools/render_nest.py         # 150k ticks → nest_preview.png in CWD
```

`--smoke` must pass after any change to `src/myrmecopolis/`.

## Layout

- `src/myrmecopolis/constants.py` — **every tunable lives here**; tuning = edit this file only
- `src/myrmecopolis/world.py` — ~1200-line orchestrator: terrain, food, digging gates, life cycle, castes, SSE frame serialization
- `src/myrmecopolis/ant.py`, `colony.py` — ant data, colony state
- `src/myrmecopolis/smoke.py` — regression assertions; `web.py` — stdlib HTTP + SSE; `sim.py` — re-export shim (import via `from .sim import World`)
- `static/` — `index.html` (main canvas view), `gallery.html` (encyclopedia), `sprites.html`, `ants.js` (shared sprites)

## Invariants (breaking these fails silently)

- **Determinism**: one seed fully determines the colony. Behavior math may only use seed-derived per-ant values (`eat_offset`, `max_age`, initial fatigue). `Ant.uid` / `id()` are cross-world identity counters — never use them in behavior math (causes run-to-run divergence).
- **RNG call order matters**: reordering `random` calls changes all behavior. Verify with `--smoke` plus a fixed-seed headless run.
- **`col.store` is a read-only property** (`start_store` + Σ food-room `ch["store"]`). Never assign to it; route all food consumption through `_take_food` so room books stay consistent (a past bug: writing rooms but not `store` stalled builder recruitment and collapsed the colony).
- **smoke.py seeds and tick counts are deliberate** (fixed seeds 5/3, `max(ticks, 8000)`, W3 = 35000t): chosen to avoid false alarms from ecological variance and from transient "corridor still under construction" (unlinked) states. Don't shorten them to speed up.
- **Movement scoring** in `world.py` is candidate-cell scoring (no pathfinding). Pull coefficients were tuned against the 0.6 straight-ahead inertia (they must exceed it) — changing them shifts whole-colony behavior; re-run smoke after.

## Wire protocol

SSE JSON at 30 fps on `/stream`; terrain is versioned — the client refetches `/terrain` when `v` changes. Food/terrain travel as **base64 of raw bytearrays** (`_b64`), decoded client-side. If you touch `frame()` in `world.py`, `static/index.html` must be updated to match.

## Docs

`findings.md` (Chinese) holds the design decisions and root-cause history; `progress.md` the verification history. Both are local working docs (gitignored), but they are the authoritative source for *why* things are the way they are — check them before redesigning any subsystem. Note: the protocol sketch in `findings.md` is partially stale; trust the code.
