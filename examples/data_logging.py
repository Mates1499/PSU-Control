#!/usr/bin/env python3
"""Log voltage / current / power to a CSV file at a fixed interval.

Current and power are signed (negative while the supply is sinking).
"""

import csv
import sys
import time

from psu_control import ITN6332B


def log(host: str, duration_s: float, interval_s: float, path: str) -> None:
    with ITN6332B.open_tcp(host) as psu:
        print("Logging from:", psu.idn())
        psu.output_on()

        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["t_s", "voltage_V", "current_A", "power_W", "mode"])

            t0 = time.monotonic()
            while True:
                t = time.monotonic() - t0
                m = psu.measure()
                writer.writerow([f"{t:.3f}", m.voltage, m.current, m.power, psu.regulation_mode()])
                fh.flush()
                print(f"t={t:6.2f}s  {m}")
                if t >= duration_s:
                    break
                time.sleep(interval_s)

    print(f"Saved log to {path}")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.200.100"
    log(host, duration_s=10.0, interval_s=0.5, path="psu_log.csv")
