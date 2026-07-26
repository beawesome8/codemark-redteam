"""
test_carrier_density.py — measure carrier density across multiple real files,
to check whether density correlates with code style (stateful/imperative vs
declarative/API code) rather than just file length.
"""
from watermark import find_carriers, embed, detect
import ast

files = ["sample_target.py", "sample_real_1.py", "sample_real_2.py"]

results = []
for filename in files:
    with open(filename) as f:
        source = f.read()
    tree = ast.parse(source)
    carriers = find_carriers(tree)
    loc = len(source.splitlines())
    density = loc / max(len(carriers), 1)
    results.append((filename, loc, len(carriers), density))
    print(f"{filename}: {loc} lines, {len(carriers)} carriers, 1 per {density:.1f} lines")

print("\n--- Summary ---")
for filename, loc, count, density in sorted(results, key=lambda r: r[3]):
    print(f"{filename:25s} density: 1/{density:.1f}")

# Only attempt payload test on files with enough carriers
print("\n--- Payload recovery (files with >=8 carriers only) ---")
key = "test-secret-key"
payload = [1, 0, 1, 1, 0, 0, 1, 0]

for filename, loc, count, density in results:
    if count >= len(payload):
        with open(filename) as f:
            source = f.read()
        watermarked = embed(source, key, payload)
        recovered, found, total = detect(watermarked, key, payload_len=len(payload))
        print(f"{filename}: recovered={recovered}, match={recovered == payload}")
    else:
        print(f"{filename}: SKIPPED (only {count} carriers, need {len(payload)})")
