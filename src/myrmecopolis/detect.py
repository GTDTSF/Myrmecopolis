from __future__ import annotations

from collections import Counter, deque


class Detector:
    """蚂蚁扎堆/徘徊检测器。每个 tick 调 step(w)，每 N tick 调 check(w)。

    扎堆：某格蚂蚁数 > cluster_max（默认 10）。健康巢单格 max ~5-8（REST_CROWD_MAX
          压住休息堆叠 + 工作房正常密度），历史 bug 是 19。
    徘徊：疲劳满(≥fat_min=100，即休息阈值)、未歇、未干活（挖/护/破/搬运全排除）、
          且窗口(40t)内几乎原地不动的蚁 ≥ wander_min（默认 15）只——即"累满却歇不下、
          原地空转"的蚁（历史 bug 签名：疲劳大群徘徊 108 只 / 竖井堵口 23 只）。
          阈值 15 卡在"健康 0-3 + 偶发瞬峰 10"与"真 bug 23+"之间。
    纯读取世界状态，**无 RNG 调用**→不影响确定性。每个世界新建一个实例
    （uid 是跨世界单调计数器，复用实例会串味）。
    """

    def __init__(self, every: int = 1000, window: int = 40, cluster_max: int = 10,
                 wander_min: int = 15, wander_disp: int = 4, fat_min: float = 100.0):
        self.every = every
        self.window = window
        self.cluster_max = cluster_max
        self.wander_min = wander_min
        self.wander_disp = wander_disp
        self.fat_min = fat_min
        self._hist: dict = {}   # uid -> deque[(x, y)]
        self.alarms: list = []  # [(tick, 类别, 说明)]
        self.last = None

    def step(self, w) -> None:
        alive = set()
        for col in w.colonies:
            for a in col.ants:
                alive.add(a.uid)
                d = self._hist.get(a.uid)
                if d is None:
                    d = deque(maxlen=self.window)
                    self._hist[a.uid] = d
                d.append((a.x, a.y))
        for k in list(self._hist):
            if k not in alive:
                del self._hist[k]

    @staticmethod
    def _busy(a) -> bool:
        # 合法的"原地"活动：歇脚 / 挖 / 维护 / 破墙 / 搬运（粮或卵）——都不是徘徊
        return (a.resting or a.building or a.maintaining or a.breaking
                or a.food or a.egg is not None)

    def _wanderers(self, w) -> list:
        out = []
        half = self.window // 2
        for col in w.colonies:
            for a in col.ants:
                if self._busy(a) or a.fatigue < self.fat_min:
                    continue
                h = self._hist.get(a.uid)
                if not h or len(h) < half:
                    continue
                xs = [p[0] for p in h]
                ys = [p[1] for p in h]
                if (max(xs) - min(xs) < self.wander_disp
                        and max(ys) - min(ys) < self.wander_disp):
                    out.append(a)
        return out

    def check(self, w) -> dict:
        cell: Counter = Counter()
        for col in w.colonies:
            for a in col.ants:
                cell[(a.x, a.y)] += 1
        n = sum(cell.values())
        if cell:
            (cx, cy), max_cell = max(cell.items(), key=lambda kv: kv[1])
        else:
            max_cell, cx, cy = 0, 0, 0
        wander = self._wanderers(w)
        rep = {
            "tick": w.tick_count, "pop": n,
            "max_cell": max_cell, "cluster_at": (cx, cy),
            "n_wander": len(wander),
            "wander_at": [(a.x, a.y) for a in wander[:5]],
        }
        self.last = rep
        if n and max_cell > self.cluster_max:
            self.alarms.append((w.tick_count, "扎堆", f"单格{max_cell}只@({cx},{cy})"))
        if len(wander) >= self.wander_min:
            self.alarms.append((w.tick_count, "徘徊", f"{len(wander)}只高疲劳原地空转"))
        return rep
