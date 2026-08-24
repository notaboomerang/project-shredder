import requests, os
u = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_2024.csv"
p = os.path.join("data", "pbp_2024.csv")
with requests.get(u, stream=True, timeout=120) as r:
    r.raise_for_status()
    with open(p, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
sz = os.path.getsize(p) / (1024*1024)
with open(p, encoding="utf-8", errors="replace") as f:
    header = f.readline().strip()
cols = header.split(",")
print(f"{sz:.1f}MB, {len(cols)} cols")
print("has posteam:", "posteam" in cols, "| has yardline_100:", "yardline_100" in cols,
      "| has fixed_drive_result:", "fixed_drive_result" in cols)
