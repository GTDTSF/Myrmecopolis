"""随时间跟踪位置聚集：每 5000 tick 报 单格最大密度/聚集蚁数/最密格位置/休息蚁数。
同时检测个体卡死：600 tick 窗口内只踏过 ≤6 格的非休息蚁，按角色/状态分组输出。
"""
import sys
from collections import Counter, defaultdict, deque
sys.path.insert(0, "src")
from myrmecopolis.world import World
from myrmecopolis.constants import SURFACE

w = World(seed=5)
col = w.colonies[0]
T = int(sys.argv[1]) if len(sys.argv) > 1 else 120000
STEP = 5000
SAMPLE = 10
WINDOW = 60  # 60 个采样点 = 600 tick

hist = defaultdict(lambda: deque(maxlen=WINDOW))
print(f"{'tick':>7} {'pop':>4} {'单格max':>7} {'≥3蚁格蚁数':>9} {'%聚集':>6} {'歇':>4}  最密格(x,y,深)")
for t in range(1, T + 1):
    w.tick()
    if t % SAMPLE == 0:
        alive = set()
        for a in col.ants:
            alive.add(a.uid)
            if a.resting:
                hist[a.uid].clear()  # 睡眠不算卡死；醒来后重新积累窗口
            else:
                hist[a.uid].append((a.x, a.y))
        for uid in list(hist):
            if uid not in alive:
                del hist[uid]
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
    by_key = Counter()
    examples = []
    for a in col.ants:
        h = hist.get(a.uid)
        if a.resting or h is None or len(h) < WINDOW:
            continue
        cells = set(h)
        if len(cells) <= 6:  # 600 tick 只碰过 ≤6 格：原地小循环/死等
            key = (a.role, a.state, bool(a.building), bool(a.maintaining), bool(a.tired))
            by_key[key] += 1
            if len(examples) < 8:
                examples.append(f"uid={a.uid}@({a.x},{a.y}) cells={len(cells)} key={key}")
    if by_key:
        print(f"  STUCK {sum(by_key.values())}  {dict(by_key)}")
        for e in examples:
            print(f"    {e}")
