import requests, os
os.makedirs("data/pbp", exist_ok=True)
for yr in (2020, 2021, 2022, 2023, 2024):
    p = f"data/pbp/pbp_{yr}.csv"
    if os.path.exists(p) and os.path.getsize(p) > 5_000_000:
        print(f"{yr}: cached {os.path.getsize(p)/1e6:.0f}MB")
        continue
    u = f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{yr}.csv"
    with requests.get(u, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(p, "wb") as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
    print(f"{yr}: {os.path.getsize(p)/1e6:.0f}MB")
print("DONE")
