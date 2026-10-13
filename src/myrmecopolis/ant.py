from __future__ import annotations

import random

from .constants import SEEK, SOLDIER, INDIV_VARIATION

class Ant:
    _next_uid = 0
    __slots__ = (
        "x", "y", "d", "state", "hp", "energy", "role", "colony", "food",
        "building", "breaking", "maintaining", "resting", "fatigue", "age", "max_age", "eat_offset", "uid",
        "egg", "pace",
    )

    def __init__(self, x: int, y: int, role: int, colony: int, rng: random.Random) -> None:
        self.x = x
        self.y = y
        self.d = rng.randrange(8)
        # 2026-10-10：兵蚁也参与觅食（SEEK）。单群落世界无战斗职责后，兵蚁（占出生 25%、
        # 寿命还是工蚁 3 倍→稳态占全群 ~50%）若只游荡不生产，是纯能量汇→存粮永远为 0。
        # 真实蚁群的"兵蚁/小型工蚁"同样参与觅食运输。
        self.state = SEEK
        self.hp = 60.0 if role == SOLDIER else 40.0
        self.energy = 100.0
        self.role = role
        self.colony = colony
        self.food = 0
        self.building = False
        self.breaking = False
        self.maintaining = False
        self.resting = False                # 回巢休息中（利用蚁巢恢复）
        self.fatigue = rng.uniform(0, 30)   # 初始疲劳随机（错开休息节奏）
        self.age = 0
        # 自然寿命：工:兵 ≈ 1:3（兵蚁更长寿），蚁后（Colony 属性，无 age）近似不朽
        self.max_age = rng.randrange(27000, 54000) if role == SOLDIER else rng.randrange(9000, 18000)
        self.eat_offset = rng.randrange(20)
        # 2026-10-10 收敛破除：每蚁代谢节律（等比缩放疲劳积累/恢复）→工作休息周期各不相同→全群去同步
        self.pace = 1.0 + rng.uniform(-INDIV_VARIATION, INDIV_VARIATION)
        self.egg = None  # 阶段11：正搬运的卵（dict 引用，随蚁移动）
        self.uid = Ant._next_uid
        Ant._next_uid += 1

