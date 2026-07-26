from watermark import embed, detect, find_carriers
import ast

with open("sample_synthetic.py") as f:
    source = f.read()

tree = ast.parse(source)
carriers = find_carriers(tree)
loc = len(source.splitlines())
print(f"File: sample_synthetic.py, {loc} lines, {len(carriers)} carriers found")
print(f"Density: 1 carrier per {loc / max(len(carriers), 1):.1f} lines")

if len(carriers) < 8:
    print("WARNING: too few carriers for a meaningful payload test. Try a larger/different file.")
else:
    key = "test-secret-key"
    payload = [1, 0, 1, 1, 0, 0, 1, 0]  # 8-bit payload

    watermarked = embed(source, key, payload)
    recovered, found, total = detect(watermarked, key, payload_len=len(payload))
    print(f"\nPayload: {payload}")
    print(f"Recovered: {recovered}")
    print(f"Match: {recovered == payload}")

    # sanity: does it still parse and is it different from the original?
    ast.parse(watermarked)  # will raise if broken
    print(f"Watermarked file still parses correctly: True")
    print(f"Watermarked differs from original: {watermarked != source}")