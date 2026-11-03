"""Pure laser quantization and edge-based paths with incoming-segment power."""

import math

import numpy as np

from .PhotoCarveModel import MAX_POINTS, PhotoCarveError, mode_frame


def power_grid(gray, params, check_abort=None):
    power = np.asarray(1 - gray, dtype=np.float32)
    if params.strategy == 'gray':
        return np.round(power * (params.levels - 1)) / (params.levels - 1)
    if params.strategy == 'bayer':
        matrix = np.array([[0]], dtype=np.float32)
        for _ in range(3):
            matrix = np.block([[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]])
        threshold = (matrix + 0.5) / 64
        rows, cols = power.shape
        return (power > threshold[np.arange(rows)[:, None] % 8, np.arange(cols) % 8]).astype(np.float32)
    if params.strategy != 'floyd':
        raise PhotoCarveError('Unknown laser dithering strategy.')
    if power.size > 4_000_000:
        raise PhotoCarveError('Floyd dithering is limited to 4000000 cells; increase resolution or spacing.')
    rows, cols = power.shape
    result = np.empty_like(power)
    current = power[0].tolist() if rows else []
    for row in range(rows):
        if check_abort is not None:
            check_abort()
        below = power[row + 1].tolist() if row + 1 < rows else [0.0] * cols
        direction = -1 if row % 2 else 1
        for col in range(cols) if direction == 1 else range(cols - 1, -1, -1):
            value = 1.0 if current[col] >= 0.5 else 0.0
            result[row, col] = value
            error = current[col] - value
            if 0 <= col + direction < cols:
                current[col + direction] += error * 7 / 16
                below[col + direction] += error / 16
            below[col] += error * 5 / 16
            if 0 <= col - direction < cols:
                below[col - direction] += error * 3 / 16
        current = below
    return result


def reverse_laser_path(path):
    result = np.asarray(path, dtype=np.float64)[::-1].copy()
    result[0, 2] = 0
    result[1:, 2] = path[:0:-1, 2]
    return result


def _merge_path(points):
    result = []
    for point in points:
        if result and point[:2] == result[-1][:2]:
            continue
        if len(result) >= 2 and result[-1][2] == point[2]:
            a, b = result[-2], result[-1]
            if (b[0] - a[0]) * (point[1] - b[1]) == (b[1] - a[1]) * (point[0] - b[0]):
                result.pop()
        result.append(point)
    return np.asarray(result, dtype=np.float64)


def laser_paths(power, params, check_abort=None):
    """Trim/split blank spans before adding off-power acceleration/deceleration spans."""
    frame = mode_frame(params)
    if power.shape != (frame.rows, frame.cols):
        raise PhotoCarveError('Laser power grid does not match the physical image frame.')
    paths, count = [], 0
    for row, values in enumerate(power):
        if check_abort is not None:
            check_abort()
        lit = np.flatnonzero(values > 0)
        if not len(lit):
            continue
        first, last = int(lit[0]), int(lit[-1]) + 1
        edges = np.r_[first, np.flatnonzero(np.diff(values[first:last])) + first + 1, last]
        portions, start = [], first
        for left, right in zip(edges, edges[1:]):
            if values[left] == 0 and (right - left) * frame.pitch_x > params.lift_gap:
                portions.append((start, int(left)))
                start = int(right)
        portions.append((start, last))
        row_paths = []
        y = frame.center(row, 0)[1]
        for start, end in portions:
            left = frame.x0 + start * frame.pitch_x
            right = frame.x0 + end * frame.pitch_x
            points = [(left - params.overscan, y, 0.0), (left, y, 0.0)]
            run_edges = edges[(edges >= start) & (edges <= end)]
            for a, b in zip(run_edges, run_edges[1:]):
                points.append((frame.x0 + int(b) * frame.pitch_x, y, float(values[a])))
            points.append((right + params.overscan, y, 0.0))
            path = _merge_path(points)
            count += len(path)
            if count > MAX_POINTS:
                raise PhotoCarveError('Laser paths exceed the point limit; increase resolution or spacing.')
            row_paths.append(path)
        if row % 2:
            row_paths = [reverse_laser_path(path) for path in row_paths[::-1]]
        paths.extend(row_paths)
    return paths


def format_power(fraction, minimum, maximum):
    """One shared integer-S mapping for the writer and simulation."""
    if not all(math.isfinite(v) for v in (fraction, minimum, maximum)):
        raise PhotoCarveError('Laser power settings must be finite.')
    if not 0 <= minimum < maximum:
        raise PhotoCarveError('Laser power must satisfy 0 <= minimum < maximum.')
    if not 0 <= fraction <= 1:
        raise PhotoCarveError('Laser path power must be between 0 and 1.')
    return 0 if fraction == 0 else int(round(minimum + fraction * (maximum - minimum)))
