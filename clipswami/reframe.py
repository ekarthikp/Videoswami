"""Subject-tracking reframe: follow the action when cropping to a new aspect ratio.

Works on tiny grayscale frames decoded by ffmpeg, in pure Python. Motion between
frames marks where the subject is; a damped virtual camera follows it so the crop
pans calmly instead of jittering, and snaps instantly on hard cuts.
"""

from __future__ import annotations

from statistics import median

from . import ff

GRID_W, GRID_H = 64, 36
TRACK_FPS = 5
_NOISE = 10            # per-pixel difference ignored as sensor noise / compression
_CUT_LEVEL = 28.0      # mean absolute frame difference that signals a hard cut
_MIN_MOTION = GRID_W * GRID_H * 0.15


def _detail_center(frame: bytes):
    """Where the visual detail sits in a still frame - a stand-in subject when nothing moves."""
    cols = [0] * GRID_W
    rows = [0] * GRID_H
    for y in range(GRID_H):
        base = y * GRID_W
        acc = 0
        for x in range(1, GRID_W - 1):
            d = frame[base + x + 1] - frame[base + x - 1]
            if d < 0:
                d = -d
            if d > 24:
                cols[x] += d
                acc += d
        rows[y] = acc
    weight = sum(cols)
    if weight < GRID_W * GRID_H * 0.4:
        return None
    cx = sum((x + 0.5) * v for x, v in enumerate(cols)) / weight / GRID_W
    cy = sum((y + 0.5) * v for y, v in enumerate(rows)) / weight / GRID_H
    return cx, cy


def motion_centers(path: str, start: float, duration: float) -> tuple[list, list, list]:
    """Return (times, centers, cut_times). centers[i] is (x, y) in 0..1, or None if still."""
    raw = ff.run([
        ff.need("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", path, "-an", "-sn",
        "-vf", f"fps={TRACK_FPS},scale={GRID_W}:{GRID_H},format=gray",
        "-f", "rawvideo", "-",
    ], binary=True).stdout
    size = GRID_W * GRID_H
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    times, centers, cuts = [], [], []
    recent: list[tuple[list[int], list[int]]] = []          # rolling ~1s of motion profiles
    for index in range(1, len(frames)):
        prev, cur = frames[index - 1], frames[index]
        t = index / TRACK_FPS
        cols = [0] * GRID_W
        rows = [0] * GRID_H
        total = 0
        for y in range(GRID_H):
            base = y * GRID_W
            row_sum = 0
            for x in range(GRID_W):
                d = cur[base + x] - prev[base + x]
                if d < 0:
                    d = -d
                total += d
                if d > _NOISE:
                    d -= _NOISE
                    cols[x] += d
                    row_sum += d
            rows[y] = row_sum
        times.append(t)
        if total / size > _CUT_LEVEL:                        # hard cut: forget the old shot
            cuts.append(t)
            recent.clear()
            centers.append(None)
            continue
        recent.append((cols, rows))
        if len(recent) > TRACK_FPS:
            recent.pop(0)
        col_acc = [sum(r[0][x] for r in recent) for x in range(GRID_W)]
        row_acc = [sum(r[1][y] for r in recent) for y in range(GRID_H)]
        weight = sum(col_acc)
        if weight < _MIN_MOTION:
            centers.append(_detail_center(cur))             # still shot: aim at the detail
        else:
            cx = sum((x + 0.5) * v for x, v in enumerate(col_acc)) / weight / GRID_W
            cy = sum((y + 0.5) * v for y, v in enumerate(row_acc)) / max(sum(row_acc), 1) / GRID_H
            centers.append((cx, cy))
    return times, centers, cuts


def camera_path(times: list[float], targets: list, cuts: list[float],
                deadzone: float = 0.08, max_speed: float = 0.35, ease: float = 0.25) -> list:
    """Damped follow of `targets` (0..1 or None). Returns [(t, pos)] keyframes."""
    if not times:
        return [(0.0, 0.5)]
    step = times[1] - times[0] if len(times) > 1 else 1.0 / TRACK_FPS
    cut_set = set(cuts)

    def settle(i: int) -> float:
        """Where a new shot should open: the median target over its first ~1.5s."""
        ahead = []
        for j in range(i, min(len(times), i + int(1.5 / step) + 1)):
            if j > i and times[j] in cut_set:
                break
            if targets[j] is not None:
                ahead.append(targets[j])
        return median(ahead) if ahead else 0.5

    pos = settle(0)
    path = [(0.0, pos)]
    for i, t in enumerate(times):
        if t in cut_set:
            new_pos = settle(i)
            path.append((max(t - 0.001, 0.0), pos))          # hold, then jump on the cut
            pos = new_pos
            path.append((t, pos))
            continue
        target = targets[i]
        if target is not None and abs(target - pos) > deadzone:
            move = (target - pos) * ease
            limit = max_speed * step
            pos += max(-limit, min(limit, move))
            pos = max(0.0, min(1.0, pos))
        path.append((t, pos))
    return path


def simplify(points: list[tuple[float, float]], tolerance: float, limit: int = 60) -> list:
    """Drop keyframes that linear interpolation reproduces within `tolerance`."""
    if len(points) <= 2:
        return points
    while True:
        kept = [points[0]]
        anchor = 0
        for i in range(1, len(points) - 1):
            (t0, v0), (t2, v2) = points[anchor], points[i + 1]
            ok = True
            for j in range(anchor + 1, i + 1):
                tj, vj = points[j]
                guess = v0 + (v2 - v0) * (tj - t0) / (t2 - t0) if t2 > t0 else v0
                if abs(guess - vj) > tolerance:
                    ok = False
                    break
            if not ok:
                kept.append(points[i])
                anchor = i
        kept.append(points[-1])
        if len(kept) <= limit:
            return kept
        tolerance *= 1.6


def expression(points: list[tuple[float, float]]) -> str:
    """An ffmpeg expression in `t` that linearly interpolates the keyframes."""
    if len(points) == 1 or all(abs(p[1] - points[0][1]) < 0.5 for p in points):
        return f"{points[0][1]:.0f}"
    expr = f"{points[-1][1]:.0f}"
    for i in range(len(points) - 2, -1, -1):
        (t0, v0), (t1, v1) = points[i], points[i + 1]
        if abs(v1 - v0) < 0.5 or t1 - t0 < 1e-4:
            seg = f"{v0:.0f}"
        else:
            seg = f"lerp({v0:.0f},{v1:.0f},(t-{t0:.3f})/{t1 - t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return expr


def plan_crop(path: str, start: float, duration: float, src_w: int, src_h: int,
              crop_w: int, crop_h: int, track: bool = True) -> tuple[str, str]:
    """Return (x_expr, y_expr) for a crop of crop_w x crop_h inside the source frame."""
    free_x, free_y = src_w - crop_w, src_h - crop_h
    center_x, center_y = f"{free_x // 2}", f"{int(free_y * 0.4)}"   # bias up: faces sit high
    if not track or (free_x <= 1 and free_y <= 1):
        return center_x, center_y
    try:
        times, centers, cuts = motion_centers(path, start, duration)
    except ff.ClipError:
        return center_x, center_y
    if not times:
        return center_x, center_y

    def axis(index: int, src: int, crop: int, free: int) -> str:
        targets = [c[index] if c else None for c in centers]
        keys = camera_path(times, targets, cuts)
        pixels = [(t, max(0.0, min(float(free), pos * src - crop / 2))) for t, pos in keys]
        return expression(simplify(pixels, tolerance=max(2.0, src / 500)))

    x_expr = axis(0, src_w, crop_w, free_x) if free_x > 1 else "0"
    y_expr = axis(1, src_h, crop_h, free_y) if free_y > 1 else "0"
    return x_expr, y_expr
