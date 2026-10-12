# AGENTS.md

蚁群生态系统模拟器。纯 Python 3.12，**零依赖**（仅标准库：`http.server`、`random`、`threading`）。无构建步骤，无 lint/test/formatter 工具——下面的 smoke 检查是唯一验证门禁。

## 命令

```bash
uv sync
uv run myrmecopolis                 # 实时服务器 → http://127.0.0.1:8000  （参数：--port、--seed，默认种子 5）
uv run myrmecopolis --smoke         # 无头回归：3 个世界带断言（最后一个世界 50k tick，需几分钟）
uv run tools/render_nest.py         # 15 万 tick → 当前目录输出 nest_preview.png
```

任何对 `src/myrmecopolis/` 的改动后，`--smoke` 必须通过。

## 布局

- `src/myrmecopolis/constants.py` — **所有可调参数都在这里**；调参 = 只改这一个文件
- `src/myrmecopolis/world.py` — 约 1200 行编排器：地形、食物、挖掘门控、生命周期、阶级、SSE 帧序列化
- `src/myrmecopolis/ant.py`、`colony.py` — 蚂蚁数据、群落状态
- `src/myrmecopolis/smoke.py` — 回归断言；`web.py` — 标准库 HTTP + SSE；`sim.py` — 再导出垫片（导入走 `from .sim import World`）
- `static/` — `index.html`（主画布视图）、`gallery.html`（图鉴）、`sprites.html`、`ants.js`（共享精灵）

## 不变量（破坏后静默失效）

- **确定性**：一个种子完全决定群落。行为数学只能用开局按种子派生的每蚁量（`eat_offset`、`max_age`、初始疲劳）。`Ant.uid` / `id()` 是跨世界身份计数器——绝不能用于行为数学（会导致不同进程/世界间结果分叉）。
- **RNG 调用顺序敏感**：重排 `random` 调用会改变全部行为。改动后用 `--smoke` + 固定种子无头长跑验证。
- **`col.store` 是只读属性**（`start_store` + Σ 粮室 `ch["store"]`）。绝不能直接赋值；所有耗粮必须走 `_take_food`，保证各房账目一致（历史 bug：只写房不写 store → builder 招募停摆 → 群落崩溃）。
- **smoke.py 的种子和 tick 数是刻意选的**（固定种子 5/3、`max(ticks, 8000)`、W3 = 50000t）：为避免生态方差和"走廊施工中"（unlinked）瞬时态误报。不要为了加速而缩短。
- **移动评分**（`world.py`）是候选格打分（无寻路）。拉力系数是相对 0.6 直行惯性调出来的（必须大于它）——改动会改变全群落行为，改完必须重跑 smoke。

## 帧协议

`/stream` 以 30 fps 发 SSE JSON；地形带版本号——`v` 变化时客户端重新拉取 `/terrain`。食物/地形以**原始 bytearray 的 base64**（`_b64`）传输，客户端解码。若改了 `world.py` 的 `frame()`，`static/index.html` 必须同步更新。

## 文档

`findings.md`（中文）记录设计决策与根因历史；`progress.md` 记录验证历史。两者都是本地工作文档（已 gitignore），但是"为什么是这样"的权威来源——重新设计任何子系统前先查它们。注意：`findings.md` 里的协议草图部分已过时，以代码为准。
