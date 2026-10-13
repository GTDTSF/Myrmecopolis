"""诊断位置聚集：蚂蚁堆在哪、堆的是什么状态（歇/搬运/挖/觅食）。"""
import sys
from collections import Counter
sys.path.insert(0, "src")
from myrmecopolis.world import World
from myrmecopolis.constants import SURFACE, SEEK, CARRY, WANDER

w = World(seed=5)
col = w.colonies[0]
T = int(sys.argv[1]) if len(sys.argv) > 1 else 80000
for t in range(T):
    w.tick()

cell_ants = Counter()
for a in col.ants:
    cell_ants[(a.x, a.y)] += 1

n = len(col.ants)
print(f"t={T//1000}k pop={n}  占据格数={len(cell_ants)}  单格最多={max(cell_ants.values())}")

antpos = {}
for a in col.ants:
    antpos.setdefault((a.x, a.y), []).append(a)

top = cell_ants.most_common(12)
print("\n最密 12 格 (数量 坐标 深度 状态分布 疲劳区间):")
for (x, y), c in top:
    ants = antpos[(x, y)]
    st = Counter()
    for a in ants:
        if a.resting: st['歇'] += 1
        elif a.egg is not None: st['运卵'] += 1
        elif a.state == CARRY and a.food: st['运'] += 1
        elif a.building: st['挖'] += 1
        elif a.maintaining: st['护'] += 1
        elif a.state == SEEK: st['觅'] += 1
        elif a.state == WANDER: st['游'] += 1
        else: st[f'S{a.state}'] += 1
    fats = [a.fatigue for a in ants]
    print(f"  {c:3d}  ({x:3d},{y:3d}) 深{y-SURFACE:3d}  {dict(st)}  疲{min(fats):.0f}-{max(fats):.0f}")

dist = Counter()
for c in cell_ants.values():
    dist['1' if c == 1 else '2' if c == 2 else '3-4' if c <= 4 else '5-8' if c <= 8 else '9+'] += 1
clustered = sum(c for c in cell_ants.values() if c >= 3)
print(f"\n格占用分布: {dict(dist)}")
print(f"处于 ≥3 蚁格中的蚂蚁: {clustered}/{n} ({100*clustered/n:.0f}%)")

# 休息蚁的空间分布（是不是歇在同样的几个点）
rest_cells = Counter()
for a in col.ants:
    if a.resting:
        rest_cells[(a.x, a.y)] += 1
if rest_cells:
    print(f"\n休息蚁 {sum(rest_cells.values())} 只，占 {len(rest_cells)} 格，单格最多 {max(rest_cells.values())}")
    for (x, y), c in rest_cells.most_common(6):
        print(f"   {c:3d}  ({x:3d},{y:3d}) 深{y-SURFACE:3d}")
