"""Turn saved Robinhood get_equity_historicals JSON responses into per-symbol CSVs.

    python3 -m bot.ingest data/ response1.json response2.json ...

Files are merged with what's already in data/<SYMBOL>_<interval>.csv and
de-duplicated by timestamp. Interpolated (gap-fill) bars are dropped.
"""
import csv
import json
import sys
from pathlib import Path

FIELDS = ["ts", "open", "high", "low", "close", "volume", "session"]
INTERVAL_NAMES = {"minute": "1m", "5minute": "5m", "day": "1d"}


def load_csv(path):
    if not path.exists():
        return {}
    with path.open() as f:
        return {row["ts"]: row for row in csv.DictReader(f)}


def ingest(out_dir, files):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = {}
    for file in files:
        for result in json.loads(Path(file).read_text())["data"]["results"]:
            key = (result["symbol"], INTERVAL_NAMES.get(result["interval"], result["interval"]))
            rows = merged.setdefault(key, None)
            if rows is None:
                rows = merged[key] = load_csv(out_dir / f"{key[0]}_{key[1]}.csv")
            for bar in result["bars"]:
                if bar.get("interpolated"):
                    continue
                rows[bar["begins_at"]] = {
                    "ts": bar["begins_at"], "open": bar["open_price"], "high": bar["high_price"],
                    "low": bar["low_price"], "close": bar["close_price"], "volume": bar["volume"],
                    "session": bar.get("session", "reg"),
                }
    for (symbol, interval), rows in merged.items():
        path = out_dir / f"{symbol}_{interval}.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows[k] for k in sorted(rows))
        print(f"{path.name}: {len(rows)} bars, {min(rows)[:10]} .. {max(rows)[:10]}")


if __name__ == "__main__":
    ingest(sys.argv[1], sys.argv[2:])
