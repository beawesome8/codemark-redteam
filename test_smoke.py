from watermark import embed, detect

source = """
total = 0
for i in range(10):
    total = total + i
count += 1
result = result - 5
"""

key = "test-secret-key"
payload = [1, 0, 1]  # 3-bit toy payload

watermarked = embed(source, key, payload)
print("--- Watermarked ---")
print(watermarked)

recovered, matched, total = detect(watermarked, key, len(payload))
print(f"Recovered: {recovered}, expected: {payload}")
assert recovered == payload, "Payload mismatch!"
print("PASS: payload recovered correctly.")