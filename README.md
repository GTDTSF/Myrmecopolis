# Myrmecopolis

**蚁国 — a live, emergent ant-colony ecosystem.** Pure Python, zero dependencies:
one command launches a real-time ant society you can watch in the browser —
foraging, tunnel-digging, raising brood, and a nest that grows organically from a
single chamber into a full underground city.

![A 150,000-tick nest](docs/nest_preview.png)

*Seed 5 after 150,000 ticks — 14 rooms (main hall / brood / food / sleep),
winding corridors in six curvature styles, room-to-room shortcuts, and an
exploratory tunnel web.*

## What it simulates

- **Full life cycle** — egg → larva → pupa → adult; caste assigned at emergence;
  age, fatigue and starvation all kill.
- **Foraging & economy** — food drops on the surface, foragers carry it back,
  granary chambers store it, and the colony lives a just-in-time economy.
- **Self-dug nest** — chambers, meandering corridors (six curvature archetypes:
  S / wave / arc / spiral / zigzag / wobble), room-to-room shortcuts and a
  capped exploratory web. The layout is never scripted; it is excavated.
- **Emergent control** — crowding gates reproduction, maintenance ants patch
  collapsing rooms, soil reclaims abandoned tunnels. No events are scripted.
- **Deterministic** — one seed fully determines the colony.

## Quick start

Needs Python 3.12+ (or just [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
uv run myrmecopolis          # serves http://127.0.0.1:8000
```

Open http://127.0.0.1:8000 — the colony starts from a queen plus five workers
and builds everything else on its own.

Headless self-check (no server):

```bash
uv run myrmecopolis --smoke
```

| flag | meaning | default |
|------|---------|---------|
| `--port` | server port | `8000` |
| `--seed` | world seed (deterministic) | `5` |
| `--smoke` | run the headless self-checks and exit | off |
| `--ticks` | smoke-test tick count | `3000` |

## Render a long-run nest to PNG

```bash
uv run tools/render_nest.py   # 150,000 ticks → nest_preview.png (current dir)
```

## How it works

A 240×150 cell grid, one `World.tick()` per frame (60 fps in the browser).
Navigation is candidate-cell scoring — no pathfinding, no pheromones.

- `src/myrmecopolis/constants.py` — every tunable in one file
- `src/myrmecopolis/ant.py`, `colony.py` — ant & colony state machines
- `src/myrmecopolis/world.py` — the orchestrator: terrain, food, digging gates,
  life cycle, serialization
- `src/myrmecopolis/smoke.py` — headless regression self-checks
- `src/myrmecopolis/web.py` — zero-dependency HTTP server: SSE frame stream,
  terrain versioning, and the canvas renderer in `static/`

No libraries, no build step, no data files — `http.server`, `random` and
`threading` only.

## License

MIT — see [LICENSE](LICENSE).
