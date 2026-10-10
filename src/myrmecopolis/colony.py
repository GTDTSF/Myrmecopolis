from __future__ import annotations

from collections import deque

from .ant import Ant
from .constants import NEST_TARGET, QUEEN_WAKE

class Colony:
    def __init__(self, cid: int, qx: int, qy: int) -> None:
        self.id = cid
        self.ants: list[Ant] = []
        self.eggs: list[dict] = []   # 阶段7.4：卵（不吃，最短）
        self.larvae: list[dict] = []  # 阶段7.4：幼虫（吃粮）
        self.pupae: list[dict] = []   # 阶段7.4：蛹（不吃，羽化出成虫）
        self.qx = qx
        self.qy = qy
        self.last_lay = 0
        self.queen_resting = False  # 阶段7.2：蚁后睡眠状态（睡时暂停产卵）
        self.queen_rest_t = QUEEN_WAKE  # 蚁后睡/醒周期计时（到 0 切换状态）
        self.sleep_rooms: list = []  # 阶段10：多个睡室（每 tick 按 kind=sleep 重算）
        self.sleep_target = None  # 阶段10：休息目标睡室（占用最少→强制均分到不同房）
        self.brood_room: dict | None = None  # 阶段11：育虫室（kind=brood，每 tick 重算；卵的最终去处）
        self.food_rooms: list = []  # 阶段10：多个储粮室（kind=food，ch["store"] 记各房存粮）
        self.food_target = None  # 阶段10：搬运目标粮室（最空→食物强制均分）
        self.start_store = 0.0  # 开局储备粮（粮室接通前由它供粮，先消耗）
        self.food_gained = 0.0
        self.surface_open = False
        self.nest_size = 0
        self.n_builders = 0
        self.n_breakers = 0
        self.entrances: list[dict] = []  # G3: {"x": 列, "open": 是否打通}
        self.sealed: set[int] = set()  # G5: 被封堵的入口顶格（格子索引）
        self.seal_cd = 0  # G5: 封堵冷却，结束后自动重开
        self.pop_hist: deque[int] = deque(maxlen=300)  # G5: 人口滑动窗口
        self.chambers: list[dict] = []  # 巢室 {cx,cy,rx,ry,base_rx,base_ry,max_rx,max_ry,kind,store}
        self.chambers_dirt = 0  # 所有巢室椭圆内待挖土格总数（形状完成判据）
        self.nest_level = 0  # 巢室随种群长大的级别
        self.shaft_x0 = qx - 1  # 主通道列范围（3 宽窄竖井）：开井期只在此列内挖，保竖井窄
        self.shaft_x1 = qx + 1
        self.nest_cells: set[int] = set()  # 标记的蚁穴格索引（受维护保护、免土壤回填）
        self.collapsed: list[int] = []     # 塌陷（变回土）的蚁穴格，维护蚁修复目标
        self.n_maintainers = 0
        self.break_digs: list = []  # 阶段9：突破蚁可挖前沿格（挖穿区内的土且邻隧道），每 tick 重算

    def home_cell(self, x: int, y: int) -> bool:
        return max(abs(x - self.qx), abs(y - self.qy)) <= 5

    def in_sleep_room(self, x: int, y: int) -> bool:
        # 阶段10：任一睡室内？（蚂蚁只在此歇脚、不睡蚁后主厅；睡室随种群长大，半径同其它巢室）
        for ch in self.chambers:
            if ch.get("kind") == "sleep":
                rx, ry = ch["rx"], ch["ry"]
                if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                    return True
        return False

    @property
    def ready(self) -> bool:
        return self.nest_size >= NEST_TARGET and self.surface_open

    @property
    def population(self) -> int:
        # 阶段7.4：人口含全部发育阶段（卵/幼虫/蛹都有体积、占巢穴空间）
        return len(self.ants) + len(self.eggs) + len(self.larvae) + len(self.pupae)

    @property
    def store(self) -> float:
        # 阶段10：存粮 = 开局储备 + 各粮室存粮之和（食物真实存在房里→经济读数与实际一致，多间均分）
        return self.start_store + sum(ch["store"] for ch in self.chambers if ch.get("kind") == "food")

