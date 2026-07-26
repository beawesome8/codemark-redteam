from watermark import embed, detect
import subprocess
import sys
import tempfile
import os


def run_and_capture(source: str) -> str:
    """Execute source in a subprocess, return stdout. Isolates each run cleanly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        result = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=5)
        return result.stdout
    finally:
        os.unlink(path)


# --- Test A: functional equivalence ---
original = """
total = 0
for i in range(10):
    total = total + i
count = 1
count += 1
result = 100
result = result - 5
print(total, count, result)
"""

key = "test-secret-key"
payload = [1, 0, 1, 1]

watermarked = embed(original, key, payload)

out_original = run_and_capture(original)
out_watermarked = run_and_capture(watermarked)

print("Original output:   ", out_original.strip())
print("Watermarked output:", out_watermarked.strip())
assert out_original == out_watermarked, "FUNCTIONAL MISMATCH — watermarking changed behavior!"
print("PASS: functional equivalence holds.\n")

# --- Test B: false-positive check on genuinely unwatermarked code ---
# add to test_correctness.py, replace the unrelated_source block
unrelated_source = """
balance = 0
balance = balance + 50
inventory = 10
inventory += 3
score = 90
score = score - 10
tally = 0
tally += 7
"""

recovered, carriers_found, total_carriers = detect(unrelated_source, key, payload_len=len(payload))
matches = sum(1 for a, b in zip(recovered, payload) if a == b)
print(f"Unwatermarked code — recovered bits: {recovered}, carriers found: {carriers_found}")
print(f"Bits matching the payload by chance: {matches}/{len(payload)} (expect ~50%, i.e. ~2/4)")