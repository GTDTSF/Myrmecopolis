"""临时诊断：聚集(单格/5×5区域/休息分布) + 方向震荡(A-B-A) + 承诺生效情况。用法：uv run tools/diag_cluster.py [ticks] [seed]"""
import sys
from collections import Counter

sys.path.insert(0, "src")
from myrmecopolis.world import World
from myrmecopolis.detect import Detector


def main() -> None:
    ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    w = World(seed=seed)
    det = Detector(every=200)
    c = w.colonies[0]

    peak_cell, peak_region, peak_wander = 0, 0, 0
    osc_total, osc_samples = 0, 0
    rest_in, rest_out, rest_total = 0, 0, 0
    commit_avg_hist = []

    for t in range(1, ticks + 1):
        w.tick()
        det.step(w)
        if t % 200 == 0:
            r = det.check(w)
            peak_cell = max(peak_cell, r["max_cell"])
            peak_region = max(peak_region, r.get("max_region5", 0))
            peak_wander = max(peak_wander, r["n_wander"])
            ants = c.ants
            rest_total = sum(1 for a in ants if a.resting)
            rest_in = sum(1 for a in ants if a.resting and c.in_sleep_room(a.x, a.y))
            rest_out = rest_total - rest_in
            commit_avg_hist.append(sum(getattr(a, "commit", 0) for a in ants) / max(1, len(ants)))
        # 震荡采样：每 500 tick 抓 30 tick 的方向序列，数 A-B-A 折返
        if t % 500 == 470:
            seq = {}
            for _ in range(30):
                if _ == 0:
                    seq = {a.uid: [a.d] for a in c.ants}
                else:
                    for a in c.ants:
                        if a.uid in seq:
                            seq[a.uid].append(a.d)
                w.tick()
            n_reversal = 0
            n_changew = 0
            n_ant = 0
            for uid, s in seq.items():
                if len(s) < 30:
                    continue
                n_ant += 1
                chg = sum(1 for i in range(1, 30) if s[i] != s[i - 1])
                rev = sum(1 for i in range(2, 30) if s[i] == s[i - 2] and s[i] != s[i - 1])
                n_changew += chg
                if rev >= 3:
                    n_reversal += 1
            osc_total += n_reversal / max(1, n_ant)
            osc_samples += 1

    print(f"=== seed={seed} ticks={ticks} ===")
    print(f"pop={c.population} store={int(c.store)} food_gained={c.food_gained:.0f}")
    print(f"峰值 单格={peak_cell}  5×5区域={peak_region}  徘徊={peak_wander}")
    print(f"休息蚁: 总{rest_total}  睡室内{rest_in}  睡室外{rest_out}")
    # 睡室分布
    for j, ch in enumerate(c.sleep_rooms):
        n = sum(1 for a in c.ants if (a.x - ch["cx"]) ** 2 / (ch["rx"] ** 2) + (a.y - ch["cy"]) ** 2 / (ch["ry"] ** 2) <= 1)
        nrest = sum(1 for a in c.ants if a.resting and (a.x - ch["cx"]) ** 2 / (ch["rx"] ** 2) + (a.y - ch["cy"]) ** 2 / (ch["ry"] ** 2) <= 1)
        print(f"  睡室{j} @({ch['cx']},{ch['cy']}) r=({ch['rx']},{ch['ry']})  占用{n} 休息{nrest}")
    if osc_samples:
        print(f"震荡: {osc_total / osc_samples:.2f} 只/样本（≥3 次 A-B-A 折返的蚁占比，样本{osc_samples}）")
    if commit_avg_hist:
        print(f"commit 均值: 末段 {sum(commit_avg_hist[-10:]) / 10:.2f}（>0 表示承诺机制在起作用）")
    print(f"=== 报警 {len(det.alarms)} 条 ===")
    for tick, kind, msg in det.alarms[:20]:
        print(f"  t={tick//1000:>3}k [{kind}] {msg}")


main()
