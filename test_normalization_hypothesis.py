from watermark import embed, carrier_form_summary

with open("sample_real_2.py") as f:
    original = f.read()

key = "test-secret-key"
payload = [1, 0, 1, 1, 0, 0, 1, 0]
watermarked = embed(original, key, payload)

with open("debug_paraphrase_output.py") as f:
    attacked = f.read()

print("Watermarked file carrier forms (type: [form0_count, form1_count]):")
print(carrier_form_summary(watermarked))

print("\nAttacked (paraphrased) file carrier forms:")
print(carrier_form_summary(attacked))