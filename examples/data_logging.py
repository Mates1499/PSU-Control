#!/usr/bin/env python3
"""Log all three channels' voltage / current / power to a CSV at a fixed rate."""

import csv
import sys
import time

from psu_control import ITN6332B


def log(host: str, duration_s: float, interval_s: float, path: str) -> None:
    with ITN6332B.open_tcp(host) as psu:
        print("Logging from:", psu.idn())
        psu.all_output_on()

        names = [ch.spec.name for ch in psu.channels]
        header = ["t_s"]
        for n in names:
            header += [f"{n}_V", f"{n}_A", f"{n}_W"]

        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)

            t0 = time.monotonic()
            while True:
                t = time.monotonic() - t0
                snap = psu.measure_all()
                row = [f"{t:.3f}"]
                for n in names:
                    m = snap[n]
                    row += [m.voltage, m.current, m.power]
                writer.writerow(row)
                fh.flush()
                print(f"t={t:6.2f}s  " + "  ".join(f"{n}:{snap[n]}" for n in names))

                if t >= duration_s:
                    break
                time.sleep(interval_s)

    print(f"Saved log to {path}")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.50"
    log(host, duration_s=10.0, interval_s=0.5, path="psu_log.csv")
