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
        self.sleep_occ: list = []    # 阶段30z-3：各睡室正在睡的蚁数（resting；每 tick 重算）
        self.sleep_inbound: list = []  # 阶段30z-3：各睡室在途的疲劳蚁数（已认领、未开睡；每 tick 重算）
        self.sleep_cap: list = []  # 阶段30z-3：各睡室可用格数——按容量比例分摊睡蚁（强制打散）
        self.sleep_target = None  # 阶段10：休息目标睡室（负载比例最低→强制均分到不同房）
        self.sleep_ready = False  # 目标睡室已挖出可用面积（≥20 格）——没挖出来前疲劳蚁不转向去（防开井期罢工）
        self.brood_room: dict | None = None  # 阶段11：育虫室（kind=brood，每 tick 重算；卵的最终去处）
        self.food_rooms: list = []  # 阶段10：多个储粮室（kind=food，ch["store"] 记各房存粮）
        # 阶段30x：全群唯一 food_target 已删除，载体按 _carrier_room 每蚁分仓。
        self.food_ready: list = []  # 阶段30t：已就绪粮室（linked + 挖出 ≥FOOD_ROOM_READY 格）
        self.food_fill = 0.0  # 阶段30t：存粮/就绪仓容（满载→多派建造蚁，见 ROLE_BUILD_FULL）
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
        self.reachable_collapsed: list[int] = []  # 阶段25：其中位于已连通房内的（可达），维护蚁只修这些
        self.maint_mci: dict = {}  # 阶段29：每 tick 的 1:1 分派 {id(ant): mci}（无活干的维护蚁不在其中→待命槽位）
        self.sleep_claimable: list[int] = []  # 阶段30：可认领睡室下标（已连通+已挖出可用面积）
        self.sleep_ui: list = []  # 阶段30u：前端 Zz 标记只画已就绪睡室 [cx,cy,rx,ry]
        self.n_maintainers = 0
        self.break_digs: list = []  # 阶段9：突破蚁可挖前沿格（挖穿区内的土且邻隧道），每 tick 重算
        self.break_tgt: dict | None = None  # 阶段30k：本 tick 突破目标（缓存：_in_break_area 每格调用）
        self.dig_targets: list = []  # 阶段30x：本 tick 全部待填房（builder 按 max_age 分流，防挤同一前沿）
        self.expand_cd = 0  # 阶段30z：地图放不下下一间房时的重试冷却（不限制房数）
        self.nest_mask = None  # 阶段30z：巢区（房椭圆∪走廊）O(1) 掩码，房数无上限时避免每格扫所有房
        self.nest_dirty = True
        self.brood_ready = False  # 阶段30k：育虫室就绪（中心格已挖+容量≥20），就绪才允许工蚁捡卵

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

