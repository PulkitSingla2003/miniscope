import numpy as np


def moving_average(data, window_size=5):
    """Simple moving average filter."""
    if len(data) < window_size:
        return data
    ret = np.cumsum(data, dtype=float)
    ret[window_size:] = ret[window_size:] - ret[:-window_size]
    return list(ret[window_size - 1:] / window_size)


def find_triggers(data, threshold, rising=True, max_found=3, min_width=3):
    """
    Return indices where threshold crossing happens.
    min_width: Number of consecutive samples that must be valid after crossing.
    """
    out = []
    N = len(data)
    for i in range(N - min_width):
        if rising:
            # Crossing: prev < th <= curr
            if data[i] < threshold <= data[i + 1]:
                # Check if it stays above threshold for min_width samples
                valid = True
                for k in range(1, min_width + 1):
                    if i + k >= N or data[i + k] < threshold:
                        valid = False
                        break
                if valid:
                    out.append(i)
        else:
            # Crossing: prev > th >= curr
            if data[i] > threshold >= data[i + 1]:
                # Check if it stays below threshold for min_width samples
                valid = True
                for k in range(1, min_width + 1):
                    if i + k >= N or data[i + k] > threshold:
                        valid = False
                        break
                if valid:
                    out.append(i)
        if len(out) >= max_found:
            break
    return out
