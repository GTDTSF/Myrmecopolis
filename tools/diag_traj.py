"""随时间跟踪位置聚集：每 5000 tick 报 单格最大密度/聚集蚁数/最密格位置/休息蚁数。"""
import sys
from collections import Counter
sys.path.insert(0, "src")
from myrmecopolis.world import World
from myrmecopolis.constants import SURFACE

w = World(seed=5)
col = w.colonies[0]
T = int(sys.argv[1]) if len(sys.argv) > 1 else 120000
STEP = 5000

print(f"{'tick':>7} {'pop':>4} {'单格max':>7} {'≥3蚁格蚁数':>9} {'%聚集':>6} {'歇':>4}  最密格(x,y,深)")
for t in range(1, T + 1):
    w.tick()
    if t % STEP != 0:
        continue
    cell = Counter()
    for a in col.ants:
        cell[(a.x, a.y)] += 1
    n = len(col.ants)
    mx = max(cell.values()) if cell else 0
    clustered = sum(c for c in cell.values() if c >= 3)
    rest = sum(1 for a in col.ants if a.resting)
    (cx, cy), _ = cell.most_common(1)[0]
    print(f"{t:>7} {n:>4} {mx:>7} {clustered:>9} {100*clustered/n:>5.0f}% {rest:>4}  ({cx},{cy},深{cy-SURFACE})")
