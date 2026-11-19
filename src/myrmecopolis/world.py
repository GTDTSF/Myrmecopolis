from __future__ import annotations

import base64
import math
import random
from array import array
from collections import deque

from .ant import Ant
from .colony import Colony
from .constants import W, H, SURFACE, ROCK, DIRT, TUNNEL, DIRS, SEEK, CARRY, WORKER, SOLDIER, MAX_POP, FOOD_CAP, FOOD_DROP_P, FOOD_DROP_MIN, FOOD_DROP_MAX, FOOD_SCAN_EVERY, FOOD_BAND, FALL_SPEED, WALK_P, DIG_P, NEST_TARGET, NEST_RADIUS, CHAMBER_BIAS, SHAFT_BIAS, NEST_GROW_EVERY, ROOM_WIP_TICKS, ROOM_EXPAND_RETRY, ROOM_PARALLEL_DIG, PATH_BIAS, RECLAIM_K, RECLAIM_P, MAINTAIN_EVERY, ROLE_SPLIT, ROOM_DECAY_P, CROWD_P, FOOD_PER_CELL, FATIGUE_RATE, FATIGUE_MAX, REST_RECOVER, REST_MIN_FAT, REST_REGEN, REST_CROWD_MAX, DAY, QUEEN_NAP, QUEEN_WAKE, EGG_TIME, LARVA_TIME, PUPA_TIME, BRIDGE_BIAS, ORPHAN_SWEEP_EVERY, HUNGER_RATE, HUNGER_MAX, EAT_TIME, EAT_ENERGY, EAT_FOOD, EAT_CROWD_MAX, PATH_RETRY, REACH_EVERY, FOOD_RESERVE, LAY_MIN, LAY_BONUS_MAX, LAY_STORE_PER, FOOD_ROOM_READY, ROLE_BUILD_FULL

def _b64(b: bytearray) -> str:
    return base64.b64encode(bytes(b)).decode()



class World:
    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        n = W * H
        self.terrain = bytearray([DIRT]) * n
        self.food = bytearray(n)
        self.food_p = bytearray(n)
        self.use = bytearray(n)  # 通行痕迹：蚂蚁踩过的格子，衰减归零后隧道可被回填
        self.nest_mark = bytearray(n)  # 蚁穴格标记：巢室/走廊/入口竖井，受维护保护免回填
        self.regrow: list[tuple[int, float]] = []
        self.targets: list[tuple[int, int]] = []
        self.falling: list[list] = []  # 阶段17：空中下落中的食物 [x, y(格,浮点), 数量]——落地才计入地表 food
        self.terrain_version = 0
        self.tick_count = 0
        self._cap_cache = {"tick": -1}
        # 阶段30x：可达掩码缓存（按 tick）。房间可能"linked 但其实连不过去"——走廊从椭圆外
        # 擦过就记了 room_capacity≥1（30u 同源问题）：粮室/睡室/育虫室就绪门前再用真实
        # BFS 可达性过滤一次。热路径共享一次洪泛，别按蚁各算。
        self._reach = {"tick": -1, "col": None, "mask": None}
        # 格子溯源（纯记账，不影响行为/RNG）：dig_t=该格最后一次被挖开的 tick（0=未挖开过），
        # dig_src=谁挖的：1=开局 2=builder 4=突破蚁 5=维护蚁 6=入口重开。
        # 房间类型无需存储：格落在哪个房间椭圆内就是什么房（_in_any_chamber）。
        self.dig_t = array("I", [0]) * n
        self.dig_src = bytearray(n)
        self.web_count = 0  # 地底未标记隧道（探索网）格数：WEB_CAP 上限控制探路蚁开挖
        self.colonies: list[Colony] = []
        self._init_terrain()
        self._init_food()
        self._init_colonies()

    def _dig(self, x: int, y: int, src: int = 0) -> None:
        i = y * W + x
        if self.terrain[i] != TUNNEL:
            self.terrain[i] = TUNNEL
            self.terrain_version += 1
            self.dig_t[i] = self.tick_count  # 溯源：最后一次被挖开的 tick
            self.dig_src[i] = src
            nested = False
            for col in self.colonies:
                # 标记蚁穴格：巢室/走廊/入口竖井 → 受维护保护（免土壤回填），维护蚁定期巡查修复
                if self._is_nest_cell(col, x, y):
                    nested = True
                    if not self.nest_mark[i]:
                        self.nest_mark[i] = 1
                        col.nest_cells.add(i)
                # 巢穴容量 = 落在"房间∪走廊"内且非入口列的隧道格。随整巢扩张而增大，
                # 供拥挤反馈(pop/nest_size)：巢越大越不挤→产卵越快→种群越大→继续扩张
                if col.chambers and self._in_nest_area(col, x, y) and not self._in_shaft(col, x, y):
                    col.nest_size += 1
                if col.chambers:
                    for ch in col.chambers:
                        rx, ry = ch["rx"], ch["ry"]
                        if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                            ch["dirt"] = max(0, ch["dirt"] - 1)
                            col.chambers_dirt = max(0, col.chambers_dirt - 1)
                            break
            # 探索网记账：非蚁穴（非房/走廊/竖井）的隧道计入网量，供 WEB_CAP 限探路蚁
            if not nested:
                self.web_count += 1

    def _init_terrain(self) -> None:
        t = self.terrain
        for x in range(W):
            for y in range(SURFACE):
                t[y * W + x] = TUNNEL
        for y in range(H):
            for x in (0, 1, W - 2, W - 1):
                t[y * W + x] = ROCK
        for x in range(W):
            t[(H - 1) * W + x] = ROCK
            t[(H - 2) * W + x] = ROCK
        for _ in range(14):
            bx = self.rng.randrange(6, W - 6)
            by = self.rng.randrange(SURFACE + 4, H - 6)
            r = self.rng.randrange(2, 5)
            for y in range(by - r, by + r + 1):
                for x in range(bx - r, bx + r + 1):
                    if (x - bx) ** 2 + (y - by) ** 2 <= r * r and 1 < x < W - 2 and 3 < y < H - 2:
                        t[y * W + x] = ROCK

    def _init_food(self) -> None:
        # 开局食物：偏落到群落(居中)觅食带内（阶段 8 单群落，缩短觅食往返）
        cx = W // 2
        for _ in range(30):
            x0 = max(6, min(W - 12, cx + self.rng.randrange(-FOOD_BAND, FOOD_BAND + 1)))
            w = self.rng.randrange(2, 5)
            for x in range(x0, min(W - 2, x0 + w)):
                self.food[W + x] = min(FOOD_CAP, self.food[W + x] + self.rng.randrange(25, 70))
        self.targets = [(x, 1) for x in range(4, W - 4) if self.food[W + x] > 12]

    def _init_colonies(self) -> None:
        for cid, fx in enumerate((0.5,)):  # 阶段 8：单群落，居中（移除双群对抗）
            nx = int(W * fx)
            ny = H - 45
            for y in range(ny - 2, ny + 3):
                for x in range(nx - 2, nx + 3):
                    self._dig(x, y, 1)  # 开局初始巢穴
            col = Colony(cid, nx, ny)
            # 阶段30z：主竖井也走蜿蜒中心线；清掉曲线带内巨石，保证 3 宽通道能挖穿地表。
            col.main_shaft = self._make_shaft(nx, SURFACE, ny)
            self._clear_shaft_rocks(col.main_shaft)
            col.ants.extend(Ant(nx, ny, WORKER, cid, self.rng) for _ in range(5))
            # 房间布局：主厅(中枢) + 随种群逐个新增的扩展房间 → 蚁巢持续扩张；
            # 每间房彼此分开(不重叠)、由主厅引出的 3 宽窄走廊相连 → "通道/房间"分化，不挖成一大圆
            # 走廊见 _in_nest_area；初始开 3 间，种群长大逐个新增(见 tick 里的扩张逻辑)
            # 完全无模板：只有主厅固定（蚁后在），其余房间全部由 _dynamic_chamber_def
            # 随机方向向外生成（距离每间 +4、功能 kind 按需求分配，见 _next_room_kind）
            col.chamber_defs = [
                # (cx_off, cy_off, base_rx, base_ry, max_rx, max_ry, kind)
                (0, 0, 5, 5, 9, 9, "main"),  # 0 主厅：蚁后中枢，最大（唯一固定房）
            ]
            col.chambers = [
                self._new_chamber(col, d, self.rng, core=True, kind=d[6]) for d in col.chamber_defs
            ]
            col.start_store = 150.0  # 建群初始储备粮（先消耗，粮室接通后存入各粮室）：撑到觅食起量，防开局饿死
            self._recount_nest_dirt(col)
            self.colonies.append(col)
            # 标记初始蚁穴格（开局 5×5 主厅团块）：_dig 在 col 创建前运行，此处补标。
            # 补标时冲销 web_count：_dig 当时看不到群落（nested=False）把它们算进了网量
            for y in range(ny - 2, ny + 3):
                for x in range(nx - 2, nx + 3):
                    i = y * W + x
                    if self.terrain[i] == TUNNEL:
                        self.nest_mark[i] = 1
                        col.nest_cells.add(i)
                        self.web_count -= 1

    def _in_any_chamber(self, col: Colony, x: int, y: int) -> bool:
        for ch in col.chambers:
            rx, ry = ch["rx"], ch["ry"]
            if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                return True
        return False

    def _corridor_hit(self, ax: int, ay: int, ch: dict, x: int, y: int) -> bool:
        """走廊几何判定（无缓存）——几何只随房间定义变化，供 _corridor_mask 一次性烘焙。"""
        bx, by = ch["cx"], ch["cy"]
        dx, dy = bx - ax, by - ay
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1:
            return (x - ax) ** 2 + (y - ay) ** 2 <= 1.5
        ux, uy = dx / L, dy / L
        vx, vy = -uy, ux
        t = ((x - ax) * ux + (y - ay) * uy) / L
        t = max(0.0, min(1.0, t))
        sh = ch.get("c_shape", "wob")
        a1, f1, p1 = ch["c_amp"], ch["c_freq"], ch["c_phase"]
        a2, f2, p2 = ch["c_amp2"], ch["c_freq2"], ch["c_phase2"]
        if sh == "zig":
            # 锯齿：三角波（尖折急拐，真实蚁穴常见）+ 少量细波
            u = (2.0 * f1 * t + p1 / math.pi) % 2.0
            off = a1 * (1.0 - 2.0 * abs(u - 1.0)) + a2 * math.sin(math.pi * f2 * t + p2)
        elif sh == "arc":
            # 弓形C：单边整拱（phase 当硬币定左右侧）
            sgn = 1.0 if p1 < math.pi else -1.0
            off = sgn * a1 * math.sin(math.pi * t)
        elif sh == "spiral":
            # 螺旋：线圈振幅向房间端渐增（绕房趋势）+ 少量细波
            off = (a1 * (0.25 + 0.75 * t) * math.sin(math.pi * f1 * t + p1)
                   + a2 * math.sin(math.pi * f2 * t + p2))
        else:  # s=整正弦 / w=多波 / wob=微抖
            off = (a1 * math.sin(math.pi * f1 * t + p1)
                   + a2 * math.sin(math.pi * f2 * t + p2))
        cx = ax + ux * (t * L) + vx * off
        cy = ay + uy * (t * L) + vy * off
        r = ch.get("c_r", 2.0)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r  # 走廊宽按房随机 3.4-4.6 格（旧固定 2.0 太均匀）

    def _corridor_mask(self, ax: int, ay: int, ch: dict):
        """走廊格掩码按 (ax,ay) 缓存在房间上：原 _in_corridor 每次要算 sin，
        而 _break_dig_cells/_in_connected_nest 会按格调用几十万次（cProfile 占 15%）。
        走廊几何由房间创建参数决定、地图上不再移动，烘焙一次后 O(1) 查表。"""
        cache = ch.get("_cm")
        if cache is None:
            cache = {}
            ch["_cm"] = cache
        hit = cache.get((ax, ay))
        if hit is not None:
            return hit
        bx, by = ch["cx"], ch["cy"]
        pad = int(abs(ch["c_amp"]) + abs(ch["c_amp2"]) + ch.get("c_r", 2.0)) + 3
        x0, x1 = max(0, min(ax, bx) - pad), min(W - 1, max(ax, bx) + pad)
        y0, y1 = max(0, min(ay, by) - pad), min(H - 1, max(ay, by) + pad)
        w = x1 - x0 + 1
        mask = bytearray(w * (y1 - y0 + 1))
        for y in range(y0, y1 + 1):
            base = (y - y0) * w
            for x in range(x0, x1 + 1):
                if self._corridor_hit(ax, ay, ch, x, y):
                    mask[base + x - x0] = 1
        hit = (x0, y0, w, y1 - y0 + 1, mask)
        cache[(ax, ay)] = hit
        return hit

    def _in_corridor(self, ax: int, ay: int, ch: dict, x: int, y: int) -> bool:
        """主厅(ax,ay)→房间 ch 的蜿蜒走廊：沿连线按"曲率形态"偏移中心线（_corridor_shape 按种子抽）→ 通道曲折多样。"""
        if (ch["cx"] - ax) ** 2 + (ch["cy"] - ay) ** 2 < 1:
            return (x - ax) ** 2 + (y - ay) ** 2 <= 1.5
        x0, y0, w, h, mask = self._corridor_mask(ax, ay, ch)
        if x0 <= x < x0 + w and y0 <= y < y0 + h:
            return bool(mask[(y - y0) * w + x - x0])
        return False

    def _in_nest_area(self, col: Colony, x: int, y: int) -> bool:
        """巢内可挖区 = 各圆形巢室 ∪ 主厅到每间房的走廊。
        阶段30z：房数无上限 → 用整图掩码 O(1) 查表（几何不变时复用）。"""
        if not col.chambers:
            return False
        if col.nest_dirty or col.nest_mask is None:
            self._sync_nest_mask(col)
        return bool(col.nest_mask[y * W + x])

    def _free_chamber_cell(self, col: Colony, ch: dict):
        """返回巢室椭圆内一个空闲隧道格（无蚁无幼虫），找不到返回 None。
        用于"卵/幼虫有体积"：蚁后只朝空闲格产卵，育虫室满则停产→负反馈逼着挖大。"""
        t = self.terrain
        occ = {(a.x, a.y) for c in self.colonies for a in c.ants}
        # 阶段7.4：卵/幼虫/蛹 都占格（有体积），任一阶段占用的格都不再产卵
        for c in self.colonies:
            for st in (c.eggs, c.larvae, c.pupae):
                occ |= {(b["x"], b["y"]) for b in st}
        rx, ry = ch["rx"], ch["ry"]
        cells = []
        for y in range(ch["cy"] - ry, ch["cy"] + ry + 1):
            for x in range(ch["cx"] - rx, ch["cx"] + rx + 1):
                if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) > 1:
                    continue
                if 0 <= x < W and 0 <= y < H and t[y * W + x] == TUNNEL and (x, y) not in occ:
                    cells.append((x, y))
        return self.rng.choice(cells) if cells else None

    def room_capacity(self, col: Colony, ch: dict) -> int:
        """巢室内已挖通隧道格数（该房间的"体积"）。育虫室满/储粮室满的判据。
        阶段30w：按 tick 缓存——热路径里每个 builder 每 tick 都会排序/查所有房，
        不缓存时开销 ≈ 蚁数 × 房间数 × 房格数（1000 蚁时会让实时服务卡死）。
        挖掘改动在同一 tick 内不影响判定（体积是启发式，下一 tick 自然重算）。"""
        _c = self._cap_cache
        _k = id(ch)
        if _c.get("tick") != self.tick_count:
            _c.clear()
            _c["tick"] = self.tick_count
            _c[_k] = None
        elif _k in _c:
            _v = _c[_k]
            if _v is not None:
                return _v
        t = self.terrain
        rx, ry = ch["rx"], ch["ry"]
        cap = 0
        for y in range(ch["cy"] - ry, ch["cy"] + ry + 1):
            for x in range(ch["cx"] - rx, ch["cx"] + rx + 1):
                if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                    if 0 <= x < W and 0 <= y < H and t[y * W + x] == TUNNEL:
                        cap += 1
        _c[_k] = cap
        return cap

    def _reach_mask(self, col: Colony) -> bytearray:
        """阶段30x：从蚁后格沿可走格（_path_cell_ok）洪泛，得本 tick 全部可达格。
        用于房间"真就绪"门：仅凭 linked/room_capacity 会选中擦过椭圆却连不过去的幽灵房，
        载体/睡蚁被指过去 → BFS 全败 → 永远抱着粮/睡不下（seed5 15k 起 80 只 CARRY 无路可走、
        food_gained 冻结、存粮被吃到 0 的根因）。按 tick 缓存，全群共享一次洪泛。"""
        c = self._reach
        if c["col"] is col and self.tick_count - c["tick"] < REACH_EVERY:
            return c["mask"]
        mask = bytearray(W * H)
        if self._path_cell_ok(col.qx, col.qy, col):
            start = col.qy * W + col.qx
            mask[start] = 1
            stack = [start]
            # 阶段30y：洪泛内联 _path_cell_ok（与 BFS 同款热点），结果与逐格调用完全一致。
            t = self.terrain
            nm = self.nest_mark
            sealed = col.sealed
            while stack:
                i = stack.pop()
                x, y = i % W, i // W
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H:
                        j = ny * W + nx
                        if mask[j]:
                            continue
                        jt = t[j]
                        if jt == ROCK or jt == DIRT or j in sealed or (ny >= SURFACE and not nm[j]):
                            continue
                        mask[j] = 1
                        stack.append(j)
        c["tick"], c["col"], c["mask"] = self.tick_count, col, mask
        return mask

    def _sync_nest_mask(self, col: Colony) -> None:
        """阶段30z：把“房椭圆 ∪ 走廊”烘焙成整图掩码。房数不设上限后，
        逐格 _in_any_chamber/_in_corridor 的循环会随房数线性变慢；掩码只在建房/半径增长时重建。"""
        mask = bytearray(W * H)
        for ch in col.chambers:
            cx, cy, rx, ry = ch["cx"], ch["cy"], ch["rx"], ch["ry"]
            for y in range(max(SURFACE, cy - ry), min(H - 1, cy + ry) + 1):
                dy2 = (y - cy) * (y - cy)
                for x in range(max(0, cx - rx), min(W - 1, cx + rx) + 1):
                    if (x - cx) ** 2 * ry * ry + dy2 * rx * rx <= rx * rx * ry * ry:
                        mask[y * W + x] = 1
        if col.chambers:
            mx, my = col.chambers[0]["cx"], col.chambers[0]["cy"]
            for ch in col.chambers[1:]:
                self._stamp_corridor(mask, mx, my, ch)
                for lk in ch.get("links", ()):
                    p = col.chambers[lk]
                    self._stamp_corridor(mask, p["cx"], p["cy"], ch)
        col.nest_mask = mask
        col.nest_dirty = False

    def _stamp_corridor(self, mask: bytearray, ax: int, ay: int, ch: dict) -> None:
        x0, y0, w, h, cm = self._corridor_mask(ax, ay, ch)
        for y in range(y0, y0 + h):
            base = y * W + x0
            mbase = (y - y0) * w
            for x in range(w):
                if cm[mbase + x]:
                    mask[base + x] = 1

    def _recount_nest_dirt(self, col: Colony) -> None:
        col.nest_dirty = True
        total = 0
        t = self.terrain
        for ch in col.chambers:
            cx, cy, rx, ry = ch["cx"], ch["cy"], ch["rx"], ch["ry"]
            cd = 0
            for y in range(cy - ry - 1, cy + ry + 2):
                for x in range(cx - rx - 1, cx + rx + 2):
                    if 0 <= x < W and 0 <= y < H and t[y * W + x] == DIRT:
                        if (x - cx) ** 2 / (rx * rx) + (y - cy) ** 2 / (ry * ry) <= 1:
                            cd += 1
            ch["dirt"] = cd
            total += cd
        col.chambers_dirt = total

    def _dirt_chambers(self, col: Colony):
        """阶段30x：全部**已连通**待填房中心，按开房顺序稳定排列。
        高人口时 builder 按 max_age 均匀认一间（不再全追"土最多"或只分 3 间）：
        seed5 32k 仅分 3 间时，约 90 只 builder 同挤睡室 (126,61)，局部 5×5 达 37 只。
        未连通房只归 3 只突破蚁（_in_break_area），普通 builder 挖不到——不能当目标。"""
        return [(ch["cx"], ch["cy"]) for ch in col.chambers if ch.get("linked") and ch["dirt"] > 0]

    def _in_ellipse(self, x: int, y: int, ch: dict) -> bool:
        rx, ry = ch["rx"], ch["ry"]
        return (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1

    def _in_food_pile(self, col: Colony, x: int, y: int, ch: dict) -> bool:
        """是否站在真正有粮的堆内（与前端 drawPantry 同一装载率半径）。
        旧版只判房间椭圆：蚂蚁在走廊擦进椭圆边缘/门口就能开吃，粮粒却还堆在房心。"""
        rx, ry = ch["rx"], ch["ry"]
        rn = math.sqrt((x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry))
        k = min(1.0, ch["store"] / max(1.0, self.room_capacity(col, ch) * FOOD_PER_CELL))
        return rn <= 0.20 + 0.80 * math.sqrt(k)

    def _make_shaft(self, x: int, y_top: int, y_bottom: int) -> dict:
        """阶段30z：竖井中心线——两端回到入口 x，中段按正弦缓慢左右摆动。
        3 宽通道（中心±1）沿曲线推进，既保持连通，又不像旧版那样是一条直筒。"""
        amp = self.rng.uniform(6.0, 9.0)
        freq = self.rng.uniform(0.9, 1.5)
        phase = self.rng.uniform(0.0, math.tau)
        cells = set()
        centers = {}
        prev = None
        for y in range(y_top, y_bottom + 1):
            t = (y_bottom - y) / max(1, y_bottom - y_top)
            _off = amp * math.sin(math.tau * freq * t + phase) * math.sin(math.pi * t)
            cx = int(round(x + _off))
            if prev is not None:  # 相邻层中心最多偏 1 格：3 宽带必重叠，竖井不会断成两截
                cx = max(prev - 1, min(prev + 1, cx))
            centers[y] = cx
            prev = cx
            for xx in (cx - 1, cx, cx + 1):
                if 0 <= xx < W:
                    cells.add((xx, y))
        return {
            "x": x, "y0": y_top, "y1": y_bottom, "dig_y1": y_bottom, "cells": cells,
            "centers": centers, "amp": amp, "freq": freq, "phase": phase,
            "conn": (x, y_bottom),
        }

    def _shaft_center(self, shaft: dict, y: int) -> int:
        """给定 y 的竖井中心 x（用于施工引导；cells 已按同一曲线烘焙）。"""
        y = min(y, shaft.get("dig_y1", y))
        return shaft["centers"].get(y, shaft["x"])

    def _in_shaft(self, col: Colony, x: int, y: int) -> bool:
        sh = getattr(col, "main_shaft", None)
        if sh is not None and (x, y) in sh["cells"]:
            return True
        for e in col.entrances:
            s = e.get("shaft")
            if s is not None and (x, y) in s["cells"]:
                return True
        return False

    def _clear_shaft_rocks(self, shaft: dict) -> None:
        t = self.terrain
        for x, y in shaft["cells"]:
            i = y * W + x
            if t[i] == ROCK:
                t[i] = DIRT
                self.terrain_version += 1

    def _shaft_connected(self, shaft: dict) -> bool:
        """井口到井底连接点是否已有连续隧道（只在“井口已挖开”时调用，约几百格）。
        防止井口先通地表、中段仍隔土 → BFS 从地表绕到井口，却下不到巢里（幽灵入口）。"""
        conn = shaft.get("conn")
        if conn is None:
            return True
        cells = shaft["cells"]
        t = self.terrain
        seen = set()
        stack = []
        for x, y in cells:
            if y == SURFACE and t[y * W + x] == TUNNEL:
                stack.append((x, y))
                seen.add((x, y))
        while stack:
            x, y = stack.pop()
            if (x, y) == conn:
                return True
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p = (x + dx, y + dy)
                if p not in seen and p in cells and t[p[1] * W + p[0]] == TUNNEL:
                    seen.add(p)
                    stack.append(p)
        return False


    def _corridor_shape(self, rng: random.Random) -> dict:
        """走廊曲率形态：按种子抽一种原型 + 参数（2026-10-09 用户要求多样：S/螺旋等，
        不能只有"直线+几个随机数"）。六种形态：
        s=标准S（一个整正弦，左右各一拱） w=多波（3-4.5 半波连弯）
        arc=弓形C（单边整拱，大弧度） spiral=螺旋（线圈向房间端渐增→绕房趋势）
        zig=锯齿（尖折，真实蚁穴常见急拐） wob=直+微抖（偶尔一条直路）
        形态参数表一次性求值 → 每房 rng 调用数固定（1 choice + 36 uniform），种子稳定。"""
        shape = rng.choice(("s", "w", "arc", "spiral", "zig", "wob"))
        amp, freq, phase, amp2, freq2, phase2 = {
            "s": (rng.uniform(2.5, 4.0), rng.uniform(2.0, 2.4), rng.uniform(0.0, math.tau),
                  rng.uniform(0.3, 0.8), rng.uniform(3.5, 5.0), rng.uniform(0.0, math.tau)),
            "w": (rng.uniform(1.5, 2.5), rng.uniform(3.0, 4.5), rng.uniform(0.0, math.tau),
                  rng.uniform(0.4, 0.9), rng.uniform(5.0, 7.0), rng.uniform(0.0, math.tau)),
            "arc": (rng.uniform(2.5, 4.5), 1.0, rng.uniform(0.0, math.tau),
                    0.0, 0.0, 0.0),
            "spiral": (rng.uniform(2.5, 4.0), rng.uniform(3.5, 5.5), rng.uniform(0.0, math.tau),
                       rng.uniform(0.3, 0.8), rng.uniform(5.0, 7.0), rng.uniform(0.0, math.tau)),
            "zig": (rng.uniform(2.0, 3.0), rng.uniform(1.5, 3.0), rng.uniform(0.0, math.tau),
                    rng.uniform(0.3, 0.8), rng.uniform(3.0, 5.0), rng.uniform(0.0, math.tau)),
            "wob": (rng.uniform(0.5, 1.2), rng.uniform(1.5, 3.0), rng.uniform(0.0, math.tau),
                    rng.uniform(0.2, 0.5), rng.uniform(3.5, 5.5), rng.uniform(0.0, math.tau)),
        }[shape]
        return {
            "c_shape": shape, "c_amp": amp, "c_freq": freq, "c_phase": phase,
            "c_amp2": amp2, "c_freq2": freq2, "c_phase2": phase2,
            "c_r": rng.uniform(1.7, 2.3),  # 走廊半径：宽窄不一（3.4-4.6 格）
        }

    def _new_chamber(self, col: Colony, d: tuple, rng: random.Random, core: bool = False, kind: str = "room") -> dict:
        """新建巢室：半径/长宽比随机（大小性质不一、椭圆为主）；走廊波浪参数随机（通道蜿蜒）。
        core=核心间（普通 builder 直接挖）；否则 linked=False→需突破蚁"挖穿"打通。
        kind=功能（阶段10：main/brood/food/sleep/room）：food 房存粮、sleep 房歇脚，多间分担。"""
        rx = max(3, d[2] + rng.randrange(-1, 3))
        ry = max(3, d[3] + rng.randrange(-1, 3))
        return {
            "cx": col.qx + d[0], "cy": col.qy + d[1],
            "base_rx": rx, "base_ry": ry, "rx": rx, "ry": ry,
            "max_rx": max(rx, d[4] + rng.randrange(-1, 3)),
            "max_ry": max(ry, d[5] + rng.randrange(-1, 3)),
            "dirt": 0, "linked": core,
            "born": self.tick_count,  # 阶段30r：蓝图登记时刻（挖超过 ROOM_WIP_TICKS 未通就不挡扩张）
            "kind": kind,
            "store": 0.0,  # 阶段10：该房存粮（仅 food 房使用）
            "links": [],  # 房-房互联捷径指向的房索引（成树不成环，见 _pick_link）
        } | self._corridor_shape(rng)

    def _link_cycle(self, col: Colony, i: int, j: int) -> bool:
        """房-房互联图加边 i-j 是否成环：环会围出中心腔体（走廊带互包→整片可挖→融成大洞），禁止。"""
        seen = {j}
        stack = [j]
        while stack:
            u = stack.pop()
            for v in col.chambers[u].get("links", ()):
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        return i in seen

    def _pick_link(self, col: Colony, ch: dict) -> None:
        """房-房互联捷径（2026-10-09 用户"更加丰富"：星形放射→网状）。
        新房按概率连一条到现有近房的捷径；约束：互联图成树不成环（防围洞）+
        短链 ≤35 格 + 每房至多 2 条 + 种子稳定（rng 顺序固定）。"""
        n = len(col.chambers) - 1
        if n < 5 or self.rng.random() >= 0.55:
            return
        cand = []
        for i in range(4, n):
            p = col.chambers[i]
            if len(p["links"]) >= 2:
                continue
            dx, dy = p["cx"] - ch["cx"], p["cy"] - ch["cy"]
            if dx * dx + dy * dy > 35 * 35:
                continue
            cand.append(i)
        if not cand:
            return
        k = self.rng.randrange(len(cand))
        if self._link_cycle(col, n, cand[k]):
            return
        ch["links"].append(cand[k])
        col.chambers[cand[k]]["links"].append(n)

    def _next_room_kind(self, col: Colony) -> str:
        """无模板：新房功能 kind 按需求分配——先保育虫室（蚁后产卵必需），
        再早期交替 food/sleep（休息/存粮尽早可用），然后保 food≥2/sleep≥3，最后按多者补齐均衡。"""
        kinds = [ch.get("kind") for ch in col.chambers]
        if "brood" not in kinds:
            return "brood"
        foods = kinds.count("food")
        sleeps = kinds.count("sleep")
        if foods < 2 and sleeps < 2:
            return "sleep" if sleeps < foods else "food"
        if foods < 2:
            return "food"
        if sleeps < 3:
            return "sleep"
        return "food" if foods <= sleeps else "sleep"

    def _dynamic_chamber_def(self, col: Colony, n: int):
        """无模板：主厅外所有房间均由此生成——全向随机角度、距离每间 +4 格（18 起，越远越大）。
        kind 由 _next_room_kind 按需求分配（育虫/储粮/睡室）。
        越界钳位（不超地图/不贴地表岩层）+ 重叠检测（中心距>半径和+2）；找不到返回 None（下 tick 重新随机角度，不死锁）。
        注：24 起步是为了恒过主厅重叠检查（mrx 8-9 + 主厅 max 9-12 + 2 = 19-23）。"""
        k = n - len(col.chamber_defs)
        for _ in range(8):
            ang = self.rng.uniform(0, math.tau)
            dist = 24 + 4 * k
            mrx, mry = self.rng.randrange(8, 10), self.rng.randrange(8, 10)
            lo_x, hi_x = 6 + mrx, W - 7 - mrx
            # 阶段23"不侵占地面" + 阶段30z：房顶至少留 30 格地表余量。
            # 8 格时 seed5 32k 在 (111,22) 生成粮室，恰贴唯一主竖井顶 → 上下班/回粮/去睡
            # 多向汇流，5×5 挤 39 只@(119,17)；留 30 格把粮室/睡室移出上段入口瓶颈。
            lo_y, hi_y = SURFACE + 30 + mry, H - 3 - mry
            cx = min(max(col.qx + dist * math.cos(ang), lo_x), hi_x)
            cy = min(max(col.qy + dist * math.sin(ang), lo_y), hi_y)
            if any(
                (cx - ch["cx"]) ** 2 + (cy - ch["cy"]) ** 2 < (mrx + ch["max_rx"] + 2) ** 2
                for ch in col.chambers
            ):
                continue
            return (int(round(cx - col.qx)), int(round(cy - col.qy)), 4, 4, mrx, mry, self._next_room_kind(col))
        return None

    def _break_target(self, col: Colony):
        """挖穿目标：第一个尚未连通（linked=False）的房间——按顺序逐个打通。
        阶段30n：无未连通动态房时兜底 core 房（出生即 linked=True）未挖够（cap<20）者——
        房填 0.9 梯度只拉向单一 argmax 最脏房，育虫室（永不当 argmax、BFS 不可达前
        零交通）走廊无人挖（seed5 实测 brood 房 50k cap=0→33+ 抱卵蚁卡死主厅）。
        core 优先（30k 原序）会让突破蚁独占 30k+ tick、动态房 linked 数不足（smoke 5/8）。
        现序=动态房优先、core 兜底：0-10k 窗口（首个动态房出现前）突破蚁本就无事可做，
        恰好挖通育虫室；10k 后动态房预算与 v8 完全一致（linked≥8 门禁不受影响）。"""
        for ch in col.chambers:
            if not ch.get("linked"):
                return ch
        for i, ch in enumerate(col.chambers):
            if i < 4 and self.room_capacity(col, ch) < 20:
                return ch
        return None

    def _take_food(self, col: Colony, amt: float) -> None:
        """阶段10：从粮室取粮（先取库存最多的房）——col.store 是各房之和，消耗必须落到房上。
        开局储备 start_store 优先消耗（粮室接通前的过渡粮源）。"""
        if col.start_store > 0:
            take = min(amt, col.start_store)
            col.start_store -= take
            amt -= take
        if amt <= 0:
            return
        for ch in sorted(
            (ch for ch in col.chambers if ch.get("kind") == "food" and ch["store"] > 0),
            key=lambda c: -c["store"],
        ):
            if amt <= 0:
                break
            take = min(amt, ch["store"])
            ch["store"] -= take
            amt -= take

    def _take_food_from(self, col: Colony, ch: dict, amt: float) -> None:
        """阶段30u：就近吃饭只从"蚂蚁所在那间粮室"扣粮。
        旧版走 _take_food（取库存最多的房）——蚂蚁在 A 房吃，粮却从别处的 B 房扣，
        画面=没人进的粮仓凭空减少（用户"蚂蚁吃饭没跑到粮仓，粮仓的食物虚空减少"）。"""
        take = min(amt, ch["store"])
        ch["store"] -= take

    def _clear_corridor_rocks(self, col: Colony, ch: dict) -> None:
        """阶段9：清主厅→房间走廊带 + 房-房互联带内的巨石（岩不可挖→走廊被堵死→房间/捷径永不通）。"""
        m = col.chambers[0]
        if ch is m:
            return
        ends = [(m["cx"], m["cy"])]
        for lk in ch.get("links", ()):
            ends.append((col.chambers[lk]["cx"], col.chambers[lk]["cy"]))
        for ax, ay in ends:
            x0, x1 = min(ax, ch["cx"]) - 3, max(ax, ch["cx"]) + 3
            y0, y1 = min(ay, ch["cy"]) - 3, max(ay, ch["cy"]) + 3
            for y in range(max(SURFACE, y0), min(H, y1 + 1)):
                for x in range(max(0, x0), min(W, x1 + 1)):
                    i = y * W + x
                    if self.terrain[i] == ROCK and self._in_corridor(ax, ay, ch, x, y):
                        self.terrain[i] = DIRT

    def _break_dig_cells(self, col: Colony) -> list:
        """阶段9：突破前沿 = 挖穿区内可挖土格（4 邻有隧道）。突破蚁朝最近前沿收敛，
        保证走廊从已挖端持续向前推进（旧"拉向房间中心"在窄蜿蜒走廊上迷路→走廊卡死）。
        阶段30k：目标用 col.break_tgt（tick 头缓存，本函数按格调用）。"""
        tgt = col.break_tgt
        if tgt is None:
            return []
        m = col.chambers[0]
        x0, x1 = min(m["cx"], tgt["cx"]) - 4, max(m["cx"], tgt["cx"]) + 4
        y0, y1 = min(m["cy"], tgt["cy"]) - 4, max(m["cy"], tgt["cy"]) + 4
        cells = []
        for y in range(max(SURFACE, y0), min(H, y1 + 1)):
            for x in range(max(0, x0), min(W, x1 + 1)):
                i = y * W + x
                if self.terrain[i] != DIRT or not self._in_break_area(col, x, y):
                    continue
                if any(
                    0 <= x + dx < W and 0 <= y + dy < H and self.terrain[(y + dy) * W + x + dx] == TUNNEL
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
                ):
                    cells.append((x, y))
        return cells

    def _link_rooms(self, col: Colony) -> None:
        """房间被打通（走廊挖进房间、出现隧道格）→ 标记连通，之后由普通 builder 填挖。"""
        for ch in col.chambers[1:]:
            if not ch.get("linked") and self.room_capacity(col, ch) >= 1:
                ch["linked"] = True

    def _in_connected_nest(self, col: Colony, x: int, y: int) -> bool:
        """已连通巢区（普通 builder 可挖）：主厅 ∪ 已连通房间 ∪ 通向它们的走廊 + 两端已连通的互联捷径。
        阶段30z：侧竖井是封闭施工带，只由入口施工队沿 front 从 conn 连续上挖；
        普通 builder 不得从浅层交叉隧道向上挖，否则会挖出“井口通、井底断”的幽灵入口。"""
        for ch in col.chambers:
            if ch.get("linked") and self._in_ellipse(x, y, ch):
                return True
        m = col.chambers[0]
        for ch in col.chambers[1:]:
            if ch.get("linked") and self._in_corridor(m["cx"], m["cy"], ch, x, y):
                return True
            for lk in ch.get("links", ()):
                p = col.chambers[lk]
                if p.get("linked") and self._in_corridor(p["cx"], p["cy"], ch, x, y):
                    return True
        sh = getattr(col, "main_shaft", None)
        if sh is not None:
            return (x, y) in sh["cells"]  # 主竖井
        return col.shaft_x0 <= x <= col.shaft_x1 and y <= col.qy + 2  # 兼容主竖井未生成曲线时

    def _shaft_front(self, sh: dict) -> set:
        """阶段30z：侧竖井施工前沿——从 conn 沿已挖竖井格 BFS，返回与连通段相邻的土格。
        只允许挖这个前沿，保证井口与巢底始终连续（否则会出现地表可进、地下不通的幽灵入口）。"""
        conn = sh.get("conn")
        if conn is None:
            return set()
        cells = sh["cells"]
        t = self.terrain
        seen = {conn}
        stack = [conn]
        front = set()
        while stack:
            x, y = stack.pop()
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p = (x + dx, y + dy)
                if p not in cells or p in seen:
                    continue
                if t[p[1] * W + p[0]] == TUNNEL:
                    seen.add(p)
                    stack.append(p)
                else:
                    front.add(p)
        sh["dug"] = seen
        return front

    def _in_break_area(self, col: Colony, x: int, y: int) -> bool:
        """挖穿区（仅突破蚁可挖）：通向未连通房间的蜿蜒走廊 + 该房间椭圆。
        走廊可穿过其它房间（阶段9：旧排除在路过房间处夹出缺口→长走廊挖穿卡死；预挖房间格无害）。
        阶段30k：目标用 col.break_tgt（tick 头缓存，本函数按格调用）。"""
        tgt = col.break_tgt
        if tgt is None:
            return False
        if self._in_ellipse(x, y, tgt):
            return True
        m = col.chambers[0]
        return self._in_corridor(m["cx"], m["cy"], tgt, x, y)

    def _is_nest_cell(self, col: Colony, x: int, y: int) -> bool:
        """蚁穴格：巢室 ∪ 走廊 ∪ 所有入口竖井。挖出即标记，受维护保护（免土壤回填）。"""
        if y < SURFACE:
            return False
        if self._in_nest_area(col, x, y):
            return True
        if self._in_shaft(col, x, y):
            return True  # 入口竖井（主/侧），蚁穴的一部分
        if getattr(col, "main_shaft", None) is not None:
            return False
        return col.shaft_x0 <= x <= col.shaft_x1 and y <= col.qy + 2  # 兼容主竖井未生成曲线时

    def nearest_food(self, x: int) -> tuple[int, int]:
        if not self.targets:
            return (W // 2, 1)
        best = self.targets[0]
        bd = abs(best[0] - x)
        for tx, ty in self.targets:
            d = abs(tx - x)
            if d < bd:
                bd, best = d, (tx, ty)
        return best

    def _eat_room(self, col: Colony, x: int, y: int) -> dict | None:
        """阶段14 进食目标房：优先"蚂蚁正处在的粮室"，否则最近的（有粮、已连通）粮室。
        与卸粮用的 _carrier_room（每蚁按 max_age 固定分仓）不同——吃饭是就近取食，避免全群饿蚁
        长途奔向同一间远房（实测 37% 蚁在赶路、进食几乎看不见）。"""
        best = None
        bd = 1 << 30
        for ch in col.food_ready:  # 阶段30t：只去就绪粮室（linked + 挖出≥FOOD_ROOM_READY）
            if ch.get("store", 0) < 1:
                continue
            if self._in_ellipse(x, y, ch):
                return ch
            d = abs(x - ch["cx"]) + abs(y - ch["cy"])
            if d < bd:
                bd, best = d, ch
        return best

    @staticmethod
    def _entrance_x(col: Colony, x: float) -> int:
        """阶段30z：地表通勤选最近的已开入口（水平距离），多竖井分流。
        没有侧入口时返回主竖井 x，行为与旧版完全一致。"""
        opens = [e["x"] for e in col.entrances if e.get("open")]
        if not opens:
            return col.qx
        return min(opens, key=lambda ex: (abs(ex - x), ex))

    @staticmethod
    def _carrier_room(col: Colony, a) -> dict | None:
        """阶段30x：载体按 max_age 稳定认一间就绪粮室，多仓分流。
        旧版全群 CARRY 都追 col.food_target（同一间"最空房"），高人口时几十只载体
        与吃饭蚁汇在同一入口；seed5 36k 粮室 (150,85) 旁 5×5 达 36 只。"""
        rooms = col.food_ready
        return rooms[a.max_age % len(rooms)] if rooms else None

    def _maint_idle_slot(self, col: Colony, a) -> tuple[int, int]:
        """阶段29 维护蚁待命槽位：巢心外圈 4-6 格上的稳定角度/层（(max_age, eat_offset) 派生，
        零新增 RNG）——没分到塌陷格的维护蚁各自站到槽位上摊匀成一圈，待命不扎堆。
        槽位格不通（未挖/非蚁穴区）→ 回落巢心（蚁后房必通）。"""
        _k = ((a.max_age * 7 + a.eat_offset) % 360) / 360.0
        _ang = _k * 6.283185307179586
        _r = 4 + (a.eat_offset % 3)
        _ix = col.qx + int(round(_r * math.cos(_ang)))
        _iy = col.qy + int(round(_r * math.sin(_ang)))
        if 0 <= _ix < W and 0 <= _iy < H and self.terrain[_iy * W + _ix] == TUNNEL and self.nest_mark[_iy * W + _ix]:
            return _ix, _iy
        return col.qx, col.qy

    # 阶段13 路线规划：在挖好的通路里给非挖掘蚁 BFS 一条整线，逐格沿走。
    # 可走格 = 地表(皆 TUNNEL) + 地下已标记蚁穴格(nest_mark)；土/岩/封土/探索网(未标记)不可走
    # ——与 _step_ant 通行判定一致（非挖掘蚁不走 DIRT、不进探索网）。
    def _path_cell_ok(self, x: int, y: int, col: Colony) -> bool:
        i = y * W + x
        t = self.terrain[i]
        if t == ROCK or t == DIRT:
            return False
        if i in col.sealed:
            return False
        if y >= SURFACE and not self.nest_mark[i]:
            return False  # 探索网（地底未标记隧道）：非挖掘蚁禁入
        return True

    @staticmethod
    def _dir_between(x0: int, y0: int, x1: int, y1: int) -> int | None:
        dx, dy = x1 - x0, y1 - y0
        for d in range(8):
            if DIRS[d] == (dx, dy):
                return d
        return None

    def _settle_spot(self, col: Colony, ch: dict, a: Ant) -> tuple[int, int]:
        # 阶段19 仅用于渲染：房内"座位"——按蚁自身的 eat_offset/max_age（种子派生、每蚁各异）
        # 用黄金角螺旋在房间椭圆内取一个固定内点，使同房蚁摊满整间房、而非全挤在房界/入口一圈。
        # 若该点尚未挖通（房只挖了一部分）→就近吸到最近的已通蚁穴格，保证座位可达。
        # 纯确定性、零 RNG；只在 frame() 输出时用（不改蚁的实际位置→全局仿真逐位不变，--smoke 不受扰）。
        seed = (a.eat_offset * 1000003 + a.max_age * 2654435761) & 0xFFFFFFFF
        ang = (seed & 0xFFFF) * 6.283185307179586 / 65536.0
        rr = 0.15 + 0.75 * math.sqrt(((seed >> 16) & 0xFFFF) / 65536.0)
        sx = int(round(ch["cx"] + ch["rx"] * rr * math.cos(ang)))
        sy = int(round(ch["cy"] + ch["ry"] * rr * math.sin(ang)))
        if self._path_cell_ok(sx, sy, col):
            return (sx, sy)
        best = None
        bd = 1 << 30
        for oy in range(-4, 5):
            for ox in range(-4, 5):
                nx, ny = sx + ox, sy + oy
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                if not self._path_cell_ok(nx, ny, col) or not self._in_ellipse(nx, ny, ch):
                    continue  # 只吸到房内已通格——不外溢到走廊/别房（否则蚁会睡到房外）
                d = ox * ox + oy * oy
                if d < bd:
                    bd, best = d, (nx, ny)
        return best if best is not None else (sx, sy)

    def find_path(self, sx: int, sy: int, tx: int, ty: int, col: Colony) -> list[tuple[int, int]] | None:
        """BFS 最短路（8 向，只走已挖通路）。返回 [(x,y)...] 起点→终点，无路径返回 None。"""
        if (sx, sy) == (tx, ty):
            return [(sx, sy)]
        if not self._path_cell_ok(tx, ty, col):
            return None
        start, goal = sy * W + sx, ty * W + tx
        # 阶段30y：BFS 内联 _path_cell_ok + seen 位图（原版每个邻居一次函数调用/字典查重，
        # cProfile 里 500 tick 调了 550 万次）。邻居顺序、入队规则完全不变→路径结果逐格相同。
        t = self.terrain
        nm = self.nest_mark
        sealed = col.sealed
        prev = {start: -1}
        seen = bytearray(W * H)
        seen[start] = 1
        q = deque([start])
        while q:
            i = q.popleft()
            if i == goal:
                path = []
                j = i
                while j != -1:
                    path.append((j % W, j // W))
                    j = prev[j]
                path.reverse()
                return path
            x, y = i % W, i // W
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H):
                    continue
                ni = ny * W + nx
                if seen[ni]:
                    continue
                jt = t[ni]
                if jt == ROCK or jt == DIRT or ni in sealed or (ny >= SURFACE and not nm[ni]):
                    continue
                seen[ni] = 1
                prev[ni] = i
                q.append(ni)
        return None

    def tick(self) -> None:
        self.tick_count += 1
        rng = self.rng
        fp = self.food_p
        use = self.use
        decay_use = self.tick_count % 4 == 0  # 通行记忆约 17 秒，巢室不因短时未走而被回填
        for i in range(len(fp)):
            if fp[i]:
                fp[i] -= 1
            if decay_use and use[i]:
                use[i] -= 1
        food = self.food
        # 地面食物投放（阶段17）：不再凭空出现在地面，而是从空中可见地落下——落地才成为地表粮。
        if rng.random() < FOOD_DROP_P:
            cx = W // 2
            x0 = max(6, min(W - 12, cx + rng.randrange(-FOOD_BAND, FOOD_BAND + 1)))
            w = rng.randrange(2, 4)
            for x in range(x0, min(W - 2, x0 + w)):
                self.falling.append([x, 0.0, rng.randrange(FOOD_DROP_MIN, FOOD_DROP_MAX)])
        if self.falling:  # 推进下落：够到地面才计入 food（paste 位置由 y 决定，落地前不算存粮）
            still = []
            for it in self.falling:
                it[1] += FALL_SPEED
                if it[1] >= 1.0:
                    x = it[0]
                    food[W + x] = min(FOOD_CAP, food[W + x] + it[2])
                else:
                    still.append(it)
            self.falling = still
        if self.tick_count % FOOD_SCAN_EVERY == 0:
            self.targets = [(x, 1) for x in range(4, W - 4) if food[W + x] > 12]
        for col in self.colonies:
            # 阶段 7.2：蚁后睡眠周期（碎片化短盹，睡时暂停产卵）——睡 QUEEN_NAP/醒 QUEEN_WAKE → 占日 ~37.5%
            col.queen_rest_t -= 1
            if col.queen_rest_t <= 0:
                if col.queen_resting:
                    col.queen_resting = False
                    col.queen_rest_t = QUEEN_WAKE
                else:
                    col.queen_resting = True
                    col.queen_rest_t = QUEEN_NAP
            # 卵有体积：蚁后只朝育虫室内空闲隧道格产卵；育虫室被幼虫占满→停产，
            # 直到 builder 把育虫室挖大腾出空格（拥挤负反馈 → 挖掘蚁穴的必要性）
            brood_cell = None
            # 拥挤负反馈：巢穴=容器，人口（卵/幼虫有体积）占巢穴空间。巢越满产卵越慢，
            # 挖大巢→空间多→产卵快 → 体现挖掘蚁穴的必要性（平滑阻尼、无死锁）
            crowding = min(1.0, col.population / max(1, col.nest_size))
            # 2026-10-09 软化产卵门："colony 可用粮" = 仓库存 + 工蚁身上在途的粮。
            # 旧版只认 store>=12：just-in-time 经济下仓库贴 0，门槛间歇关闭→卵管线断供→
            # 第一代集中老死无更替→全群灭绝（seed3 实测）。真实蚁后由工蚁直接喂食，不必等仓满。
            available = col.store + sum(a.food for a in col.ants if a.food)
            col.food_available = available  # 缓存本 tick 可用粮：builder 指派门也用（防 JIT 经济死锁）
            # 阶段22 冗余缓冲：存粮跌破储备线→放缓产卵（省粮、留缓冲）；
            # 人口跌破近期峰值 20%（崩溃/老死集中）→ 恢复模式加快产卵补回；
            # 否则正常（人口越大间隔越短，拓荒慢→鼎盛快）。
            # 用"峰值回落"判崩溃（与 _defend 封入口同源）：早期正常生长 pop≈peak 不触发，避免过度膨胀。
            peak = max(col.pop_hist) if col.pop_hist else 0
            if available < FOOD_RESERVE:
                lay_interval = 90
            elif peak >= 20 and col.population <= 0.8 * peak:
                lay_interval = 30
            else:
                lay_interval = max(50, 90 - col.population // 2)
                # 阶段30x：粮越多产卵越快（成比例，不是象征性加速）。
                # 余粮每 LAY_STORE_PER 份 -1 tick，最多 -LAY_BONUS_MAX，下限 LAY_MIN。
                # 粮多→卵密→人口涨→吃得多→粮回落→自动刹车（负反馈闭环，不用人口门限）。
                _surplus = max(0.0, available - FOOD_RESERVE)
                lay_interval = max(LAY_MIN, lay_interval - int(min(LAY_BONUS_MAX, _surplus / LAY_STORE_PER)))
            if (
                not col.queen_resting  # 蚁后睡觉→暂停产卵（睡眠占日的一部分）
                and self.tick_count - col.last_lay >= lay_interval
                and available >= 8
                and col.population < MAX_POP
                and rng.random() < (1.0 - 0.85 * crowding)
            ):
                # 阶段11：卵下在蚁后身边（主厅空闲格），再由工蚁搬运到育虫室；主厅堆满散卵→停产（自然流控）
                brood_cell = self._free_chamber_cell(col, col.chambers[0])
            if brood_cell is not None:
                col.last_lay = self.tick_count
                # 2026-10-10：产卵耗粮 12→8。旧值下产卵是全群落最大耗粮项
                #（pop≥80 时每 50t 一卵 ≈ 2400 粮/万tick，占需求 67%）→ 存粮永远贴 0、
                # 无缓冲扛老死波。降到 8（≈1600/万tick）给存粮留出缓冲空间。
                self._take_food(col, 8)
                # 阶段7.4：先产**卵**（不吃、最短）；阶级不在产卵时定，蛹期羽化才定（真实蚂蚁）
                col.eggs.append({"x": brood_cell[0], "y": brood_cell[1], "age": 0, "moved": False})
            # 阶段7.4 生命周期 ① 卵→幼虫：卵不吃东西，到 EGG_TIME 化幼虫（留在原地）
            # 阶段11：搬运途中的卵暂停发育（不放回育虫室前不孵化，防走廊里化幼虫）
            carrying_eggs = {id(a.egg) for a in col.ants if a.egg is not None}
            molt = []
            for e in col.eggs:
                if id(e) in carrying_eggs:
                    continue
                e["age"] += 1
                if e["age"] >= EGG_TIME:
                    molt.append(e)
            for e in molt:
                col.eggs.remove(e)
                for a in col.ants:  # 卵在搬运途中化蛹→放下（防悬空引用）
                    if a.egg is e:
                        a.egg = None
                col.larvae.append({"x": e["x"], "y": e["y"], "age": 0, "starve": 0})
            # ② 幼虫→蛹：幼虫吃粮（连续 3 顿没粮饿死），到 LARVA_TIME 化蛹并在此定阶级
            pup, starved = [], []
            for l in col.larvae:
                l["age"] += 1
                if l["age"] % 200 == 0:
                    if col.store < 1:
                        l["starve"] = l.get("starve", 0) + 1
                        if l["starve"] >= 3:
                            starved.append(l)  # 连续三顿没粮，幼虫饿死
                        continue
                    l["starve"] = 0
                    self._take_food(col, 1)
                if l["age"] >= LARVA_TIME:
                    pup.append(l)
            for l in starved:
                col.larvae.remove(l)
            for l in pup:
                col.larvae.remove(l)
                col.pupae.append(
                    {"x": l["x"], "y": l["y"], "age": 0, "role": SOLDIER if rng.random() < 0.25 else WORKER}
                )
            # ③ 蛹→成虫：蛹不吃东西，到 PUPA_TIME 羽化出成虫（在蚁后处出生，避免落在未挖通的土里）
            hatched = []
            for p in col.pupae:
                p["age"] += 1
                if p["age"] >= PUPA_TIME:
                    hatched.append(p)
            for p in hatched:
                col.pupae.remove(p)
                col.ants.append(Ant(col.qx, col.qy, p["role"], col.id, self.rng))
            new_level = col.population // NEST_GROW_EVERY  # 巢室随种群长大（半径封顶 max_rx）
            if new_level > col.nest_level:
                col.nest_level = new_level
                for ch in col.chambers:
                    ch["rx"] = min(ch["max_rx"], ch["base_rx"] + new_level)
                    ch["ry"] = min(ch["max_ry"], ch["base_ry"] + new_level)
                for ch in col.chambers:  # 邻居感知封顶：与任一房保持 ≥4 格走廊，防圆形房融合
                    for nb in col.chambers:
                        if nb is ch:
                            continue
                        dx, dy = nb["cx"] - ch["cx"], nb["cy"] - ch["cy"]
                        if abs(dx) >= abs(dy):  # ponytail: 按主分离轴近似椭圆间距，斜向邻居略偏保守
                            lim = abs(dx) - nb["rx"] - 4
                            if lim < ch["rx"]:
                                ch["rx"] = max(ch["base_rx"], int(lim))
                        else:
                            lim = abs(dy) - nb["ry"] - 4
                            if lim < ch["ry"]:
                                ch["ry"] = max(ch["base_ry"], int(lim))
                self._recount_nest_dirt(col)
            # 阶段30z：用户硬性要求——不设任何房间数量上限/目标。
            # 只维持 ROOM_PARALLEL_DIG 个并行施工前沿（完成一个立刻补一个），
            # 既不让全群挤单一前沿（seed5 22k 曾 45 只 builder 同挖一间），也不限制总房数。
            # 地图放不下/重叠/未连通积压才停；房间功能按 _next_room_kind 的当前需求分配。
            _peak = max(col.pop_hist) if col.pop_hist else 0
            _crashing = _peak >= 20 and col.population <= 0.6 * _peak
            healthy = col.store >= FOOD_RESERVE and not _crashing and len(col.reachable_collapsed) < 10
            _wip = sum(
                1 for ch in col.chambers
                if not ch["linked"] and self.tick_count - ch["born"] < ROOM_WIP_TICKS
            )
            if col.expand_cd > 0:
                col.expand_cd -= 1
            _unfinished = sum(1 for ch in col.chambers if ch["dirt"] > 0)
            if healthy and col.expand_cd == 0 and _wip < 2 and _unfinished < ROOM_PARALLEL_DIG:
                n = len(col.chambers)
                d = col.chamber_defs[n] if n < len(col.chamber_defs) else self._dynamic_chamber_def(col, n)
                if d is not None:
                    ch_new = self._new_chamber(col, d, self.rng, core=(n < 4), kind=d[6])
                    col.chambers.append(ch_new)
                    self._pick_link(col, ch_new)  # 房-房互联捷径（成树不成环）
                    self._clear_corridor_rocks(col, ch_new)  # 阶段9：新走廊带清岩，保挖穿可达
                    self._recount_nest_dirt(col)
                else:
                    col.expand_cd = ROOM_EXPAND_RETRY  # 地图满/放不下：稍后再试，不降低房数目标
        for col in self.colonies:
            col.n_builders = sum(1 for a in col.ants if a.building)
            col.n_breakers = sum(1 for a in col.ants if a.breaking)
            col.n_maintainers = sum(1 for a in col.ants if a.maintaining)
            # 阶段30k：突破目标每 tick 缓存一次（_in_break_area/_break_dig_cells 按格调用，
            # 目标选择含 room_capacity，不能内联重算）；育虫室就绪门（抱卵 tp=中心格须可达）
            col.break_tgt = self._break_target(col)
            col.break_digs = self._break_dig_cells(col)  # 阶段9：突破前沿（无未连通房间时为空）
            col.dig_targets = self._dirt_chambers(col)  # 阶段30x：builder 分流目标（每 tick 一次）
            _br = col.brood_room
            _reach = self._reach_mask(col)  # 阶段30x：房间就绪门统一加"真可达"判定（见 _reach_mask）
            col.brood_ready = (
                _br is not None
                and self.terrain[_br["cy"] * W + _br["cx"]] == TUNNEL
                and self.room_capacity(col, _br) >= 20
                and _reach[_br["cy"] * W + _br["cx"]]
            )
            # 阶段10：多房间均分——睡室选"占用最少"（休息蚁强制分散）、粮室选"最空"（食物强制分散）
            col.sleep_rooms = [ch for ch in col.chambers if ch.get("kind") == "sleep"]
            col.sleep_target = None
            if col.sleep_rooms:
                n = len(col.sleep_rooms)
                occ = [0] * n
                inbound = [0] * n
                for a in col.ants:
                    # 阶段30z-3：睡室配额只看“真正在睡/正要睡”的蚁——醒着的 builder/觅食蚁
                    # 穿过睡室不再占名额（旧版按所有蚁的 sleep_room 计 inbound，配额被虚占）。
                    if a.resting:
                        for j, ch in enumerate(col.sleep_rooms):
                            rx, ry = ch["rx"], ch["ry"]
                            if (a.x - ch["cx"]) ** 2 / (rx * rx) + (a.y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                                occ[j] += 1
                                break
                    elif a.tired and 0 <= a.sleep_room < n:
                        inbound[a.sleep_room] += 1
                # 阶段12：在途认领一并计入负载——蚂蚁常在房界**外**歇脚（占用统计漏计），
                # 只按 occ 选房会让同一间永远"最空"→持续流入→入口带堆大群（用户所见"全卡死一处"）
                col.sleep_occ = occ
                col.sleep_inbound = inbound
                col.sleep_cap = [max(1, self.room_capacity(col, ch)) for ch in col.sleep_rooms]
                # 阶段30：只许"已连通+已挖出可用面积"的睡室当目标/认领（与 30x 载体分仓、
                # 阶段25 mci 同族：未连通房不可达，选中=卡死）。施工中睡室 occ 恒 0 → 旧式永远
                # "最空"→ sleep_target 恒指它：① sleep_ready 只看它→新睡室施工期（5-15k tick）
                # 全群落禁睡（seed5 43k 实测 154/173 蚁疲劳满却歇不下）；② 蚁认领它→tp 不可达
                # → 疲劳/饥饿蚁堆在走廊汇流口（13 只单格扎堆+24 只原地空转，用户所见"卡住"）。
                col.sleep_claimable = [
                    j
                    for j, ch in enumerate(col.sleep_rooms)
                    if ch.get("linked") and self.room_capacity(col, ch) >= 20
                    and _reach[ch["cy"] * W + ch["cx"]]
                ]
                # 阶段30u：UI 只画已就绪睡室（与 sleep_claimable 同一门槛）——
                # 旧版 frame 发全部 sleep_rooms，房间没挖开就画 Zz 虚线椭圆。
                col.sleep_ui = [
                    [col.sleep_rooms[j]["cx"], col.sleep_rooms[j]["cy"],
                     col.sleep_rooms[j]["rx"], col.sleep_rooms[j]["ry"]]
                    for j in col.sleep_claimable
                ]
                if col.sleep_claimable:
                    # 阶段30z-3：按“已睡+在途 / 房容量”比例选最空，容量大的房多分蚁。
                    col.sleep_target = col.sleep_rooms[min(
                        col.sleep_claimable,
                        key=lambda j: (
                            (occ[j] + inbound[j]) / col.sleep_cap[j],
                            abs(col.sleep_rooms[j]["cx"] - col.qx) + abs(col.sleep_rooms[j]["cy"] - col.qy),
                            j,
                        ),
                    )]
                    col.sleep_ready = True
                else:
                    col.sleep_ready = False
            else:
                col.sleep_occ = []
                col.sleep_inbound = []
                col.sleep_cap = []
                col.sleep_claimable = []
                col.sleep_ui = []
                col.sleep_ready = False
            col.brood_room = next((ch for ch in col.chambers if ch.get("kind") == "brood"), None)
            col.food_rooms = [ch for ch in col.chambers if ch.get("kind") == "food"]
            # 阶段30t：粮室就绪门——"已连通 + 挖出 ≥FOOD_ROOM_READY 格"才算可用仓容。
            # 旧版只判 linked（_link_rooms 在 capacity≥1 就置位）→ 房间才挖开 1 格就收货、
            # 粮粒瞬间出现在还没挖的土里（用户"粮仓会在巢穴还没挖好之前就启用，然后粮食瞬移过去"）。
            col.food_ready = [
                ch
                for ch in col.food_rooms
                if ch.get("linked") and self.room_capacity(col, ch) >= FOOD_ROOM_READY
                and _reach[ch["cy"] * W + ch["cx"]]
            ]
            # 阶段30x：不再维护全群唯一 food_target——载体按 _carrier_room 每蚁固定分仓，
            # 少掉"最空房"迟滞与逐 tick 切换（旧版全体载体路线随目标抖动会 BFS 风暴）。
            _cap = sum(self.room_capacity(col, ch) * FOOD_PER_CELL for ch in col.food_ready)
            _fill = col.store / max(1.0, _cap)
            col.food_fill = min(1.0, _fill)  # 阶段30t：满仓度 → 动态 觅食/建造 编制（见 t_build）
            # 阶段30v：移除"蚂蚁饭店/派对"（用户：太假，不符合真实蚂蚁）——存的粮不再靠派对消耗，
            # 改由产卵速度消化（余粮越多产卵越快，见下方 lay_interval）。
        if self.tick_count % MAINTAIN_EVERY == 0:  # 重扫塌陷 → 维护蚁修复目标
            # 维护蚁的窄逻辑：只修"塌了的房格"（已挖房格变土），不碰走廊/竖井，更不挖随机土——
            # 否则维护蚁变成全巢挖掘工，蚁穴越修越大
            t = self.terrain
            for col in self.colonies:
                col.collapsed = [
                    i for i in col.nest_cells
                    if t[i] == DIRT and self._in_any_chamber(col, i % W, i // W)
                ]
        all_pos: dict = {}
        occ: dict = {}
        for c in self.colonies:
            for a in c.ants:
                p = (a.x, a.y)
                all_pos[p] = a
                occ[p] = occ.get(p, 0) + 1
        # 阶段25：维护蚁只修"已连通房内 + 可挖（有相邻隧道）"的塌陷格。
        # 未连通房里的不可达→卡住；中间格（无相邻隧道）挖不到（_dig 要求 tn>=1）→也会卡。
        # 只留边界塌陷格：维护蚁从边界往里逐格修，挖开通道后内层格自然变成可挖。
        for col in self.colonies:
            _linked = [ch for ch in col.chambers if ch.get("linked")]
            _t = self.terrain
            col.reachable_collapsed = [
                i for i in col.collapsed
                if any(
                    (i % W - ch["cx"]) ** 2 / (ch["rx"] * ch["rx"])
                    + (i // W - ch["cy"]) ** 2 / (ch["ry"] * ch["ry"]) <= 1
                    for ch in _linked
                )
                and any(
                    _t[j] == TUNNEL for j in (i - 1, i + 1, i - W, i + W) if 0 <= j < len(_t)
                )
            ]
            # 阶段29：塌陷格 1:1 分派给维护蚁——按 (max_age, eat_offset) 排序（开局按种子派生的
            # 稳定每蚁量，零新增 RNG、跨世界确定），前 len(_cells) 名一人一格；其余 mci=None → 待命槽位。
            # 旧版 `max_age % len` 非 1:1：维护蚁数 > 塌陷格数时多蚁抢同一格 → 同格扎堆+原地打转（用户看到的"卡住"）。
            _maint = sorted((b for b in col.ants if b.maintaining), key=lambda b: (b.max_age, b.eat_offset))
            col.maint_mci = {
                id(b): col.reachable_collapsed[k]
                for k, b in enumerate(_maint)
                if k < len(col.reachable_collapsed)
            }
        dead: set[int] = set()
        for col in self.colonies:
            for a in col.ants:
                self._step_ant(a, col, all_pos, occ, dead)
        for col in self.colonies:
            col.ants = [a for a in col.ants if id(a) not in dead]
        self._reclaim(dead)
        for col in self.colonies:
            col.ants = [a for a in col.ants if id(a) not in dead]
        for col in self.colonies:
            self._link_rooms(col)
        self._entrances()
        self._defend()
        if self.tick_count % ORPHAN_SWEEP_EVERY == 0:
            self._sweep_orphans()
            self._sweep_stubs()  # 短须清扫：与孤儿清扫同频，去掉 1-2 格直线小隧道

    def _entrances(self) -> None:
        """多出入口：巢长大后先横向接入已有走廊，再向上开竖井，分散主竖井客流。
        数量随人口自然增加（1→3 个），一次只施工一个；不设总房数限制。"""
        t = self.terrain
        for col in self.colonies:
            if not col.surface_open:
                continue
            # 阶段30z：人口越多、地面通勤越重，才多开入口。最多 3 个（主入口 + 2 条侧竖井）。
            # 第 4 条在 seed5 的 14-26 格交替位上永远与已有侧井冲突（每 tick 12 次重建 shaft → 45k 182ms/tick），
            # 实测 3 个入口已把 44k 扎堆降到 0，所以不再保留必然失败的第 4 条。
            want = 1 + (col.population >= 100) + (col.population >= 220)
            reach = self._reach_mask(col)  # 侧井必须接到蚁后可达的巢，防“开在地表却下不去”的幽灵入口
            if len(col.entrances) < want:
                sh = None
                for _ in range(12):
                    # 左右交替选位，距离 14-26：既离开主竖井，又不跑到巢外荒地。
                    side = -1 if len(col.entrances) % 2 == 0 else 1
                    cand = col.qx + side * self.rng.randrange(14, 27)
                    if not (4 <= cand <= W - 5):
                        continue
                    if any(abs(cand - e["x"]) < 12 for e in col.entrances):
                        continue
                    if any(abs(cand - o.qx) < 20 for o in self.colonies if o is not col):
                        continue
                    sh = self._make_shaft(cand, SURFACE, col.qy)
                    # 必须穿过已有蚂蚁隧道（走廊/房），否则 builder 只能挖已连通巢区，永远接不上。
                    # 取最浅的交点当井底：侧井只挖到最近的既有通道，不强行挖到蚁后层。
                    conns = [
                        (x, y) for x, y in sh["cells"]
                        if y >= SURFACE + 3 and self.nest_mark[y * W + x] and reach[y * W + x]
                    ]
                    if not conns:
                        sh = None
                        continue
                    y_conn = min(y for _, y in conns)
                    sh["dig_y1"] = y_conn
                    sh["conn"] = min(conns, key=lambda p: (p[1], p[0]))
                    sh["cells"] = {(x, y) for x, y in sh["cells"] if y <= y_conn}
                    break
                if sh is not None:
                    self._clear_shaft_rocks(sh)
                    col.entrances.append({
                        "x": sh["x"], "x0": sh["x"] - 1, "x1": sh["x"] + 1,
                        "shaft": sh, "open": False,
                    })
            for e in col.entrances:
                if not e["open"] and t[SURFACE * W + e["x"]] == TUNNEL and reach[SURFACE * W + e["x"]]:
                    sh = e.get("shaft")
                    if sh is None or self._shaft_connected(sh):
                        e["open"] = True
                # 口部会随竖井拓宽漂移、原口格可能被回填：标记指向死格时挪到该竖井带内仍通的口（封堵中不挪）
                if e["open"] and t[SURFACE * W + e["x"]] != TUNNEL and SURFACE * W + e["x"] not in col.sealed:
                    _x0 = e.get("x0", col.shaft_x0)
                    _x1 = e.get("x1", col.shaft_x1)
                    for xx in sorted(range(_x0, _x1 + 1), key=lambda v: abs(v - e["x"])):
                        if t[SURFACE * W + xx] == TUNNEL:
                            e["x"] = xx
                            break
            for e in col.entrances:
                sh = e.get("shaft")
                if not e["open"] and sh is not None:
                    sh["front"] = self._shaft_front(sh)

    def _defend(self) -> None:
        """G5 入口封堵：人口跌破近期峰值 70% → 封所有入口，冷却 600 tick 后自动重开。"""
        for col in self.colonies:
            col.pop_hist.append(col.population)
            if col.seal_cd > 0:
                col.seal_cd -= 1
                if col.seal_cd == 0:
                    self._open_entrances(col)
                continue
            peak = max(col.pop_hist)
            if (
                col.population > 0
                and peak >= 6
                and col.population <= 0.7 * peak
                and any(e["open"] for e in col.entrances)
            ):
                self._seal_entrances(col)
                col.seal_cd = 600

    def _seal_entrances(self, col: Colony) -> None:
        t = self.terrain
        for e in col.entrances:
            if not e["open"]:
                continue
            i = SURFACE * W + e["x"]
            if t[i] == TUNNEL:
                t[i] = DIRT
                self.terrain_version += 1
                col.sealed.add(i)
                col.ants = [a for a in col.ants if a.y * W + a.x != i]  # 口上蚂蚁被埋

    def _open_entrances(self, col: Colony) -> None:
        t = self.terrain
        for i in col.sealed:
            if t[i] == DIRT:
                t[i] = TUNNEL
                self.terrain_version += 1
                self.dig_t[i] = self.tick_count  # 溯源：入口重开
                self.dig_src[i] = 6
        col.sealed.clear()

    def _reclaim(self, dead: set[int]) -> None:
        """土壤回收：远离蚁后且近期无蚂蚁通行的隧道按概率回填为土。
        房格例外：以极小概率自然塌缩（回填成土）→ 维护蚁提前修复（见 MAINTAIN_EVERY 扫描）；
        走廊/竖井仍受保护不塌。"""
        t = self.terrain
        use = self.use
        pos = {(a.x, a.y): a for c in self.colonies for a in c.ants}
        for _ in range(RECLAIM_K):
            i = self.rng.randrange(len(t))
            if t[i] != TUNNEL or use[i]:
                continue
            x, y = i % W, i // W
            if y < SURFACE:
                continue
            in_box = any(
                abs(x - c.qx) <= NEST_RADIUS and abs(y - c.qy) <= NEST_RADIUS
                for c in self.colonies
            )
            decayed_room = False
            if self.nest_mark[i]:
                if in_box:
                    continue  # 巢格在巢芯箱内：免回填（房/走廊/竖井都该常驻）
                # 蚁穴格：走廊/竖井不塌；房格极慢塌缩（维护蚁修复目标，防房间长期无人维护失修）
                if not self._in_any_chamber(self.colonies[0], x, y) or self.rng.random() >= ROOM_DECAY_P:
                    continue
                decayed_room = True
            elif self.rng.random() >= RECLAIM_P:
                # 探索网（含巢芯箱内的）照常可回收——旧版整箱免回填，巢芯周围网格永久积累成"散点"
                continue
            t[i] = DIRT
            self.terrain_version += 1
            if not decayed_room:
                self.web_count = max(0, self.web_count - 1)  # 回填的必是未标记隧道
            a = pos.get((x, y))
            if a is not None:  # 塌陷活埋
                dead.add(id(a))
                a.food = 0

    def _sweep_orphans(self) -> None:
        """孤儿清扫：与蚁巢断联的探索网碎片（蚂蚁再也够不到的死格）整批回填。
        随机回收 ~6 万 tick/格，碎片会永久残留成"散点"（2026-10-10 诊断：171 片/337 格）。
        从巢格 BFS，不可达的未标记隧道全部回填；压在碎片上的蚂蚁活埋（同塌陷）。
        纯确定性（无 RNG 调用）。"""
        t = self.terrain
        reached = bytearray(W * H)
        dq = deque()
        for c in self.colonies:
            for i in c.nest_cells:
                if t[i] == TUNNEL and not reached[i]:
                    reached[i] = 1
                    dq.append(i)
        while dq:
            i = dq.popleft()
            x, y = i % W, i // W
            for dx, dy in DIRS:
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H:
                    j = yy * W + xx
                    if t[j] == TUNNEL and not reached[j]:
                        reached[j] = 1
                        dq.append(j)
        pos = {(a.x, a.y): a for c in self.colonies for a in c.ants}
        dead = set()
        n = 0
        for i in range(SURFACE * W, W * H):
            if t[i] == TUNNEL and not self.nest_mark[i] and not reached[i]:
                t[i] = DIRT
                n += 1
                a = pos.get((i % W, i // W))
                if a is not None:  # 压在孤儿碎片上的蚂蚁活埋
                    dead.add(id(a))
                    a.food = 0
        if n:
            self.terrain_version += 1
            self.web_count = max(0, self.web_count - n)
            for c in self.colonies:
                c.ants = [a for a in c.ants if id(a) not in dead]

    def _sweep_stubs(self, max_len: int = 2) -> None:
        """短须清扫：探路网的死端分支若 ≤max_len 格，整支回填（2026-10-10 用户"避免一两个的
        直线小隧道"）。探路蚁 tn==1 延伸 1 宽须常只挖 1-2 格就停（下一格 tn≥2 禁挖 / 蚁转向）
        → 满巢戳出单格/双格短须（诊断：194 死端里 192 支长度 ≤2，即散点/竖直小隧道）。
        从每个死端(tn==1)沿唯一 1 宽路回溯到分支点(tn≥2)或非网格，链长 ≤max_len 则整支回填。
        纯确定性（无 RNG 调用）。"""
        t = self.terrain

        def is_web(i: int) -> bool:
            return t[i] == TUNNEL and not self.nest_mark[i] and i // W >= SURFACE

        def tn(i: int) -> int:
            x, y = i % W, i // W
            c = 0
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                xx, yy = x + dx, y + dy
                if 0 <= xx < W and 0 <= yy < H and t[yy * W + xx] == TUNNEL:
                    c += 1
            return c

        reclaim: set[int] = set()
        for i in range(SURFACE * W, W * H):
            if not is_web(i) or tn(i) != 1:
                continue  # 只从死端出发
            chain = []
            prev, cur = None, i
            while cur is not None:
                chain.append(cur)
                x, y = cur % W, cur // W
                nxt = None
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < W and 0 <= yy < H:
                        j = yy * W + xx
                        if j != prev and is_web(j) and tn(j) <= 2:
                            nxt = j
                            break
                prev, cur = cur, nxt
            if len(chain) <= max_len:
                reclaim.update(chain)
        if not reclaim:
            return
        pos = {(a.x, a.y): a for c in self.colonies for a in c.ants}
        dead = set()
        n = 0
        for i in reclaim:
            if t[i] != TUNNEL:
                continue
            t[i] = DIRT
            n += 1
            a = pos.get((i % W, i // W))
            if a is not None:  # 压在短须上的蚂蚁活埋（同塌陷）
                dead.add(id(a))
                a.food = 0
        self.terrain_version += 1
        self.web_count = max(0, self.web_count - n)
        for c in self.colonies:
            c.ants = [a for a in c.ants if id(a) not in dead]

    def _step_ant(self, a: Ant, col: Colony, all_pos: dict, occ: dict, dead: set[int]) -> None:
        rng = self.rng
        a.age += 1
        if a.age >= a.max_age:
            dead.add(id(a))  # 老死
            return
        # 休息（阶段17 重构：只在睡室睡，不在路上/别处睡）。
        # 旧逻辑"地下累了即原地歇"是为绕开"睡室导航不可靠"的坑（旧直线梯度→seed5@80k 78% 蚁疲劳拉满、
        # 仅 3% 在歇）；阶段13 上了 BFS 后走睡室已可靠，故改回写实版：累了(疲劳满)→先干完当前这趟
        # （带粮/抱卵的先卸货，见下 tp 分支）→走到**自己认领的睡室**才开睡。去睡室的路上**不再累积疲劳**
        # （"路上不再消耗精力"）——不再像旧版卡在半路疲劳拉满原地空转。枢纽无需特判：只认睡室就够。
        at_home = (
            0 <= a.sleep_room < len(col.sleep_rooms)
            and self._in_ellipse(a.x, a.y, col.sleep_rooms[a.sleep_room])
        )
        if a.fatigue >= FATIGUE_MAX:
            if not a.tired:
                a.tired = True  # 累了→想睡（先把当前这趟干完，再回睡室）
                # 阶段30g：出发去睡先等固定延迟（max_age%600：worker 9000-18000 恰为 600 整数倍区间
                # →mod 后 0-599 近均匀、每蚁恒定、零新增 RNG）。孵化批次同步疲劳→全员同刻冲睡室
                # →走廊枢纽合流扎堆（seed5 24k 实测 19 只@(126,82)）；错峰 600 tick 把波摊平。
                # 阶段30z：主竖井上段（y<SURFACE+40）变累的蚁不等待——它们已在唯一出入口，
                # 等待期 tp=None 会原地堆积（seed5 33k 27 只 fat100@(120,23)）；立刻去睡=向下疏散。
                # 阶段30z-3：builder 也不等待——等待期没有建造梯度（30i/30j），蓝蚁只能在工地
                # 小范围来回蹭（用户所见“蓝色蚂蚁卡住”）；睡室已按容量比例分摊，直接去睡不会挤一间。
                a.sleep_wait = 0 if (a.y < SURFACE + 40 or a.building) else a.max_age % 600
                a.sleep_room = -1  # 阶段30z-3：每个入睡周期重新按容量比例认领，避免永久挤同一间
        if a.tired and not a.resting and a.sleep_wait > 0:
            a.sleep_wait -= 1  # 等待期继续干当前的活（疲劳满不影响工作，只延迟出发）
            if a.y < SURFACE:
                a.sleep_wait = 0  # 地表无处可干：到点前也必须立刻回巢睡，不能在地表入口绕圈
        if a.resting:
            a.fatigue = max(0.0, a.fatigue - REST_RECOVER * a.pace)  # *pace：个体节律→恢复快慢各异→去同步
            if at_home:
                a.energy = min(150.0, a.energy + REST_REGEN)  # 睡室内休息：额外被动回血
            if a.fatigue <= REST_MIN_FAT:
                a.resting = False
                a.tired = False  # 歇够了→复工
        elif a.tired:
            if at_home:
                a.resting = True  # 走到睡室→开睡（只在睡室睡）
                # 睡着的蚁不是建造/维护蚁：否则建筑蓝/维护绿会一直挂在睡眠蚁身上，
                # 且 n_builders 把睡蚁算进编制（用户所见“蓝蚁堆在睡室不动”）。
                a.building = a.breaking = a.maintaining = False
                # 阶段30d：真实坐标直接挪到房内"座位"（_settle_spot 黄金角螺旋点）——
                # 阶段19 座位只在渲染侧展开，睡蚁真实坐标全堆在睡室入口格（seed5 24k 单格 14 只
                # → 检测器扎堆报警），且入口格 occ 被睡蚁虚高→CROWD_P 把去睡蚁推出房外
                # （"睡室入口带 38-50"历史 bug 签名）。挪仿真坐标后：堆叠消失、入口无假拥挤、
                # 起床离房从房内各处分散。座位零 RNG（eat_offset/max_age 种子派生）。
                sr = col.sleep_rooms[a.sleep_room] if 0 <= a.sleep_room < len(col.sleep_rooms) else None
                if sr is not None:
                    spot = self._settle_spot(col, sr, a)
                    if self._path_cell_ok(spot[0], spot[1], col):
                        a.x, a.y = spot  # 座位必须是已通格；找不到就不挪（留在入口，安全）
            # 去睡室途中：不累积疲劳（精力只补给、不再消耗）
        else:
            a.fatigue = min(FATIGUE_MAX, a.fatigue + FATIGUE_RATE * a.pace)  # 干活→累（*pace：累快慢各异）
        a.energy -= 0.03 * a.size  # *size：体型各异→能耗不同→大蚁更勤觅食（均值 1.0 不变，不扰经济）
        # 阶段14：饥饿节律——干活/休息都按各自 pace 累积饥饿，到阈值回粮室吃饭（见下方 eating 块）。
        a.hunger += HUNGER_RATE * a.pace
        # 回退领食：尚无粮室时（开局/粮室未挖）就地取过渡粮（start_store），否则开局全饿死。
        if (
            a.hunger >= HUNGER_MAX
            and not a.eating
            and not col.food_ready
            and col.store >= 1
        ):
            self._take_food(col, EAT_FOOD)
            a.energy = min(150.0, a.energy + EAT_ENERGY)
            a.hunger = 0.0
        if a.energy <= 0:
            dead.add(id(a))  # 饿死
            return
        self.use[a.y * W + a.x] = 255  # 踩过的格子近期免回填
        for dx, dy in DIRS:
            e = all_pos.get((a.x + dx, a.y + dy))
            if e is not None and e.colony != a.colony and id(e) not in dead:
                if a.role == SOLDIER or rng.random() < 0.25:
                    e.hp -= 4
                    if e.hp <= 0:
                        dead.add(id(e))
                        if e.food and e.y < SURFACE:
                            i = e.y * W + e.x
                            self.food[i] = min(255, self.food[i] + e.food)
                    break
        if a.resting:
            return  # 原地休息（不移动/不觅食/不挖，仅防御）——睡室内额外回血，原地歇也有疲劳恢复
        # 阶段14 可见进食：进食中→原地停留、逐 tick 回能量；饿了且已走到粮室→就地开饭。
        # （饿蚁的 BFS 目标已指向粮室，见下方 tp 分支——先走过去，到这儿才开吃；吃满 EAT_TIME 收工。）
        if a.eating:
            a.energy = min(150.0, a.energy + EAT_ENERGY / EAT_TIME)
            a.eat_t -= 1
            if a.eat_t <= 0:
                a.eating = False
            return  # 进食中：不移动/不觅食（可见停留）
        # 阶段30y：只在真饿了才找粮室（原版每只蚁每 tick 都扫一遍所有粮室，结果只在饿了时用）。
        # 阶段30q：维护蚁也吃饭——1489 把饿了的维护蚁 tp 到粮室，旧版这里 `not a.maintaining`
        # 会让它们到了粮室永远不开吃、饥饿不归零 → 全群绿蚁永久卡粮室中心饿死。
        er = (
            self._eat_room(col, a.x, a.y)
            if (a.hunger >= HUNGER_MAX and a.state == SEEK
                and a.egg is None and a.y >= SURFACE)  # 巢内（地下）才吃粮室
            else None
        )
        if (
            er is not None
            and self._in_food_pile(col, a.x, a.y, er)  # 站在真正有粮的堆内才开吃（门口不算）
            and occ.get((a.x, a.y), 0) < EAT_CROWD_MAX  # 本格已挤→不在此开吃，先走开（防进食堆叠）
        ):
            self._take_food_from(col, er, EAT_FOOD)  # 阶段30u：只从所在的这间粮室扣粮
            a.eating = True
            a.eat_t = EAT_TIME
            a.hunger = 0.0  # 吃完重置饥饿节律
            return  # 本 tick 起开吃（不移动）
        food = self.food
        sig = self.food_p if (a.state == SEEK and not a.maintaining and not a.resting and a.egg is None) else None
        target = self.nearest_food(a.x) if (a.state == SEEK and a.y < SURFACE and not a.building and not a.maintaining and not a.resting and a.egg is None) else None
        if a.role == WORKER:
            if a.resting:
                a.building = a.breaking = a.maintaining = False  # 休息中：放下手头工作
            has_pending = any(not e["open"] for e in col.entrances)
            # 形状判据：竖井未通 或 有巢室待挖 或 有待挖入口 → 继续建（比"凑格子数"真实）
            work_left = (not col.surface_open) or (col.chambers_dirt > 0) or has_pending
            # 阶段21：三类分工（觅食 / 建造 / 维护）按固定比例自动调配，蚁可在三类间自由转换。
            # 编制 = 种群 × ROLE_SPLIT；满编不再招募，超编的自动退回觅食。
            # 阶段30t：粮满时动态上调建造编制（觅食蚁转去挖巢），缺粮自动退回觅食。
            # 满仓度 col.food_fill 由 tick() 头算好（存粮/就绪仓容）；50% 起线性升到 ROLE_BUILD_FULL。
            _k = min(1.0, max(0.0, (col.food_fill - 0.5) * 2.0))
            t_build = int(col.population * (ROLE_SPLIT[1] + (ROLE_BUILD_FULL - ROLE_SPLIT[1]) * _k))
            t_maint = int(col.population * ROLE_SPLIT[2])
            # 塌陷积压临时扩编：已连通房格被土埋时多派维护蚁（修完自动退回觅食）。
            # 阶段25：只算可达（已连通房内）的塌陷格——未连通房里的维护蚁够不着，扩编也无用。
            # 阶段29：只升不降（max 保底）——旧式 len 在 (3t, 5t) 区间时 1+len//5 < 保底编制，
            # "扩编"分支反而缩编，积压期间维修力塌缩。
            if col.reachable_collapsed and len(col.reachable_collapsed) > t_maint * 3:
                t_maint = min(int(col.population * 0.30), max(t_maint, 1 + len(col.reachable_collapsed) // 5))
            # 退编（双向转换的另一半）：没活干 / 超编 → 转回觅食
            # 阶段30h-3：站在未标记探索网里的 builder 不退编——退编=变 forager，而 forager
            # 被准入规则（1464）禁出禁入未标记格→builder 干活时进网、退编后困在网中央
            # 全候选被拒→1658 原地转向站桩（seed5 50k 14 只 wander 根因，"怎么进的网"之谜）。
            # 留编（nesting=True 豁免准入）+ 房/睡梯度拉出网→踏上有标记格后退编照常。
            _in_web = (
                a.y >= SURFACE
                and self.terrain[a.y * W + a.x] == TUNNEL
                and not self.nest_mark[a.y * W + a.x]
            )
            if a.building and (not work_left or col.n_builders > t_build) and not _in_web:
                a.building = False
            # builder 招募：维护保底满员后才招 builder（2026-11-10 用户"每种蚂蚁固定保底比例"）——
            # 否则空闲蚁先被 builder 抢走→维护编制塌缩（睡醒/死亡后补不回来）。
            if (
                a.egg is None
                and not a.building
                and not a.maintaining
                and not a.resting
                and a.state == SEEK
                and a.food == 0
                and a.y >= SURFACE
                # 2026-10-09 软化 builder 门（同产卵门）：用"可用粮"(仓库+在途)而非仅 store。
                # 旧版 store>=15 在 JIT 经济下死锁：存粮贴 0→无 builder→突破蚁绝迹→
                # 粮室走廊不通→粮食无处入库→store 永远回不到 15（seed5 实测 10k 后建造冻结）。
                # 2026-10-10 排除搬运蚁：在途粮计入 food_available→返巢 CARRY 蚁在竖井里
                # (y>=3) 被中途转成 builder（committed 永不释放：chambers_dirt 恒>0）→
                # 每趟往返都可能掏空一名觅食者→小群下觅食池归零、存粮永 0、突破蚁饿死、
                # 动态房间走廊永久卡死（seed5 实测 45k 后拾取冻结、粮室 50k 未 linked）。
                and col.food_available >= 15
                and work_left
                and col.n_maintainers >= t_maint  # 维护保底（固定比例下限）
                and col.n_builders < t_build
            ):
                a.building = True
                col.n_builders += 1
            # 挖穿（break-through）：builder 中专选少量"突破蚁"打通未连通房间的蜿蜒走廊+房间。
            # 上限 3：多 1 只只会换 RNG 轨迹（seed5 实测 4 只反而连通更慢），连通快慢靠房间总数约束
            break_tgt = col.break_tgt  # 阶段30k：tick 头缓存（目标选择含 room_capacity，按蚁重算太贵）
            if a.breaking and (break_tgt is None or not a.building):
                a.breaking = False
            if a.building and break_tgt is not None and not a.breaking and col.n_breakers < 3:
                a.breaking = True
                col.n_breakers += 1
            # 维护蚁：按固定比例保底编制（t_maint，塌陷积压时临时上浮）
            if a.maintaining and (
                not col.surface_open or not col.nest_cells or col.n_maintainers > t_maint
            ):
                a.maintaining = False
            if (
                a.egg is None
                and not a.maintaining
                and not a.building
                and not a.resting
                and col.surface_open
                and col.nest_size >= NEST_TARGET
                and col.n_maintainers < t_maint
                and a.state == SEEK
                and a.food == 0
            ):
                a.maintaining = True
                col.n_maintainers += 1
            # 阶段11：工蚁就近捡拾散卵（蚁后下在主厅）→ 搬运到育虫室
            # 阶段30k：育虫室未就绪（中心格未挖/容量<20）不捡——抱卵 tp=育虫室中心，
            # 中心不可达时 BFS 全败→抱卵蚁全卡死主厅汇成团（seed5 实测 50k 主厅 33 只、
            # 5×5 区域 33-43 只）；等待期卵留在主厅原位发育（主厅是合法蚁穴格）
            if (
                a.egg is None
                and a.food == 0
                and not a.resting
                and not a.building
                and not a.breaking
                and not a.maintaining
                and a.y >= SURFACE
                and col.brood_ready
            ):
                e = next(
                    (e for e in col.eggs if not e["moved"] and abs(e["x"] - a.x) + abs(e["y"] - a.y) <= 2),
                    None,
                )
                if e is not None:
                    e["moved"] = True  # 捡走即标记（防同 tick 两只蚁抢同一枚卵）
                    a.egg = e
        nesting = a.building and a.state == SEEK
        prev = a.d
        # 阶段30z：侧竖井只派 1/3 builder 施工（其余继续挖房/走廊），
        # 否则新入口一出现全群 builder 一起涌去，反而制造新拥堵。
        pending = (
            next((e for e in col.entrances if not e["open"]), None)
            if nesting and (a.max_age // 600) % 3 == 0
            else None
        )
        ex = self._entrance_x(col, a.x)  # 阶段30z：本 tick 最近的可通地表入口
        best_i = None
        best_d = prev
        best_score = float("-inf")
        cands = (prev - 1, prev, prev + 1)
        if a.egg is not None:
            cands = cands + (prev + 4,)  # 抱卵允许 180° 掉头（直行惯性下直行走廊会走到地表才回头，白跑一趟）
        st = None
        if (
            a.egg is None
            and not (a.state == CARRY and a.food)
            and a.tired  # 累了→回睡室睡（先卸货：带粮/抱卵时不睡，见上方条件）
            and a.sleep_wait <= 0  # 阶段30g：错峰延迟到点才出发（防同步疲劳波在走廊枢纽合流扎堆）
            # 阶段30o：突破蚁不认领睡室——睡室梯度 1.2 压过突破前沿梯度 0.7，
            # 疲劳突破蚁被拽回睡室、醒来后重选 3 只又全是疲劳蚁（全群落 70% fat100），
            # 突破班组原地打转：seed5 20k-50k 第 6 房 cap 恒 0、linked 卡 5/8。
            # 疲劳满不影响干活（见 1185 行），突破蚁等打通当前走廊（break_tgt 变 None/换房）后再睡。
            and not a.breaking
            and col.sleep_ready  # 睡室已挖出可用面积（未挖=土椭圆，拽过去=罢工）
            # 阶段30z：旧版用“离蚁后曼哈顿≤90”防远处空转——侧竖井把地面通勤拉到竖井上段后，
            # (97,18) 距蚁后 110 格，疲劳蚁永远不认领睡室、tp=None 原地空转（smoke 48k 15 只）。
            # 改为“站在已标记巢区（房/走廊/竖井）”即可认领：巢再大、竖井再高都覆盖，地表/探索网不算。
            # 阶段30z-3：地表（y<SURFACE）的特殊情况——地表格永远没有 nest_mark，疲劳觅食蚁
            # 在入口 y=2 时永远认领不到睡室、tp=None，只能在井口 2×2 绕到老死（用户所见全蚁种“卡住”）。
            # 地表蚁允许立即认领，BFS 会带它走最近入口下巢。
            and (a.y < SURFACE or self.nest_mark[a.y * W + a.x])
        ):
            # 阶段12：每蚁各认领一间睡室（"占用+在途"最少者，距离破平）→同批累倒的蚁摊到多间。
            # 旧全局 sleep_target 单间：休息蚁常在房界外歇脚→occ 漏计→该房永远"最空"→
            # 持续流入→入口带堆 ~50 只（用户所见"全卡死一处"）。在途即时计数→同 tick 后续蚁换房。
            # 阶段30：只在 sleep_claimable（已连通+已挖出）里认领——认领施工中的房=tp 不可达=卡死。
            n = len(col.sleep_rooms)
            # 阶段30o：认领不可认领（未连通/塌陷到 <20 格）的房也要改认领——
            # 旧式只判下标越界：曾认领的房后来不可达/被回填，蚁仍被睡室梯度 1.2 拽向它
            # （seed5 34-47k 实测 15-19 只 fat100 在房外空转=smoke phase12 徘徊报警）。
            if n and (a.sleep_room < 0 or a.sleep_room >= n or a.sleep_room not in col.sleep_claimable) and col.sleep_claimable:
                r = min(
                    col.sleep_claimable,
                    key=lambda j: (
                        (col.sleep_occ[j] + col.sleep_inbound[j]) / col.sleep_cap[j],
                        abs(a.x - col.sleep_rooms[j]["cx"]) + abs(a.y - col.sleep_rooms[j]["cy"]),
                        j,
                    ),
                )
                a.sleep_room = r
                col.sleep_inbound[r] += 1
            st = col.sleep_rooms[a.sleep_room] if 0 <= a.sleep_room < n else None
        # 阶段30b：builder 去睡觉（st 已定）时只关"个性震荡"（digging 门控 1496 行）——
        # 震荡±1.4 逐 tick 翻向是 17k 瓶颈扎堆 11 只@(112,93) 的根因（边挖边荡停格、疲劳波汇入）。
        # 挖土(1457)/房填梯度(1540) **保持对去睡 builder 开启**——v3 曾全关：同步疲劳波把 builder
        # 全打 fat100、睡房又没挖到 capacity 20→全员睡不了、builder 全停、睡房永远挖不完=死锁
        # （50k 探针 pop 卡 60、linked 恒 4、sleeping≈0）。去睡 builder 顺路挖=睡房及时就绪=死锁破解器。
        # 拥挤队列由两道"停格拥挤门"防（本格≥REST_CROWD_MAX 则强制完成动作前进）：
        # 挖停门 1684 行（DIRT 干等 DIG_P）、走路停门 1689 行（WALK_P 40% 停留，v4 补：
        # 只挡挖停时瓶颈仍堆 9 只@(116,86)@20k）。
        # 阶段30c：去睡 builder 只拿"睡室内"的房偏置 3.0、不再被建造房梯度 0.9 拉
        # （1520/1560 行）——否则途中停在枢纽啃建造房土（seed5 20k 枢纽 11 只@110,97）。
        # 建造吞吐：阶段30z 起不设房数目标；有土继续挖，全挖完且健康就开下一间，
        # 施工积压只由 ROOM_WIP_TICKS/未连通计数限速（同样不是房数限制）。
        digging = nesting and st is None
        # 阶段29：mci 由 tick() 每 tick 统一 1:1 分派（见 col.maint_mci）——一人一格，多蚁不再抢同一格。
        mci = col.maint_mci.get(id(a)) if a.maintaining else None
        # 阶段13：当前"预定路线"的终点（BFS 目标）。BFS 自带迷宫解→CARRY 直接定粮室
        # （旧两段 hub 逻辑是直线梯度时代的补丁，已删）。SEEK 地下段终点=地表入口格
        # (qx, SURFACE-1)：到 (qx,3) 就路径耗尽上不了地表，须把终点放到地表首格，
        # 蚁踏上地表后 y<SURFACE → tp 自动切最近食物簇。
        tp = None
        if st is not None:
            tp = (st["cx"], st["cy"])
        elif a.egg is not None and not a.resting:
            if a.y < SURFACE:
                tp = (ex, SURFACE)
            elif col.brood_room is not None:
                tp = (col.brood_room["cx"], col.brood_room["cy"])
        elif a.state == CARRY and not a.resting:
            ft = self._carrier_room(col, a)
            if a.y < SURFACE:
                tp = (ex, SURFACE)
            else:
                tp = (ft["cx"], ft["cy"]) if ft is not None else (col.qx, col.qy)
        elif a.state == SEEK and not a.maintaining and not a.resting and a.egg is None:
            er = self._eat_room(col, a.x, a.y) if a.hunger >= HUNGER_MAX else None
            if er is not None:
                # 阶段14：饿了→去（就近的）粮室吃饭（地上先入巢，地下直奔粮室）；到粮室后由上方 eating 块开吃
                tp = (ex, SURFACE) if a.y < SURFACE else (er["cx"], er["cy"])
            elif a.y < SURFACE and target is not None:
                tp = (target[0], target[1])
            else:
                # 阶段30m：疲劳的地下觅食蚁不汇主竖井（30g"等待期继续干活"让 tired forager
                # 一波波涌上竖井觅食→seed5 26.7k 上竖井 15-18 只队列+单格 15 结）。
                # 疲劳蚁停止通勤地表：tp=None→原地局部游荡（下方 1.0 向上偏置同样关闭），
                # ≤599t 等待期后睡室梯度 1.2 接管。
                # 阶段30z：目标改为最近的可通入口，地下客流按所在位置分流到不同竖井。
                tp = None if a.tired else (ex, SURFACE - 1)
        elif a.maintaining:
            # 阶段25b/25d/25e/29：维护蚁接入 BFS（原只靠曼哈顿梯度→死胡同转圈卡住；BFS 最短路不会卡）。
            # - 饿了（hunger>=HUNGER_MAX）：去（就近）粮室吃饭——原维护蚁无吃饭 tp，会饿到
            #   hunger 暴涨、在巢心无目标转圈（a.d 震荡，前端看起来"原地打转"）。
            # - 分到塌陷格（mci 非 None，1:1）：tp=塌陷格 mci 相邻隧道格，走到那儿再挖。
            # - 没分到（mci 为 None）：tp=自己的待命槽位——巢心外圈 4-6 格、按 (max_age, eat_offset)
            #   派生的稳定角度/层（零新增 RNG）摊匀成一圈；旧版全员回巢心→多只待命蚁挤同一 1-2 格
            #   （实时画面 9 只绿蚁挤蚁后房=用户看到的"卡住扎堆"）。槽位格不通→回落巢心。
            if a.hunger >= HUNGER_MAX:
                er = self._eat_room(col, a.x, a.y)
                tp = (ex, SURFACE) if a.y < SURFACE else (er["cx"], er["cy"]) if er is not None else (col.qx, col.qy)
            elif mci is not None:
                _mx, _my = mci % W, mci // W
                for _dx, _dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    _xx, _yy = _mx + _dx, _my + _dy
                    if 0 <= _xx < W and 0 <= _yy < H and self.terrain[_yy * W + _xx] == TUNNEL:
                        tp = (_xx, _yy)
                        break
            else:
                tp = self._maint_idle_slot(col, a)
        # 阶段25e/29：维护蚁无活干、不饿、没在睡、且已到自己待命槽位(曼哈顿≤1)→站定待命（不移动不转向）。
        # 旧版只在"恰好站在巢心格"站定，但维护蚁会在巢心附近游荡、每 tick 重选朝向→原地打转
        # （spin 探针：巢心附近 dirchg 20-29/40、moved 4-8）。有塌陷格/饿了/要睡才起身。
        if a.maintaining and mci is None and a.hunger < HUNGER_MAX and not a.resting:
            if abs(a.x - tp[0]) + abs(a.y - tp[1]) <= 1:
                return
        # 阶段30q：疲劳蚁困在探索网须内（地底未标记单宽隧道）时朝巢心方向有额外拉力——
        # 睡室/建造梯度指向网外（不可达），只靠它们会在 2-3 格小环里无限绕圈（seed5 23k
        # 11 只 tired builder 在 (106,93)→(106,92)→(105,92) 循环，单格 11 只=smoke 扎堆报警）。
        # 网须按 tn==1 规则单宽无分支 → 朝巢心必是回根出口；1.5 > 睡室梯度 1.2，先出网。
        esc_web = (
            a.tired
            and a.y >= SURFACE
            and self.terrain[a.y * W + a.x] == TUNNEL
            and not self.nest_mark[a.y * W + a.x]
        )
        # 阶段30y：个性震荡每只蚁每 tick 只算一次（原先在 8 个候选方向里重复算同值）。
        osc = (
            math.sin(self.tick_count * (0.12 + (a.eat_offset % 5) * 0.05) + (a.max_age % 97) * 0.13)
            if digging and not a.breaking
            else 0.0
        )
        for dd in cands:
            d = dd % 8
            nx = a.x + DIRS[d][0]
            ny = a.y + DIRS[d][1]
            if not (0 <= nx < W and 0 <= ny < H):
                continue
            i = ny * W + nx
            t = self.terrain[i]
            if t == ROCK or i in col.sealed:
                continue  # 封土视同岩体，蚂蚁不会把封口挖开
            if (
                ny >= SURFACE
                and t == TUNNEL
                and not self.nest_mark[i]
                and not nesting
                and not a.maintaining
            ):
                # 探索网准入：地底未标记隧道（探路蚁挖的网）只许挖掘蚁进出。
                # 觅食/搬运/休息蚁走已标记巢区（房间/走廊/竖井，皆 3 宽以上）+ 地表——
                # 防觅食蚁钻进单格网迷宫出不来（2026-09-23 实测 seed3/11 全群饿死的根因）
                # 阶段30h-2 曾给"去睡蚁"开豁免，实测无效还惹网口汇流扎堆（11 只@(109,98)）→ 回退；
                # 困网根因已堵在源头：builder 在网内不退编（见 1277 行阶段30h-3）。
                continue
            if t == DIRT:
                # 行为分类：挖掘（开井/巢室/入口）是 builder 专职，维护蚁专职修复蚁穴格，其余只走隧道
                if a.maintaining:
                    if not self.nest_mark[i]:  # 维护蚁只修复标记的蚁穴格
                        continue
                elif not nesting:
                    # 阶段30b v4：去睡的 builder 仍可挖（v3 全关→全群疲劳死锁，见 1369 行注释）——
                    # 顺路挖是睡房及时就绪（capacity≥20）的关键，睡眠循环的自洽靠它。
                    continue
                tn = 0
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    xx, yy = nx + dx, ny + dy
                    if 0 <= xx < W and 0 <= yy < H and self.terrain[yy * W + xx] == TUNNEL:
                        tn += 1
                # 只挖与隧道正交相连的边界格：杜绝孤立点和对角虚线
                if tn < 1:
                    continue
                if not col.surface_open:
                    # 开井期：只在蜿蜒主竖井带内向上挖 → 竖井保持 3 宽，不扩散
                    if not self._in_shaft(col, nx, ny):
                        continue
                elif a.breaking:
                    # 挖穿（单独行为）：突破蚁只挖"通向未连通房间的蜿蜒走廊 + 该房间"，绝不挖穿其它墙
                    if not self._in_break_area(col, nx, ny):
                        continue
                elif not a.maintaining:
                    # 普通建造：只挖已连通巢区（主厅/已连通房间/它们的走廊）。
                    # 阶段30s：关闭探路蚁自由开挖（用户"老是挖出一些没人走、也不好看的羊肠小道，关掉探路，只要挖巢"）——
                    # 旧分支让 1/10 builder 从任意隧道格向外挖 1 宽须（WEB_CAP=600/WEB_RADIUS=40），
                    # 未标记格觅食/休息蚁一律拒走（见 1541 行）→ 纯视觉死路，实测 30k 占隧道 8%。
                    if not self._in_connected_nest(col, nx, ny):
                        # 阶段30z：侧竖井只允许入口施工队挖“从 conn 连续上长的 front”，
                        # 防止浅层交叉隧道把井口挖通、井底却还隔着土。
                        sh = pending.get("shaft") if pending is not None else None
                        if sh is None or (nx, ny) not in sh.get("front", ()):
                            continue
            # 直行惯性：强偏好当前方向 + 小随机扰动 → 转弯平滑缓慢、动作连贯不抽搐
            score = rng.random() * 0.3 + (0.6 if dd == prev else 0.0)
            if digging and not a.breaking:
                # 奇形怪状蚁穴：挖掘蚁叠加"每蚁个性震荡"——按 uid 派生不同相位/频率的正弦，
                # 左/右转向得分随正弦交替 → 边挖边左右摇摆，走出 S 形/波浪/歪扭走廊。
                # 振幅 1.4 > 直行惯性 0.6：摇摆主导直行，形状夸张；房间填充仍由 CHAMBER_BIAS 3.0 主导（房还是房，路变歪）
                # 阶段30e：突破蚁豁免——震荡±1.4+噪声0.7 与突破梯度(候选间差≤1.4)同量级，
                # 突破蚁在走廊里做无漂移随机游走（seed5 25k 实测：3 只 12.5k 目标房 cap 恒 0、
                # 蚁漂离前沿 21 格）→ 走廊永远挖不穿、新房永远 linked 不了。
                # 走廊蜿蜒形态创建时已烘焙进 _in_corridor 带，突破蚁只管把带子直直挖穿。
                if dd == prev - 1:
                    score += osc * 1.4
                elif dd == prev + 1:
                    score -= osc * 1.4
                score += rng.random() * 0.7  # 挖掘扰动加大（0.3→1.0 总量）：偶尔猛拐、岔出怪角
            if t == DIRT:
                score += 0.3 + tn * 0.15  # 积极挖土：优先奔向可挖的边界土，凹格(tn高)优先圆腔
                if nesting and not a.breaking and not self._in_any_chamber(col, nx, ny):
                    # 连接目的：格两侧有隧道（两条走廊之间的缝）→ 优先打通，挖出捷径/环路。
                    # 房间内(椭圆内)不桥接：否则会把房间间隔墙吃穿、整巢融成一个洞（2026-09-23 用户反馈失控）
                    # （岩边列保证 nx±1/ny±1 不越界）
                    if (
                        self.terrain[ny * W + nx - 1] == TUNNEL
                        and self.terrain[ny * W + nx + 1] == TUNNEL
                        or self.terrain[(ny - 1) * W + nx] == TUNNEL
                        and self.terrain[(ny + 1) * W + nx] == TUNNEL
                    ):
                        score += BRIDGE_BIAS
                if nesting and col.surface_open and self._in_any_chamber(col, nx, ny):
                    # 阶段30c：去睡 builder 只在**自己认领的睡室**内拿房偏置——建造房的 3.0 会
                    # 让它停在走廊枢纽啃建造房土（seed5 20k 枢纽 11 只扎堆）；睡室偏置保留
                    # （去睡顺路挖睡室=睡室及时挖通，否则 v3 型睡眠死锁）
                    # 阶段30i：tired 蚁（sleep_wait 等待期 st=None）同样不拿建造房偏置——
                    # 3.0+房填 0.9 把等待期 tired builder 拉去在建房的突破走廊单格瓶颈排队
                    # （seed5 31k 实测 (147,94) 8-10 只、smoke 31k/44k 报 11/13 只）
                    if (st is None and not a.tired) or (st is not None and self._in_ellipse(nx, ny, st)):
                        score += CHAMBER_BIAS  # 强引导：优先填定义好的巢室（成房间，非乱爬）
                elif nesting and not col.surface_open and self._in_shaft(col, nx, ny):
                    score += SHAFT_BIAS  # 开井期：优先挖蜿蜒主竖井带内，保走廊不扩散
            crowd = occ.get((nx, ny), 0)
            if crowd:
                score -= CROWD_P * crowd  # 拥挤：别往堆满同类的格子里挤
            if esc_web:
                score -= (abs(nx - col.qx) + abs(ny - col.qy)) * 1.5  # 阶段30q：出网须（见上）
            if sig is not None and not nesting:
                # 阶段9：食物信息素只引导觅食蚁——builder/突破蚁被竖井高信息素拽去"上班路"→挖穿卡死
                score += sig[i] * 0.06
            if a.state == SEEK and food[i] and not a.maintaining and a.egg is None:
                score += food[i] * 0.1
            if target is not None:
                # 2026-10-10 觅食修复：旧 0.4 < 直行惯性 0.6，地表觅食蚁转向不了食物——
                # 只能沿直线走到地图边缘再折返（1D 随机游走），唯一能捡到的是恰好在
                # 入口口掉落的食物（实测拾取全在 x∈[117,123]）→ 吞吐量取决于掉粮 RNG
                # 运气 → 存粮攒不出缓冲 → 老死波一来全群饿死（seed5 150k 实测 44k 冻结）。
                # 0.8 > 0.6：觅食蚁主动走向最近食物簇，吞吐量与掉粮位置解耦。
                score -= (abs(nx - target[0]) + abs(ny - target[1])) * 0.8
            if nesting:
                # 阶段30b v4：房填/探路/开井梯度对去睡 builder 保持开启（v3 关闭→死锁，见 1369 行注释）
                if pending is not None:
                    # 入口施工：先把人带到井底 conn，再从 conn 沿 front 连续向上挖。
                    _sh = pending.get("shaft")
                    _conn = _sh.get("conn") if _sh is not None else None
                    if _sh is not None and _conn is not None:
                        if (a.x, a.y) in _sh.get("dug", ()) and a.y <= _conn[1]:
                            score -= DIRS[d][1] * 0.9
                            _px = self._shaft_center(_sh, a.y)
                            score -= abs(nx - _px) * 0.6
                        else:
                            score -= (abs(nx - _conn[0]) + abs(ny - _conn[1])) * 0.7
                    else:
                        score -= DIRS[d][1] * 0.6
                        score -= abs(nx - pending["x"]) * 0.2
                elif not col.surface_open:
                    # 阶段1 开井：强向上，各列直上（不拉向中轴，保住走廊宽度）
                    score -= DIRS[d][1] * 0.6
                else:
                    if a.breaking:
                        # 阶段9：挖穿 = 收敛到最近的可挖前沿格（保证从已挖端持续推进到房间，不迷路）；
                        # 小随机扰动保留（多前沿格竞争时方向不僵死）
                        if col.break_digs:
                            bx, by = min(col.break_digs, key=lambda p: (nx - p[0]) ** 2 + (ny - p[1]) ** 2)
                            score -= ((nx - bx) ** 2 + (ny - by) ** 2) ** 0.5 * 0.7
                        score += rng.random() * 0.3
                    else:
                        # 阶段2 建室（已通地表）：向"最没填满"的巢室靠拢，保证各房都挖（非只挖主厅）。
                        # 梯度须 > 直行惯性 0.6：旧值 0.45 时 builder 会一头扎进先开始的走廊挖到底、
                        # 其它房间的走廊永远不开工（实测 seed11 育虫室 6000 tick 一格未挖）
                        if st is None and not a.tired:
                            # 阶段30c：去睡 builder 不再被"最没填的建造房"梯度拉——方向由睡室
                            # 梯度 1.2 管，否则途中拐向建造房在枢纽拥堵
                            # 阶段30i：等待期 tired 蚁（st 未认领）同样不拉——防其在
                            # 在建房突破走廊瓶颈排队扎堆（seed5 31k (147,94) 11 只）
                            # 阶段30x：builder 按 max_age 分到前 3 个待填房之一（原来全员只
                            # 追"土最多"那一间，高人口时 200 名 builder 挤同一前沿 → 5×5 33+ 报警）
                            md = col.dig_targets[a.max_age % len(col.dig_targets)] if col.dig_targets else None
                            if md is not None:
                                score -= (abs(nx - md[0]) + abs(ny - md[1])) * 0.9
                        else:
                            # 去睡 / 等待期 tired builder 不拉向建造房（30c/30i），只给弱回巢心
                            # （防漂到地图边缘，离巢越远拉力越大）
                            # 阶段30j：tired 蚁（等待期 st 未认领/去睡中）不拿回巢心——
                            # 30i 拆了建造房拉力后，0.03 回巢心把整波等待期 tired builder
                            # 缓漂进主厅汇成团（seed5 48-50k 实测 5×5 内 31-34 只、region>32）；
                            # 等待期留在工地（本地挖土/桥接/震荡力足够），≤599t 后睡室梯度 1.2 接管
                            if not a.tired:
                                score -= (abs(nx - col.qx) + abs(ny - col.qy)) * 0.03
            else:
                # 阶段30j：等待期 tired 蚁（sleep_wait>0）不走信息素——无目标的等待期 builder
                # 被主通道信息素带走到枢纽滞留成团（seed5 48-50k 主厅 31-34→枢纽 (120,84) 33-43）；
                # 关掉后只剩本地力（挖土 0.3+0.15tn/震荡/噪声/拥挤罚）→锚在各自工地分散挖，
                # ≤599t 后 sleep_wait 归零、睡室梯度 1.2 + 信息素恢复（去睡途中导航不受影响）
                if not (a.tired and a.sleep_wait > 0):
                    score += self.use[i] * PATH_BIAS  # 走廊信息素：越被踩熟越优先→汇流主通道，不另开新洞
                if (
                    a.state == SEEK
                    and a.y >= SURFACE
                    and not a.maintaining
                    and not a.resting
                    and a.egg is None
                    and not a.tired
                ):
                    # 地下觅食蚁强向上（须>直行惯性0.6，否则宽腔内横向惯性把蚁困住、爬不上竖井；休息蚁除外）
                    # 阶段30m：tired 蚁关闭向上偏置+巢心列汇聚——与 30m tp=None 配套，
                    # 等待期疲劳蚁原地局部游荡而非被拽上竖井（否则 tp 关了偏置还在→照样汇竖井）
                    score -= DIRS[d][1] * 1.0
                    # 无模板后走廊网变大：向上被土挡住时先横向汇到最近入口竖井列再爬升
                    score -= abs(nx - ex) * 0.2
                if a.state == CARRY and not a.resting:
                    # 阶段10：搬运蚁走向目标粮室（最空那间→食物多间均分）；无粮室时回退蚁后处。
                    # 地表上先回主入口竖井口（唯一上下通道）——直瞄地下粮室会让蚁走到粮室 x 坐标的地表后下不去
                    # 2026-10-10 修复：旧 0.3/0.25 < 直行惯性 0.6，搬运蚁转向不了竖井/粮室走廊。
                    # 旧代码靠"食物恰掉在入口口"侥幸成立（回巢只有 5 格）；觅食改主动导航后
                    # 拾取点在 70 格外的食物簇，回巢 50-140 格→搬运蚁走到地图边缘才被迫折返
                    # （实测 drop 5/万 vs pick 20/万，15 只载体迷路饿死在回巢路上，store 永远 0）。
                    # 0.8 > 0.6：沿已有隧道直线汇回竖井口→粮室。
                    ft = self._carrier_room(col, a)
                    if a.y < SURFACE:
                        tx, ty = (ex, SURFACE)
                    elif (abs(a.x - col.qx) + abs(a.y - col.qy)) > 12:
                        # 2026-10-10 两段导航：地下先回主厅枢纽，再进粮室。
                        # 直瞄远端粮室的直线梯度在迷宫走廊里会卡死：seed3 实测搬运蚁
                        # 沿竖井被梯度拽进育虫室死端（育虫室恰在竖井线上），梯度指向
                        # 粮室方向的土墙（中间隔 3 格土）→ 22 只载体在 4×10 区域振荡
                        # 到能量耗尽（200 tick 内 e=7→0 全灭，30k 零入库）。
                        # 所有走廊都由主厅放射而出→第二段梯度与走廊走向一致，可靠。
                        tx, ty = (col.qx, col.qy)
                    else:
                        tx, ty = (ft["cx"], ft["cy"]) if ft is not None else (col.qx, col.qy)
                    score -= (abs(nx - tx) + abs(ny - ty)) * 0.8
                if a.egg is not None and not a.resting:
                    # 阶段11：搬运卵→直走育虫室。梯度必须 > 直行惯性 0.6（否则蚁会一路走直线把卵带出巢）
                    br = col.brood_room
                    if br is not None:
                        # 地表上先回主入口竖井口（唯一上下通道）——直瞄地下育虫室会让蚁沿地表走到育虫室
                        # x 坐标后下不去、越走越远（同 CARRY 粮的坑，实测 seed11 载体沿地表走 100 格）
                        if a.y < SURFACE:
                            tx, ty = (ex, SURFACE)
                        else:
                            tx, ty = (br["cx"], br["cy"])
                        score -= (abs(nx - tx) + abs(ny - ty)) * 0.9
            if a.maintaining:
                # 维护蚁：走向分派的塌陷格修复；没分到则走向自己的待命槽位（0.7>直行惯性 0.6，拽得动）
                if mci is not None:
                    score -= (abs(nx - (mci % W)) + abs(ny - (mci // W))) * 0.6
                else:
                    _idle = self._maint_idle_slot(col, a)
                    score -= (abs(nx - _idle[0]) + abs(ny - _idle[1])) * 0.7
            if a.resting:
                # 阶段10：休息蚁留在睡室歇脚（目标=占用最少的睡室→多间均分，不挤一间）
                rt = col.sleep_target
                if rt is not None:
                    score -= (abs(nx - rt["cx"]) + abs(ny - rt["cy"])) * 0.5
            elif st is not None:
                # 阶段7.3/12：累了(疲劳满)且在巢内→走向**自己认领的**睡室歇脚。
                # 2026-10-10 修复疲劳陷阱：旧 0.3 < 直行惯性 0.6，疲劳蚁根本转向不了睡室，
                # 在巢里带满疲劳打转直到老死（seed5 实测 41 蚁 30 疲劳满、0 在睡室→群落塌缩）。
                # 梯度须 > 直行惯性 0.6 + 觅食上偏置 1.0 才能拽动疲劳蚁：1.2。
                # 阶段12：st=该蚁认领房（"占用+在途"最少，见循环前认领逻辑），非全局单间→摊开不堆。
                score -= (abs(nx - st["cx"]) + abs(ny - st["cy"])) * 1.2
            if score > best_score:
                best_score, best_i, best_d = score, i, d
        if best_i is None:
            a.d = (a.d + rng.randrange(1, 3)) % 8
            return
        # 阶段13 路线规划：非挖掘蚁沿"在挖好的通路里画好的一条线"（BFS 最短路）逐格走——
        # 整条路线一次定好→流畅、不撞墙、不折返、不逐 tick 抖动（"抽搐/卡死一处"根因修复）。
        # 挖掘蚁（nesting，要挖新土、无预设通路）保持打分兜底。
        # 维护蚁接入 BFS（阶段25c）：tp=mci 旁隧道格，走最短路过去再挖——
        # 旧"维护蚁只靠曼哈顿梯度"会走死胡同原地转圈（用户反复看到"卡住"的真正根因）。
        # 路径须同时满足：目标未变、蚁还在路径上、下一格仍可走——否则从当前位置重算
        # （塌陷封路/目标切换/角色切换自动自愈）。
        # 阶段30z-3：疲劳蚁即使仍是 builder（nesting）也走 BFS 去睡室——
        # 旧版只给非挖掘蚁规划，tired builder 在探索网/窄走廊里靠局部梯度打转（20k 22 只
        # 蓝蚁 600t 活动范围≤10 格，用户所见“蓝色蚂蚁卡住”）；睡室目标明确时交给 BFS 最稳。
        sleep_trip = a.tired and st is not None
        if (not nesting or sleep_trip) and tp is not None:
            if (a.x, a.y) == tp:
                # 已站在目标上：路径耗尽，无需 BFS（否则每 tick 白算一条平凡路径）；
                # 清掉旧路径，靠打分兜底就地行为（拾粮/卸粮后 tp 变化再重新规划）
                a.path = None
                a.path_i = 0
                a.path_target = None
                if a.maintaining and mci is None and a.hunger < HUNGER_MAX:
                    # 阶段25e：维护蚁无活干、不饿、已到巢心→站定待命（不移动不转向，
                    # 否则打分兜底每 tick 重选朝向→精灵原地打转，用户反复看到的"绿色蚁旋转"）。
                    return
            else:
                p = a.path
                ok = (
                    p is not None
                    and a.path_target == tp
                    and a.path_i < len(p)
                    and p[a.path_i] == (a.x, a.y)
                    and a.path_i + 1 < len(p)
                    and self._path_cell_ok(p[a.path_i + 1][0], p[a.path_i + 1][1], col)
                )
                if not ok and a.path_target == tp and a.path_fail > 0:
                    # 已知该目标不可达（如新建房中心格尚未挖通）：冷却期内不重算 BFS，
                    # 退回打分兜底自由行走——否则每 tick 白算一次 BFS → 性能风暴
                    # （2026-10-14 定位：空 food_target 房中心 + 未挖通睡室中心，pop 越大越糟）
                    a.path_fail -= 1
                elif not ok:
                    p = self.find_path(a.x, a.y, tp[0], tp[1], col)
                    a.path = p
                    a.path_i = 0
                    a.path_target = tp
                    # 不可达→记冷却，PATH_RETRY tick 后才重试（目标挖通后自愈）
                    a.path_fail = 0 if p is not None else PATH_RETRY
                    ok = p is not None and a.path_i + 1 < len(p)
                if ok:
                    nxp, nyp = p[a.path_i + 1]
                    # 拥挤门：同一条路的蚁会在瓶颈格（竖井口/走廊交汇）汇流堆死——
                    # 路径不看 CROWD_P，下一格已挤(≥REST_CROWD_MAX)就放弃本 tick 沿路径，
                    # 退回打分兜底（CROWD_P 会绕开挤格），蚁自然分流（实测无此门单格堆 17）
                    if occ.get((nxp, nyp), 0) < REST_CROWD_MAX:
                        best_i = nyp * W + nxp
                        best_d = self._dir_between(a.x, a.y, nxp, nyp)
        nx, ny = best_i % W, best_i // W
        a.d = best_d
        if self.terrain[best_i] == DIRT:
            # 阶段30b：本格已挤(≥REST_CROWD_MAX)时强制本 tick 挖完前进——
            # 否则"在本格干等 DIG_P 掷出"的挖停循环会让瓶颈格变成队列（seed5 17k 实测 11 只）。
            # rng 调用次数不变（仍掷一次），只是拥挤时不再因掷输而停留。
            if (
                rng.random() >= DIG_P[0 if a.role == WORKER else 1]
                and occ.get((a.x, a.y), 0) < REST_CROWD_MAX
            ):
                return  # 还在挖这一格
        elif (
            rng.random() >= WALK_P
            and occ.get((a.x, a.y), 0) < REST_CROWD_MAX
        ):
            # 阶段30b v4：走路停同样加拥挤门（与上方挖停门对称）——WALK_P 40% 停留无门时，
            # 瓶颈格"走到→掷输停留→后来者再走到→再堆"照样成队列（seed5 20k 实测 (116,86) 9 只）；
            # 本格已挤则强制迈步，队列自然排开。rng 调用次数不变。
            return  # 这 tick 没迈开步
        if a.maintaining:
            src = 5  # 维护蚁修复
        elif a.building:
            src = 4 if a.breaking else 2
        else:
            src = 0  # 走已有隧道，_dig 不会改写
        self._dig(nx, ny, src)
        if src == 5:  # 修好一格立刻从积压里摘掉（旧版要等下一次 MAINTAIN_EVERY 全量重扫）
            _ci = ny * W + nx
            if _ci in col.collapsed:
                col.collapsed.remove(_ci)
        a.x, a.y = nx, ny
        # 路线推进：确实走到路径下一格才前进（WALK_P 没触发时不跳格）
        if a.path is not None and a.path_i + 1 < len(a.path) and a.path[a.path_i + 1] == (a.x, a.y):
            a.path_i += 1
        if a.egg is not None:
            a.egg["x"], a.egg["y"] = nx, ny  # 卵随工蚁移动
        occ[(nx, ny)] = occ.get((nx, ny), 0) + 1  # 实时占用：同 tick 内后来者避开
        if ny < SURFACE and not col.surface_open:
            col.entrances.append({
                "x": col.main_shaft["x"], "x0": col.shaft_x0, "x1": col.shaft_x1,
                "shaft": col.main_shaft, "open": True,
            })  # 主入口
        if ny < SURFACE:
            col.surface_open = True
        if a.state == CARRY:
            self.food_p[best_i] = 255
        if a.state == SEEK and food[best_i] > 0 and not a.maintaining and a.egg is None:
            take = min(round(14 * a.carry), food[best_i])  # *carry：个体搬运量各异（round 保 int 供 bytearray；均值≈14 吞吐稳）
            food[best_i] -= take
            a.food = take
            a.state = CARRY
        elif a.state == CARRY:
            # 阶段30t：卸粮必须真的走进就绪粮室，不能路过主厅就把粮记进远处房间。
            # 阶段30x：走进**任何一间**就绪粮室都可卸（与 _carrier_room 的多仓分流配套），
            # 不再要求全群载体都挤进同一间。
            ft = next((ch for ch in col.food_ready if self._in_ellipse(nx, ny, ch)), None)
            if ft is not None or (not col.food_ready and col.home_cell(nx, ny)):
                col.food_gained += a.food
                if ft is not None:
                    # 食物有体积：房容量=隧道格×FOOD_PER_CELL，满则弃多余。
                    give = min(a.food, max(0.0, self.room_capacity(col, ft) * FOOD_PER_CELL - ft["store"]))
                    ft["store"] += give  # 食物入房即计入 col.store（=各房之和）
                else:
                    # 还没有就绪粮室（开局/粮室未挖好）→ 在巢心上交为过渡粮，不再丢弃。
                    col.start_store += a.food
                a.food = 0
                a.energy = min(150.0, a.energy + 50)
                a.state = SEEK  # 兵蚁回巢卸粮后继续觅食
        elif (
            a.egg is not None
            and col.brood_room is not None
            and self._in_ellipse(a.x, a.y, col.brood_room)
        ):
            a.egg["moved"] = True  # 阶段11：卵存入育虫室，停止搬运
            a.egg = None

    def frame(self) -> dict:
        c0 = self.colonies[0] if self.colonies else None
        # 阶段19：休息蚁在房内摊匀显示——只改输出坐标（蚁的真实位置不动），故全局仿真逐位不变、
        # --smoke 不受影响；避免"全挤在房界/入口一圈"。仅当蚁已进房时替换，出房后自动回落真实坐标。
        ants = []
        for c in self.colonies:
            for a in c.ants:
                dx, dy = a.x, a.y
                if a.resting:
                    ch = next((r for r in c.sleep_rooms if self._in_ellipse(a.x, a.y, r)), None)
                    if ch is not None:
                        dx, dy = self._settle_spot(c, ch, a)
                ants.append([a.uid, dx, dy, a.colony, a.state, a.role, a.d, a.maintaining, a.resting, a.size, 1 if a.eating else 0, 1 if a.building else 0])
        # 阶段7.4：三阶段幼体，带阶段标记（0=卵 1=幼虫 2=蛹）供前端分色渲染
        larvae = (
            [[e["x"], e["y"], 0] for c in self.colonies for e in c.eggs]
            + [[l["x"], l["y"], 1] for c in self.colonies for l in c.larvae]
            + [[p["x"], p["y"], 2] for c in self.colonies for p in c.pupae]
        )
        q = [v for c in self.colonies for v in (c.qx, c.qy, 1 if c.queen_resting else 0)]
        s = [v for c in self.colonies for v in (c.population, int(c.store), len(c.eggs) + len(c.larvae) + len(c.pupae), int(c.food_gained))]
        # 阶段15 粮仓可视化：每间粮室直接发"房中心/半径/存粮"，前端画成堆满的谷堆
        # （旧版发房壁边缘 16 格、每格一个 4×2 像素小黄块——太小、没有"粮仓堆满"的感觉）
        fc = []
        for c in self.colonies:
            for ch in c.food_rooms:
                amt = int(ch["store"])
                if amt < 2:
                    continue
                # 阶段30t：带上真实仓容（挖出的格数×FOOD_PER_CELL）——前端按装载率画粮堆，
                # 旧版写死 amt/1000 → 粮堆与房间实际挖开面积/容量无关。
                cap = int(self.room_capacity(c, ch) * FOOD_PER_CELL)
                fc.append([ch["cx"], ch["cy"], ch["rx"], ch["ry"], amt, cap])
        e = [
            [
                en["x"]
                for en in c.entrances
                if en["open"] and SURFACE * W + en["x"] not in c.sealed
            ]
            for c in self.colonies
        ]
        return {
            "v": self.terrain_version,
            "f": _b64(self.food),
            "day": round((self.tick_count % DAY) / DAY, 3),
            "a": ants,
            "l": larvae,
            "q": q,
            "s": s,
            "fc": fc,
            "e": e,
            # 阶段30u：只发已就绪睡室（linked + 挖出≥20 格）——旧版发全部 sleep_rooms，
            # 房间还没挖开就画 Zz 虚线椭圆（用户"睡室在挖出来之前就出现了 UI"）。
            "sr": getattr(c0, "sleep_ui", []) if c0 is not None else [],
            "ff": [[it[0], round(it[1], 3), it[2]] for it in self.falling],  # 阶段17：空中下落中的食物 [x,y,量]
        }

    def terrain_str(self) -> str:
        return _b64(self.terrain)

