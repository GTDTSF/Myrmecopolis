"""蚂蚁扎堆/徘徊检测器（CLI）。无头跑 N tick，每 1000 tick 检测一次，超阈值报警。

用法：uv run tools/detector.py [ticks] [seed]     默认 100000 tick、seed 5
退出码：0=无报警，1=检测到扎堆/徘徊（可接 CI）。
"""
import sys

sys.path.insert(0, "src")
from myrmecopolis.world import World          # noqa: E402
from myrmecopolis.detect import Detector      # noqa: E402


def main() -> None:
    ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    w = World(seed=seed)
    det = Detector(every=1000)
    REPORT = 5000
    print(f"ticks={ticks} seed={seed}  阈值: 扎堆>单格{det.cluster_max}  徘徊>={det.wander_min}只")
    print(f"{'t':>5} {'pop':>4} {'单格max':>7}  {'最密格':>10}  {'徘徊':>4}")
    for t in range(1, ticks + 1):
        w.tick()
        det.step(w)
        if t % det.every == 0:
            det.check(w)
        if t % REPORT == 0:
            r = det.last
            print(f"{t//1000:>4}k {r['pop']:>4} {r['max_cell']:>7}  {str(r['cluster_at']):>10}  {r['n_wander']:>4}")
    print(f"\n=== 报警 {len(det.alarms)} 条 ===")
    for tick, kind, msg in det.alarms:
        print(f"  t={tick//1000:>3}k [{kind}] {msg}")
    sys.exit(1 if det.alarms else 0)


main()
