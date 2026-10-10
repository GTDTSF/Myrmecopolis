from __future__ import annotations

import random

from .constants import SEEK, SOLDIER, WANDER

class Ant:
    _next_uid = 0
    __slots__ = (
        "x", "y", "d", "state", "hp", "energy", "role", "colony", "food",
        "building", "breaking", "maintaining", "resting", "fatigue", "age", "max_age", "eat_offset", "uid",
        "egg",
    )

    def __init__(self, x: int, y: int, role: int, colony: int, rng: random.Random) -> None:
        self.x = x
        self.y = y
        self.d = rng.randrange(8)
        self.state = WANDER if role == SOLDIER else SEEK
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
        self.egg = None  # 阶段11：正搬运的卵（dict 引用，随蚁移动）
        self.uid = Ant._next_uid
        Ant._next_uid += 1

