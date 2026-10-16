from __future__ import annotations

import random

from .constants import SEEK, SOLDIER, INDIV_VARIATION, HUNGER_MAX

class Ant:
    _next_uid = 0
    __slots__ = (
        "x", "y", "d", "state", "hp", "energy", "role", "colony", "food",
        "building", "breaking", "maintaining", "resting", "fatigue", "age", "max_age", "eat_offset", "uid",
        "egg", "pace", "size", "carry", "sleep_room", "path", "path_i", "path_target", "path_fail",
        "eating", "eat_t", "hunger",
        "party",
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
        # 2026-10-10 基因随机（同 pace，按种子派生、确定性）：体型缩放能耗（大蚁更勤觅食）+
        # 前端渲染尺寸；搬运量缩放每趟取食（大蚁一趟搬更多）。均值 1.0 不变→不扰经济。
        # 注：不给"移动步频"加随机——变速蚁会在窄瓶颈处互相堵成拥塞（实测 seed5@39k 单格 22 只），
        # 与"破除聚集"目标相悖，故移动速度保持同质、只让代谢/体型/搬运各异。
        self.size = 1.0 + rng.uniform(-INDIV_VARIATION, INDIV_VARIATION)
        self.carry = 1.0 + rng.uniform(-INDIV_VARIATION, INDIV_VARIATION)
        self.egg = None  # 阶段11：正搬运的卵（dict 引用，随蚁移动）
        # 阶段12：已认领睡室索引（-1=无）。疲劳蚁各认领"占用+在途最少"的房→摊开，不再全涌一间。
        self.sleep_room = -1
        # 阶段13：路线规划——在挖好的通路里 BFS 出的整条路线（"提前画好一条线"），
        # 逐格沿走→不撞墙、不折返、不震荡。path_target=算路线时的目标（变了/路断了重算）。
        self.path = None
        self.path_i = 0
        self.path_target = None
        self.path_fail = 0  # 目标不可达时的重试冷却（防每 tick 重算 BFS 风暴）
        # 阶段14：可见进食——按饥饿节律回粮室停留进食（eating=正在吃, eat_t=剩余停留 tick）
        # hunger 用已知的 eat_offset 派生初值（错开进食节奏），不新增 RNG。
        self.eating = False
        self.eat_t = 0
        self.hunger = self.eat_offset * (HUNGER_MAX / 20.0)
        self.party = False  # 阶段16：正在参加饭店派对（去饭店跳舞；散场由 Colony 块统一清）
        self.uid = Ant._next_uid
        Ant._next_uid += 1

