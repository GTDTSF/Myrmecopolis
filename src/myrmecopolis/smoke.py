from __future__ import annotations

from .constants import DIRT, SURFACE, TUNNEL, W
from .world import World

def smoke(ticks: int = 6000) -> None:
    """无头自检：挖洞/觅食/育幼/巢扩张(挖穿)/G5 封堵。用固定足够时长 + 稳定种子，
    不因生态波动（个别种子偏慢）误报。单群落。"""
    ticks = max(ticks, 8000)
    w = World(seed=5)
    c = w.colonies[0]
    t0 = w.terrain.count(TUNNEL)
    rest_peak = 0  # 休息行为自检：记录同时休息蚁峰值（回巢歇脚）
    all3 = 0  # 阶段7.4：生命周期自检——是否曾同时出现 卵+幼虫+蛹（三段流水线都在运转）
    for i in range(1, ticks + 1):
        w.tick()
        rest_peak = max(rest_peak, sum(1 for a in c.ants if a.resting))
        if c.eggs and c.larvae and c.pupae:
            all3 = 1
        if i % 4000 == 0:
            linked = sum(1 for ch in c.chambers if ch.get("linked"))
            print(
                f"t={i} pop={c.population} store={int(c.store)} "
                f"nest={c.nest_size} rooms={len(c.chambers)} linked={linked} "
                f"open={c.surface_open} brood=卵{len(c.eggs)}/幼{len(c.larvae)}/蛹{len(c.pupae)}"
            )
    t1 = w.terrain.count(TUNNEL)
    assert t1 > t0 + 150, f"digging too little: {t0} -> {t1}"
    assert c.food_gained > 100, "foraging not working"
    assert c.population > 25, "queen did not grow colony"
    # 挖穿（break-through）：健康群落应把巢扩出核心 3 间——第 4 间被突破蚁打通并连通
    assert (
        len(c.chambers) >= 4 and sum(1 for ch in c.chambers if ch.get("linked")) >= 4
    ), "break-through did not expand nest beyond the core 3 rooms"
    assert rest_peak >= 3, f"rest behavior not triggering (peak resting={rest_peak})"
    assert all3, "lifecycle: 3 stages (卵/幼虫/蛹) never all active at once"
    print(
        f"SMOKE OK  tunnels {t0} -> {t1}  pop {c.population}  "
        f"rooms {len(c.chambers)}  gained {int(c.food_gained)}  rest_peak {rest_peak}  all3 {all3}"
    )
    # G5 确定性检查：人口骤降 → 入口封堵（用另一稳定种子，先确保群落够大）
    w2 = World(seed=3)
    for _ in range(ticks):
        w2.tick()
    c2 = w2.colonies[0]
    assert any(e["open"] for e in c2.entrances), "G5: no open entrance"
    assert c2.population >= 10, f"G5: colony too small to test crash (pop={c2.population})"
    del c2.ants[1::2]  # 战损：杀掉一半工蚁
    for _ in range(300):
        w2.tick()
    assert c2.sealed and all(
        w2.terrain[SURFACE * W + e["x"]] == DIRT for e in c2.entrances if e["open"]
    ), "G5: entrances not sealed after population crash"
    print(f"G5 OK  sealed={len(c2.sealed)} cd={c2.seal_cd}")
    # 阶段9 向外扩张：长跑验证——动态房生成（主厅外全为动态房）+ 全部挖穿连通 + 巢径外扩
    w3 = World(seed=5)
    food_peak = 0.0  # 后期(20000t+)粮室存粮峰值：紧经济下存粮贴近消耗，末刻单快照会误报
    rooms_with_food_peak = 0
    # 50000t：动态房随种群出现、走廊施工需 5-15k，末批房（13/14 间）在 35k 时仍在挖属
    # 瞬时施工态（2026-10-10 实测 seed5：room12 于 40-50k 间、room13 于 45-50k 间 linked），
    # 多给时间让"全部连通"断言不卡在临时状态
    for _ in range(50000):
        w3.tick()
        if w3.tick_count >= 20000:
            fsum = sum(ch["store"] for ch in w3.colonies[0].chambers if ch.get("kind") == "food")
            food_peak = max(food_peak, fsum)
            rooms_with_food_peak = max(
                rooms_with_food_peak,
                sum(1 for ch in w3.colonies[0].chambers if ch.get("kind") == "food" and ch["store"] >= 2),
            )
    c3 = w3.colonies[0]
    dyn = c3.chambers[len(c3.chamber_defs):]
    far_dyn = max(
        ((ch["cx"] - c3.qx) ** 2 + (ch["cy"] - c3.qy) ** 2) ** 0.5 for ch in dyn
    ) if dyn else 0
    unlinked = [i for i, ch in enumerate(c3.chambers) if not ch.get("linked")]
    assert dyn, f"phase9: no dynamic rooms (rooms={len(c3.chambers)})"
    assert not unlinked, f"phase9: unlinked room(s) {unlinked}"
    assert far_dyn >= 22, f"phase9: nest not expanding outward (far_dyn={far_dyn:.0f})"
    # 阶段10 多房间均分：储粮/睡室各多间 + 食物确实流入多间粮室
    n_food = sum(1 for ch in c3.chambers if ch.get("kind") == "food")
    n_sleep = sum(1 for ch in c3.chambers if ch.get("kind") == "sleep")
    rooms_with_food = rooms_with_food_peak
    assert n_food >= 2 and n_sleep >= 3, f"phase10: multi rooms missing (food={n_food} sleep={n_sleep})"
    assert food_peak > 50, f"phase10: food not stored in rooms (peak_in_rooms={food_peak:.0f})"
    assert rooms_with_food >= 2, f"phase10: food not distributed across rooms (n={rooms_with_food})"
    print(
        f"PHASE9 OK  rooms {len(c3.chambers)} (dyn {len(dyn)})  far_dyn {far_dyn:.0f}  pop {c3.population}  "
        f"food_rooms {n_food}(峰值{rooms_with_food}有粮,存粮峰{food_peak:.0f})  sleep_rooms {n_sleep}"
    )
