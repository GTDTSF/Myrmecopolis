import sys
import struct
import zlib

sys.path.insert(0, "src")
from myrmecopolis.world import World
from myrmecopolis.constants import TUNNEL, DIRT, SURFACE, W, H

T = 150000
w = World(seed=5)
for _ in range(T):
    w.tick()

t = w.terrain
col = w.colonies[0]
print(f"t={w.tick_count} pop={len(col.ants)+1} rooms={len(col.chambers)} "
      f"web={w.web_count} store={col.store:.0f}")


def in_chamber(x, y):
    for ch in col.chambers:
        rx, ry = ch["rx"], ch["ry"]
        if (x - ch["cx"]) ** 2 / (rx * rx) + (y - ch["cy"]) ** 2 / (ry * ry) <= 1:
            return ch
    return None


SKY = (74, 128, 180)
DIRT = (44, 32, 20)
DIRT_ROOM = (60, 45, 28)
ROOM = {"main": (214, 178, 104), "brood": (205, 132, 152), "food": (232, 200, 84),
        "sleep": (138, 168, 208), "queen": (214, 178, 104)}
CORR = (158, 128, 84)
WEB = (88, 84, 76)
QUEEN = (255, 220, 60)
ENTRY = (240, 240, 240)

rows = []
for y in range(H):
    row = []
    for x in range(W):
        i = y * W + x
        if y < SURFACE:
            row.append(SKY)
            continue
        ch = in_chamber(x, y)
        if t[i] == TUNNEL:
            if ch is not None:
                row.append(ROOM.get(ch.get("kind"), ROOM["main"]))
            elif w.nest_mark[i]:
                row.append(CORR)
            else:
                row.append(WEB)
        else:
            row.append(DIRT_ROOM if ch is not None else DIRT)
    rows.append(row)

for cx, cy in ((col.qx, col.qy),):
    if 0 <= cy < H:
        rows[cy][cx] = QUEEN
ey = SURFACE
if 0 <= ey < H:
    rows[ey - 1][col.qx] = ENTRY
    rows[ey][col.qx] = ENTRY


def write_png(path, scale):
    out = []
    for row in rows:
        ext = []
        for c in row:
            ext.extend([c] * scale)
        out.append(ext)
    raw = b""
    for row in out:
        line = b"\x00" + b"".join(bytes(c) for c in row)
        raw += line * scale  # 垂直也放大 scale 倍（否则格子横向 4px 纵向 1px，图被压扁）

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", len(out[0]), len(out) * scale, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# 2026-10-10 分辨率 4→8（用户"大幅增加分辨率"）：960×600 → 1920×1200
write_png("nest_preview.png", 8)
print("wrote nest_preview.png", W * 8, "x", H * 8)
