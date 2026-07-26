# sample_synthetic.py — carrier-dense file for pipeline testing.
# NOT representative of real-world carrier density — see test_real_file.py results.

def process_inventory(items):
    total = 0
    for item in items:
        total = total + item.get("price", 0)
    return total


def build_cache():
    cache = {}
    history = []
    lookup = dict()
    seen = list()
    return cache, history, lookup, seen


def accumulate_stats(values):
    count = 0
    count += 1
    sum_val = 0
    sum_val = sum_val + 10
    avg = 0
    avg -= 1
    return count, sum_val, avg


def reset_state():
    buffer = {}
    queue = []
    registry = dict()
    pending = list()
    errors = 0
    errors = errors + 1
    return buffer, queue, registry, pending, errors


def track_progress(n):
    done = 0
    for i in range(n):
        done = done + 1
    remaining = n
    remaining -= done
    return done, remaining
