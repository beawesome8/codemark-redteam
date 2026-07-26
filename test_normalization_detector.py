from watermark import detect_normalization_anomaly

files_to_check = {
    "sample_real_2.py (natural, unwatermarked)": "sample_real_2.py",
    "debug_paraphrase_output.py (attacked)": "debug_paraphrase_output.py",
    "debug_refactor_style_output.py (attacked)": "debug_refactor_style_output.py",
    "debug_cleanup_output.py (attacked)": "debug_cleanup_output.py",
    "sample_real_1.py (natural, different project, unwatermarked)": "sample_real_1.py",
}

for label, filename in files_to_check.items():
    with open(filename) as f:
        source = f.read()
    report = detect_normalization_anomaly(source)
    print(f"\n{label}:")
    for carrier_type, info in report.items():
        flag_marker = " <-- FLAGGED" if info["flagged"] else ""
        print(f"  {carrier_type}: {info}{flag_marker}")
