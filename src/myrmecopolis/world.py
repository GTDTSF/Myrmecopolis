from __future__ import annotations

import base64
import math
import random
from array import array
from collections import deque

from .ant import Ant
from .colony import Colony
from .constants import W, H, SURFACE, ROCK, DIRT, TUNNEL, DIRS, SEEK, CARRY, WORKER, SOLDIER, MAX_POP, FOOD_CAP, FOOD_DROP_P, FOOD_DROP_MIN, FOOD_DROP_MAX, FOOD_SCAN_EVERY, FOOD_BAND, WALK_P, DIG_P, NEST_TARGET, NEST_RADIUS, CHAMBER_BIAS, SHAFT_BIAS, NEST_GROW_EVERY, MAX_CHAMBERS, PATH_BIAS, RECLAIM_K, RECLAIM_P, MAINTAIN_EVERY, MAINTAINER_MAX, ROOM_DECAY_P, CROWD_P, FOOD_PER_CELL, FATIGUE_RATE, FATIGUE_MAX, REST_RECOVER, REST_MIN_FAT, REST_REGEN, REST_CROWD_MAX, DAY, QUEEN_NAP, QUEEN_WAKE, EGG_TIME, LARVA_TIME, PUPA_TIME, BRIDGE_BIAS, EXPLORER_MOD, WEB_CAP, WEB_RADIUS, ORPHAN_SWEEP_EVERY, HUNGER_RATE, HUNGER_MAX, EAT_TIME, EAT_ENERGY, EAT_FOOD, EAT_CROWD_MAX, PATH_RETRY

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
        self.terrain_version = 0
        self.tick_count = 0
        # 格子溯源（纯记账，不影响行为/RNG）：dig_t=该格最后一次被挖开的 tick（0=未挖开过），
        # dig_src=谁挖的：1=开局 2=builder 3=探路蚁 4=突破蚁 5=维护蚁 6=入口重开。
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
                if col.chambers and self._in_nest_area(col, x, y) and all(
                    abs(x - e["x"]) >= 3 for e in col.entrances
                ):
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
            # 清除竖井走廊（nx-1..nx+1 的 3 宽硬门列）内的巨石：竖井只准在此列挖，
            # 遇不可穿的 ROCK 就会卡死在半空挖不穿（阶段8 单群落：无备份群落，必须保证能通地表）
            for y in range(3, ny + 1):
                for x in range(nx - 1, nx + 2):
                    if self.terrain[y * W + x] == ROCK:
                        self.terrain[y * W + x] = DIRT
            col = Colony(cid, nx, ny)
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

    def _in_corridor(self, ax: int, ay: int, ch: dict, x: int, y: int) -> bool:
        """主厅(ax,ay)→房间 ch 的蜿蜒走廊：沿连线按"曲率形态"偏移中心线（_corridor_shape 按种子抽）→ 通道曲折多样。"""
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

    def _in_nest_area(self, col: Colony, x: int, y: int) -> bool:
        """巢内可挖区 = 各圆形巢室 ∪ 主厅到每间房的 3 宽窄走廊。
        房间（圆、宽）与走廊（窄）由此分化：只在这两区内挖 → 不会连成一大圆。"""
        if not col.chambers:
            return False
        if self._in_any_chamber(col, x, y):
            return True
        m = col.chambers[0]
        mx, my = m["cx"], m["cy"]
        for ch in col.chambers[1:]:
            if self._in_corridor(mx, my, ch, x, y):
                return True
            for lk in ch.get("links", ()):  # 房-房互联捷径（成树不成环）
                p = col.chambers[lk]
                if self._in_corridor(p["cx"], p["cy"], ch, x, y):
                    return True
        return False

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
        """巢室内已挖通隧道格数（该房间的"体积"）。育虫室满/储粮室满的判据。"""
        t = self.terrain
        rx, ry = ch["rx"], ch["ry"]
        cap = 0
        for y in range(ch["cy"] - ry, ch["cy"] + ry + 1):
            for x in range(ch["cx"] - rx, ch["cx"] + rx + 1):
                if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                    if 0 <= x < W and 0 <= y < H and t[y * W + x] == TUNNEL:
                        cap += 1
        return cap

    def _recount_nest_dirt(self, col: Colony) -> None:
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

    def _most_dirt_chamber(self, col: Colony):
        best, bd = None, 0
        for ch in col.chambers:
            if ch["dirt"] > bd:
                best, bd = (ch["cx"], ch["cy"]), ch["dirt"]
        return best

    def _in_ellipse(self, x: int, y: int, ch: dict) -> bool:
        rx, ry = ch["rx"], ch["ry"]
        return (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1

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
            lo_y, hi_y = SURFACE + 3 + mry, H - 3 - mry
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
        """挖穿目标：第一个尚未连通（linked=False）的房间——按顺序逐个打通。"""
        for ch in col.chambers[1:]:
            if not ch.get("linked"):
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
        保证走廊从已挖端持续向前推进（旧"拉向房间中心"在窄蜿蜒走廊上迷路→走廊卡死）。"""
        tgt = self._break_target(col)
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
        """已连通巢区（普通 builder 可挖）：主厅 ∪ 已连通房间 ∪ 通向它们的走廊 + 两端已连通的互联捷径。"""
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
        return False

    def _in_break_area(self, col: Colony, x: int, y: int) -> bool:
        """挖穿区（仅突破蚁可挖）：通向未连通房间的蜿蜒走廊 + 该房间椭圆。
        走廊可穿过其它房间（阶段9：旧排除在路过房间处夹出缺口→长走廊挖穿卡死；预挖房间格无害）。"""
        tgt = self._break_target(col)
        if tgt is None:
            return False
        if self._in_ellipse(x, y, tgt):
            return True
        m = col.chambers[0]
        return self._in_corridor(m["cx"], m["cy"], tgt, x, y)

    def _is_nest_cell(self, col: Colony, x: int, y: int) -> bool:
        """蚁穴格：巢室 ∪ 走廊 ∪ 主入口竖井。挖出即标记，受维护保护（免土壤回填）。"""
        if y < SURFACE:
            return False
        if self._in_nest_area(col, x, y):
            return True
        if col.shaft_x0 <= x <= col.shaft_x1 and y <= col.qy + 2:
            return True  # 主入口竖井（蚁穴的一部分）
        return False

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
        与卸粮用的 food_target（全群最空房、负载均衡）不同——吃饭是就近取食，避免全群饿蚁
        长途奔向同一间远房（实测 37% 蚁在赶路、进食几乎看不见）。"""
        best = None
        bd = 1 << 30
        for ch in col.food_rooms:
            if ch.get("store", 0) < 1 or not ch.get("linked"):
                continue
            if self._in_ellipse(x, y, ch):
                return ch
            d = abs(x - ch["cx"]) + abs(y - ch["cy"])
            if d < bd:
                bd, best = d, ch
        return best

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

    def find_path(self, sx: int, sy: int, tx: int, ty: int, col: Colony) -> list[tuple[int, int]] | None:
        """BFS 最短路（8 向，只走已挖通路）。返回 [(x,y)...] 起点→终点，无路径返回 None。"""
        if (sx, sy) == (tx, ty):
            return [(sx, sy)]
        if not self._path_cell_ok(tx, ty, col):
            return None
        start, goal = sy * W + sx, ty * W + tx
        prev = {start: -1}
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
                if ni in prev or not self._path_cell_ok(nx, ny, col):
                    continue
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
        # 地面随机掉落：偏落到群落(居中)觅食带内（阶段 8 单群落，缩短觅食往返）
        if rng.random() < FOOD_DROP_P:
            cx = W // 2
            x0 = max(6, min(W - 12, cx + rng.randrange(-FOOD_BAND, FOOD_BAND + 1)))
            w = rng.randrange(2, 4)
            for x in range(x0, min(W - 2, x0 + w)):
                food[W + x] = min(FOOD_CAP, food[W + x] + rng.randrange(FOOD_DROP_MIN, FOOD_DROP_MAX))
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
            # 阶段 7.5：产卵随群落发展加速（拓荒慢→鼎盛快）——人口越大产卵间隔越短（温和，避免爆量）
            lay_interval = max(50, 90 - col.population // 2)
            # 2026-10-09 软化产卵门："colony 可用粮" = 仓库存 + 工蚁身上在途的粮。
            # 旧版只认 store>=12：just-in-time 经济下仓库贴 0，门槛间歇关闭→卵管线断供→
            # 第一代集中老死无更替→全群灭绝（seed3 实测）。真实蚁后由工蚁直接喂食，不必等仓满。
            available = col.store + sum(a.food for a in col.ants if a.food)
            col.food_available = available  # 缓存本 tick 可用粮：builder 指派门也用（防 JIT 经济死锁）
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
            # 蚁巢扩张（无模板）：随种群逐个由 _dynamic_chamber_def 随机方向向外生成
            # 新房间（kind 按需求分配）。新房间自带待挖土 → chambers_dirt>0 → builder 继续挖。
            target = min(MAX_CHAMBERS, 4 + col.population // 25)
            added = 0
            while len(col.chambers) < target and added < 4:
                n = len(col.chambers)
                d = col.chamber_defs[n] if n < len(col.chamber_defs) else self._dynamic_chamber_def(col, n)
                if d is None:
                    break  # 本 tick 没找到不重叠的位置，下 tick 重抽
                ch_new = self._new_chamber(col, d, self.rng, core=(n < 4), kind=d[6])
                col.chambers.append(ch_new)
                self._pick_link(col, ch_new)  # 房-房互联捷径（成树不成环）
                self._clear_corridor_rocks(col, ch_new)  # 阶段9：新走廊带清岩，保挖穿可达
                self._recount_nest_dirt(col)
                added += 1
        for col in self.colonies:
            col.n_builders = sum(1 for a in col.ants if a.building)
            col.n_breakers = sum(1 for a in col.ants if a.breaking)
            col.n_maintainers = sum(1 for a in col.ants if a.maintaining)
            col.break_digs = self._break_dig_cells(col)  # 阶段9：突破前沿（无未连通房间时为空）
            # 阶段10：多房间均分——睡室选"占用最少"（休息蚁强制分散）、粮室选"最空"（食物强制分散）
            col.sleep_rooms = [ch for ch in col.chambers if ch.get("kind") == "sleep"]
            col.sleep_target = None
            if col.sleep_rooms:
                n = len(col.sleep_rooms)
                occ = [0] * n
                inbound = [0] * n
                for a in col.ants:
                    if 0 <= a.sleep_room < n:
                        inbound[a.sleep_room] += 1
                    for j, ch in enumerate(col.sleep_rooms):
                        rx, ry = ch["rx"], ch["ry"]
                        if (a.x - ch["cx"]) ** 2 / (rx * rx) + (a.y - ch["cy"]) ** 2 / (ry * ry) <= 1:
                            occ[j] += 1
                # 阶段12：在途认领一并计入负载——蚂蚁常在房界**外**歇脚（占用统计漏计），
                # 只按 occ 选房会让同一间永远"最空"→持续流入→入口带堆大群（用户所见"全卡死一处"）
                col.sleep_occ = occ
                col.sleep_inbound = inbound
                col.sleep_target = col.sleep_rooms[occ.index(min(occ))]
                # 睡室必须挖出可用面积才许当休息目标：未挖的睡室是土椭圆，拽过去=罢工（2026-10-10 开井期全灭根因）
                col.sleep_ready = self.room_capacity(col, col.sleep_target) >= 20
            else:
                col.sleep_occ = []
                col.sleep_inbound = []
                col.sleep_ready = False
            col.brood_room = next((ch for ch in col.chambers if ch.get("kind") == "brood"), None)
            col.food_rooms = [ch for ch in col.chambers if ch.get("kind") == "food"]
            # 阶段13：加迟滞防"最空房"逐 tick 来回翻——载体卸粮抬升当前房占比→目标抖动→
            # 全体载体路线逐 tick 重算（BFS 成本爆炸，实测 10k 处 81ms/tick）。仅当别的房"明显更空"
            # （占比低 0.05 以上）才切换；均分意图不变（房装满自然轮到下一间）。
            if col.food_rooms:

                def _full(ch):
                    return ch["store"] / max(1.0, self.room_capacity(col, ch) * FOOD_PER_CELL)

                _cur = col.food_target
                if _cur is None or _cur not in col.food_rooms:
                    col.food_target = min(col.food_rooms, key=_full)
                else:
                    _best = min(col.food_rooms, key=_full)
                    if _best is not _cur and _full(_best) < _full(_cur) - 0.05:
                        col.food_target = _best
            else:
                col.food_target = None
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
        """多出入口：巢长大后在侧面生成待挖入口列，打通地表即 open。"""
        t = self.terrain
        for col in self.colonies:
            if not col.surface_open:
                continue
            want = 0  # 只做单主入口：额外侧入口离蚁后 10-30 格太远→乱挖，改围绕蚁后的紧凑蚁穴
            if len(col.entrances) < want:
                ex = None
                for _ in range(8):
                    cand = col.qx + self.rng.choice((-1, 1)) * self.rng.randrange(10, 31)
                    if not (4 <= cand <= W - 5):
                        continue
                    if any(abs(cand - e["x"]) < 8 for e in col.entrances):
                        continue
                    if any(abs(cand - o.qx) < 20 for o in self.colonies if o is not col):
                        continue
                    if any(t[y * W + cand] == ROCK for y in range(SURFACE, col.qy)):
                        continue  # 整列避开岩体
                    ex = cand
                    break
                if ex is not None:
                    col.entrances.append({"x": ex, "open": False})
            for e in col.entrances:
                if not e["open"] and t[SURFACE * W + e["x"]] == TUNNEL:
                    e["open"] = True
                # 口部会随竖井拓宽漂移、原口格可能被回填：标记指向死格时挪到竖井带内仍通的口（封堵中不挪）
                if e["open"] and t[SURFACE * W + e["x"]] != TUNNEL and SURFACE * W + e["x"] not in col.sealed:
                    for xx in sorted(range(col.shaft_x0 - 1, col.shaft_x1 + 2), key=lambda v: abs(v - e["x"])):
                        if t[SURFACE * W + xx] == TUNNEL:
                            e["x"] = xx
                            break

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
        # 休息（写实碎片化打盹）：干累了(疲劳达标)→在巢内**原地**歇脚。
        # 2026-10-10 修复"疲劳蚁大群无规律徘徊"：旧逻辑要求人**站在睡室内**才许歇，
        # 但睡室在走廊迷宫里靠直线梯度导航不可靠→seed5@80k 实测 78% 蚁疲劳拉满、仅 3% 在歇，
        # 其余带满疲劳空转（用户所见"大群徘徊"）。真实蚂蚁本就在巢内到处打盹，不必挤一间睡室。
        # 改为：地下累了即原地歇（恢复疲劳）；睡室降级为**回血加成区**（at_home 时额外被动回血，
        # 见下），仍值得去、但去不到也能歇→导航不再是休息的硬前提，彻底解除迷宫依赖。
        at_home = col.in_sleep_room(a.x, a.y)
        # 2026-10-10 修复"竖井大群徘徊"：主厅+主竖井是全巢唯一交通枢纽（上下行/各走廊都过这里），
        # 原地打盹若允许在此歇脚，累蚁会堆积在竖井口（实测 seed5@80k 23/32 休息蚁全堵在主厅顶 y=100）
        # → 堵死唯一上下通道、蚁群排队。真实蚂蚁在安静的角落歇、不堵在繁忙的中心。
        # 故枢纽区（主厅+竖井）禁止歇脚：累蚁在此继续走、出了枢纽再原地歇。
        in_hub = abs(a.x - col.qx) <= 5 and SURFACE - 1 <= a.y <= col.qy + 6
        if (
            not a.resting
            and a.egg is None
            and not (a.state == CARRY and a.food)
            and a.fatigue >= FATIGUE_MAX
            and a.y >= SURFACE  # 在巢内（地下）即许原地歇脚
            and not in_hub  # 枢纽区（主厅+竖井）不许歇脚，防堵唯一上下通道
            and occ.get((a.x, a.y), 0) < REST_CROWD_MAX  # 本格已挤→不歇，走到安静格再歇（防同格堆叠）
        ):
            a.resting = True
            if a.sleep_room >= 0:
                a.sleep_room = -1  # 已就地歇脚，不再需要睡室（在途计数由下 tick 重算自动冲销）
        if a.resting:
            a.fatigue = max(0.0, a.fatigue - REST_RECOVER * a.pace)  # *pace：个体节律→恢复快慢各异→去同步
            if at_home:
                a.energy = min(150.0, a.energy + REST_REGEN)  # 睡室内休息：额外被动回血
            if a.fatigue <= REST_MIN_FAT:
                a.resting = False  # 歇够了→复工
        else:
            a.fatigue = min(FATIGUE_MAX, a.fatigue + FATIGUE_RATE * a.pace)  # 干活→累（*pace：累快慢各异）
        a.energy -= 0.03 * a.size  # *size：体型各异→能耗不同→大蚁更勤觅食（均值 1.0 不变，不扰经济）
        # 阶段14：饥饿节律——干活/休息都按各自 pace 累积饥饿，到阈值回粮室吃饭（见下方 eating 块）。
        a.hunger += HUNGER_RATE * a.pace
        # 回退领食：尚无粮室时（开局/粮室未挖）就地取过渡粮（start_store），否则开局全饿死。
        if (
            a.hunger >= HUNGER_MAX
            and not a.eating
            and col.food_target is None
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
        er = (
            self._eat_room(col, a.x, a.y)
            if (a.hunger >= HUNGER_MAX and a.state == SEEK and not a.maintaining
                and a.egg is None and a.y >= SURFACE)  # 巢内（地下）才吃粮室
            else None
        )
        if (
            er is not None
            and self._in_ellipse(a.x, a.y, er)  # 已走到（最近/所在的）粮室内
            and occ.get((a.x, a.y), 0) < EAT_CROWD_MAX  # 本格已挤→不在此开吃，先走开（防进食堆叠）
        ):
            self._take_food(col, EAT_FOOD)  # 吃 1 份粮
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
            # 开井期需多 builder 并行挖出 5 宽主通道；通地表后回到小比例扩建巢室
            build_cap = (
                max(4, col.population // 2)
                if not col.surface_open
                # 阶段7.4：幼体=卵+幼虫+蛹（越多越需要扩建巢室腾空间）
                else max(1, col.population // 4 + len(col.eggs) + len(col.larvae) + len(col.pupae))
            )
            # 人口过多→主动扩巢：拥挤度(pop/巢格)越高→越多蚂蚁转去扩建巢室（挖大巢缓解拥挤）
            # 配合放大的巢室上限，大群→房间继续长→巢变大→拥挤缓解（挖掘蚁穴的必要性）
            if col.surface_open and col.nest_size and col.population / col.nest_size > 0.72:
                build_cap += col.population // 8
            # 分工承诺：一旦认领挖掘就一直挖到底——饿了从巢穴领食、吃完接着挖，
            # 只在巢真正完工（work_left 为假：竖井通 + 巢室全挖空）时才放下；
            # 不再因缺粮中途撤退→反复启停（旧逻辑把巢挖成大洞而非通道/房间）
            if a.building and not work_left:
                a.building = False
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
                and col.n_builders < build_cap
            ):
                a.building = True
                col.n_builders += 1
            # 挖穿（break-through）：单独行为——builder 中专选少量"突破蚁"打通未连通房间的蜿蜒走廊+房间，
            # 普通 builder 不碰这些新土（不能挖穿墙）；突破蚁单独限额（≤3），房间打通后自动转普通建造
            break_tgt = self._break_target(col)
            if a.breaking and (break_tgt is None or not a.building):
                a.breaking = False
            if a.building and break_tgt is not None and not a.breaking and col.n_breakers < 3:
                a.breaking = True
                col.n_breakers += 1
            # 维护蚁：巢成型且群够大才常驻少量（规模随种群：小群不派，保觅食；大群占比小）
            maint_cap = min(MAINTAINER_MAX, max(0, (col.population - 15) // 15))
            if a.maintaining and (
                not col.surface_open or not col.nest_cells or col.n_maintainers > maint_cap
            ):
                a.maintaining = False
            if (
                a.egg is None
                and not a.maintaining
                and not a.building
                and not a.resting
                and col.surface_open
                and col.nest_size >= NEST_TARGET
                and col.n_maintainers < maint_cap
                and a.state == SEEK
                and a.food == 0
            ):
                a.maintaining = True
                col.n_maintainers += 1
            # 阶段11：工蚁就近捡拾散卵（蚁后下在主厅）→ 搬运到育虫室
            if (
                a.egg is None
                and a.food == 0
                and not a.resting
                and not a.building
                and not a.breaking
                and not a.maintaining
                and a.y >= SURFACE
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
        pending = next((e for e in col.entrances if not e["open"]), None) if nesting else None
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
            and a.fatigue >= FATIGUE_MAX
            and col.sleep_ready  # 睡室已挖出可用面积（未挖=土椭圆，拽过去=罢工）
            and a.y >= SURFACE
            and (abs(a.x - col.qx) + abs(a.y - col.qy)) <= 90  # 覆盖最外围动态睡室（房距 ≤76+房半径）
        ):
            # 阶段12：每蚁各认领一间睡室（"占用+在途"最少者，距离破平）→同批累倒的蚁摊到多间。
            # 旧全局 sleep_target 单间：休息蚁常在房界外歇脚→occ 漏计→该房永远"最空"→
            # 持续流入→入口带堆 ~50 只（用户所见"全卡死一处"）。在途即时计数→同 tick 后续蚁换房。
            n = len(col.sleep_rooms)
            if n and (a.sleep_room < 0 or a.sleep_room >= n):
                r = min(
                    range(n),
                    key=lambda j: (
                        col.sleep_occ[j] + col.sleep_inbound[j],
                        abs(a.x - col.sleep_rooms[j]["cx"]) + abs(a.y - col.sleep_rooms[j]["cy"]),
                    ),
                )
                a.sleep_room = r
                col.sleep_inbound[r] += 1
            st = col.sleep_rooms[a.sleep_room]
        # 阶段13：当前"预定路线"的终点（BFS 目标）。BFS 自带迷宫解→CARRY 直接定粮室
        # （旧两段 hub 逻辑是直线梯度时代的补丁，已删）。SEEK 地下段终点=地表入口格
        # (qx, SURFACE-1)：到 (qx,3) 就路径耗尽上不了地表，须把终点放到地表首格，
        # 蚁踏上地表后 y<SURFACE → tp 自动切最近食物簇。
        tp = None
        if st is not None:
            tp = (st["cx"], st["cy"])
        elif a.egg is not None and not a.resting:
            if a.y < SURFACE:
                tp = (col.qx, SURFACE)
            elif col.brood_room is not None:
                tp = (col.brood_room["cx"], col.brood_room["cy"])
        elif a.state == CARRY and not a.resting:
            ft = col.food_target
            if a.y < SURFACE:
                tp = (col.qx, SURFACE)
            else:
                tp = (ft["cx"], ft["cy"]) if ft is not None else (col.qx, col.qy)
        elif a.state == SEEK and not a.maintaining and not a.resting and a.egg is None:
            er = self._eat_room(col, a.x, a.y) if a.hunger >= HUNGER_MAX else None
            if er is not None:
                # 阶段14：饿了→去（就近的）粮室吃饭（地上先入巢，地下直奔粮室）；到粮室后由上方 eating 块开吃
                tp = (col.qx, SURFACE) if a.y < SURFACE else (er["cx"], er["cy"])
            elif a.y < SURFACE and target is not None:
                tp = (target[0], target[1])
            else:
                tp = (col.qx, SURFACE - 1)  # 地下觅食蚁：汇主竖井→上地表入口格
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
                continue
            if t == DIRT:
                # 行为分类：挖掘（开井/巢室/入口）是 builder 专职，维护蚁专职修复蚁穴格，其余只走隧道
                if a.maintaining:
                    if not self.nest_mark[i]:  # 维护蚁只修复标记的蚁穴格
                        continue
                elif not nesting:
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
                    # 开井期：只在窄主通道列内向上挖 → 竖井保持窄，不扩散
                    if not (col.shaft_x0 <= nx <= col.shaft_x1):
                        continue
                elif a.breaking:
                    # 挖穿（单独行为）：突破蚁只挖"通向未连通房间的蜿蜒走廊 + 该房间"，绝不挖穿其它墙
                    if not self._in_break_area(col, nx, ny):
                        continue
                elif not a.maintaining:
                    # 普通建造：挖已连通巢区（主厅/已连通房间/它们的走廊）；
                    # 探路蚁（1/10 builder，eat_offset 种子稳定）可从不限于房间的任意隧道格开挖
                    # （ny>SURFACE：只在地底，防随机开出新的地表洞口）→ 隧道网自由生长
                    if not self._in_connected_nest(col, nx, ny) and not (
                        a.eat_offset % EXPLORER_MOD == 0 and ny > SURFACE
                        and self.web_count < WEB_CAP
                        # 探路蚁只准延伸自己的 1 宽须：tn==1（恰好 1 个隧道邻）。
                        # 缝格/交叉格(tn≥2)禁挖——否则走廊之间的土被逐步吃穿、整巢融成大方形洞
                        # （用户"方形不符合自然规律"）。须会在已有隧道 1 格外自然停住（留薄土墙），
                        # 探索网 = 不交叉的须状死胡同 + WEB_CAP 封顶 + WEB_RADIUS 限巢周 + 废弃被回收。
                        and tn == 1
                        and abs(nx - col.qx) + abs(ny - col.qy) <= WEB_RADIUS
                    ):
                        continue
            # 直行惯性：强偏好当前方向 + 小随机扰动 → 转弯平滑缓慢、动作连贯不抽搐
            score = rng.random() * 0.3 + (0.6 if dd == prev else 0.0)
            if nesting:
                # 奇形怪状蚁穴：挖掘蚁叠加"每蚁个性震荡"——按 uid 派生不同相位/频率的正弦，
                # 左/右转向得分随正弦交替 → 边挖边左右摇摆，走出 S 形/波浪/歪扭走廊。
                # 振幅 1.4 > 直行惯性 0.6：摇摆主导直行，形状夸张；房间填充仍由 CHAMBER_BIAS 3.0 主导（房还是房，路变歪）
                osc = math.sin(self.tick_count * (0.12 + (a.eat_offset % 5) * 0.05) + (a.max_age % 97) * 0.13)
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
                    score += CHAMBER_BIAS  # 强引导：优先填定义好的巢室（成房间，非乱爬）
                elif nesting and not col.surface_open and col.shaft_x0 - 1 <= nx <= col.shaft_x1 + 1:
                    score += SHAFT_BIAS  # 开井期：优先挖主通道列内，保走廊不扩散
            crowd = occ.get((nx, ny), 0)
            if crowd:
                score -= CROWD_P * crowd  # 拥挤：别往堆满同类的格子里挤
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
                if pending is not None:
                    # 入口施工：向待挖入口列掘进，打通地表
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
                        if a.eat_offset % EXPLORER_MOD != 0:
                            md = self._most_dirt_chamber(col)
                            if md is not None:
                                score -= (abs(nx - md[0]) + abs(ny - md[1])) * 0.9
                        else:
                            # 探路蚁不拉向房间：自由游走 + 弱回巢心（防漂到地图边缘，离巢越远拉力越大）
                            score -= (abs(nx - col.qx) + abs(ny - col.qy)) * 0.03
            else:
                score += self.use[i] * PATH_BIAS  # 走廊信息素：越被踩熟越优先→汇流主通道，不另开新洞
                if a.state == SEEK and a.y >= SURFACE and not a.maintaining and not a.resting and a.egg is None:
                    # 地下觅食蚁强向上（须>直行惯性0.6，否则宽腔内横向惯性把蚁困住、爬不上竖井；休息蚁除外）
                    score -= DIRS[d][1] * 1.0
                    # 无模板后走廊网变大：向上被土挡住时先横向汇到主通道列再爬升（否则在主厅周边游荡一整天）
                    score -= abs(nx - col.qx) * 0.2
                if a.state == CARRY and not a.resting:
                    # 阶段10：搬运蚁走向目标粮室（最空那间→食物多间均分）；无粮室时回退蚁后处。
                    # 地表上先回主入口竖井口（唯一上下通道）——直瞄地下粮室会让蚁走到粮室 x 坐标的地表后下不去
                    # 2026-10-10 修复：旧 0.3/0.25 < 直行惯性 0.6，搬运蚁转向不了竖井/粮室走廊。
                    # 旧代码靠"食物恰掉在入口口"侥幸成立（回巢只有 5 格）；觅食改主动导航后
                    # 拾取点在 70 格外的食物簇，回巢 50-140 格→搬运蚁走到地图边缘才被迫折返
                    # （实测 drop 5/万 vs pick 20/万，15 只载体迷路饿死在回巢路上，store 永远 0）。
                    # 0.8 > 0.6：沿已有隧道直线汇回竖井口→粮室。
                    ft = col.food_target
                    if a.y < SURFACE:
                        tx, ty = (col.qx, SURFACE)
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
                            tx, ty = (col.qx, SURFACE)
                        else:
                            tx, ty = (br["cx"], br["cy"])
                        score -= (abs(nx - tx) + abs(ny - ty)) * 0.9
            if a.maintaining:
                # 维护蚁：走向塌陷的蚁穴格修复；无塌陷则徘徊蚁穴中枢附近（定期巡查）
                if col.collapsed:
                    ci = col.collapsed[rng.randrange(len(col.collapsed))]
                    score -= (abs(nx - (ci % W)) + abs(ny - (ci // W))) * 0.6
                else:
                    score -= (abs(nx - col.qx) + abs(ny - col.qy)) * 0.3
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
        # 挖掘蚁（nesting，要挖新土、无预设通路）与巡逻的维护蚁保持打分兜底。
        # 路径须同时满足：目标未变、蚁还在路径上、下一格仍可走——否则从当前位置重算
        # （塌陷封路/目标切换/角色切换自动自愈）。
        if not nesting and not a.maintaining and tp is not None:
            if (a.x, a.y) == tp:
                # 已站在目标上：路径耗尽，无需 BFS（否则每 tick 白算一条平凡路径）；
                # 清掉旧路径，靠打分兜底就地行为（拾粮/卸粮后 tp 变化再重新规划）
                a.path = None
                a.path_i = 0
                a.path_target = None
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
        a.d = best_d
        nx, ny = best_i % W, best_i // W
        if self.terrain[best_i] == DIRT:
            if rng.random() >= DIG_P[0 if a.role == WORKER else 1]:
                return  # 还在挖这一格
        elif rng.random() >= WALK_P:
            return  # 这 tick 没迈开步
        if a.maintaining:
            src = 5  # 维护蚁修复
        elif a.building:
            src = 4 if a.breaking else (3 if a.eat_offset % EXPLORER_MOD == 0 else 2)
        else:
            src = 0  # 走已有隧道，_dig 不会改写
        self._dig(nx, ny, src)
        a.x, a.y = nx, ny
        # 路线推进：确实走到路径下一格才前进（WALK_P 没触发时不跳格）
        if a.path is not None and a.path_i + 1 < len(a.path) and a.path[a.path_i + 1] == (a.x, a.y):
            a.path_i += 1
        if a.egg is not None:
            a.egg["x"], a.egg["y"] = nx, ny  # 卵随工蚁移动
        occ[(nx, ny)] = occ.get((nx, ny), 0) + 1  # 实时占用：同 tick 内后来者避开
        if ny < SURFACE and not col.surface_open:
            col.entrances.append({"x": a.x, "open": True})  # 主入口
        if ny < SURFACE:
            col.surface_open = True
        if a.state == CARRY:
            self.food_p[best_i] = 255
        if a.state == SEEK and food[best_i] > 0 and not a.maintaining and a.egg is None:
            take = min(round(14 * a.carry), food[best_i])  # *carry：个体搬运量各异（round 保 int 供 bytearray；均值≈14 吞吐稳）
            food[best_i] -= take
            a.food = take
            a.state = CARRY
        elif a.state == CARRY and (
            col.home_cell(nx, ny)
            or (col.food_target is not None and self._in_ellipse(nx, ny, col.food_target))
        ):
            col.food_gained += a.food
            # 阶段10：食物入目标粮室（最空那间→多间均分）；食物有体积，房容量=隧道格×FOOD_PER_CELL，满则弃多余
            ft = col.food_target
            if ft is not None and self.room_capacity(col, ft) > 0:
                give = min(a.food, max(0, self.room_capacity(col, ft) * FOOD_PER_CELL - ft["store"]))
                ft["store"] += give  # 食物入房即计入 col.store（=各房之和）
            a.food = 0
            a.energy = min(150.0, a.energy + 50)
            a.state = SEEK  # 2026-10-10：兵蚁回巢卸粮后继续觅食（同初始化，不再 WANDER 游荡耗能量）
        elif (
            a.egg is not None
            and col.brood_room is not None
            and self._in_ellipse(a.x, a.y, col.brood_room)
        ):
            a.egg["moved"] = True  # 阶段11：卵存入育虫室，停止搬运
            a.egg = None

    def frame(self) -> dict:
        ants = [[a.uid, a.x, a.y, a.colony, a.state, a.role, a.d, a.maintaining, a.resting, a.size, 1 if a.eating else 0] for c in self.colonies for a in c.ants]
        # 阶段7.4：三阶段幼体，带阶段标记（0=卵 1=幼虫 2=蛹）供前端分色渲染
        larvae = (
            [[e["x"], e["y"], 0] for c in self.colonies for e in c.eggs]
            + [[l["x"], l["y"], 1] for c in self.colonies for l in c.larvae]
            + [[p["x"], p["y"], 2] for c in self.colonies for p in c.pupae]
        )
        q = [v for c in self.colonies for v in (c.qx, c.qy, 1 if c.queen_resting else 0)]
        s = [v for c in self.colonies for v in (c.population, int(c.store), len(c.eggs) + len(c.larvae) + len(c.pupae), int(c.food_gained))]
        fc = []
        for c in self.colonies:
            for ch in c.food_rooms:  # 阶段10：多间储粮室，各按自己存粮渲染（食物分散可见）
                amt = int(ch["store"])
                if amt < 2:
                    continue
                rx, ry = ch["rx"], ch["ry"]

                def in_room(x, y):
                    return (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1

                # 食物散在房壁边缘：取内周圈格子，按稳定哈希排序（每帧不闪）
                key = (ch["cx"] * 73856093) ^ (ch["cy"] * 19349663)
                edge = [
                    (x, y)
                    for y in range(ch["cy"] - ry, ch["cy"] + ry + 1)
                    for x in range(ch["cx"] - rx, ch["cx"] + rx + 1)
                    if in_room(x, y)
                    and self.terrain[y * W + x] == TUNNEL
                    and not (in_room(x - 1, y) and in_room(x + 1, y) and in_room(x, y - 1) and in_room(x, y + 1))
                ]
                edge.sort(key=lambda p: (p[0] * 73856093) ^ (p[1] * 19349663) ^ key)
                cells = edge[:16]
                if cells:
                    for x, y in cells:
                        fc.append([x, y, max(1, amt // len(cells))])
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
            "sr": [
                [ch["cx"], ch["cy"], ch["rx"], ch["ry"]] for ch in self.colonies[0].sleep_rooms
            ]
            if self.colonies and self.colonies[0].sleep_rooms
            else [],
        }

    def terrain_str(self) -> str:
        return _b64(self.terrain)

