from __future__ import annotations

import argparse
import threading
import time


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="myrmecopolis", description="Myrmecopolis 蚁国 — emergent ant-colony ecosystem simulator")
    p.add_argument("--smoke", action="store_true", help="headless self-check, no server")
    p.add_argument("--ticks", type=int, default=3000, help="smoke test tick count")
    p.add_argument("--port", type=int, default=8000, help="server port")
    p.add_argument("--seed", type=int, default=5, help="world seed")
    a = p.parse_args(argv)

    from .sim import World, smoke

    if a.smoke:
        smoke(a.ticks)
        return

    from .web import serve

    world = World(seed=a.seed)

    def loop() -> None:
        t = 1 / 60
        while True:
            world.tick()
            time.sleep(t)

    threading.Thread(target=loop, daemon=True).start()
    serve(world, a.port)
