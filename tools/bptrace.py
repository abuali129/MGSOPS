"""Breakpoint tracer: python bptrace.py <hits> <off>[,regs] ...
offsets are relative to mgp_main (EBOOT file addr); prints requested regs each hit."""
import json, sys, time
from ppctl import connect

BASE = 0x8804000


def main():
    hits = int(sys.argv[1])
    specs = []
    for s in sys.argv[2:]:
        off, _, regs = s.partition(",")
        specs.append((BASE + int(off, 16), regs.split(":") if regs else ["a0", "a1", "a2"]))
    ws = connect()
    ws.settimeout(60)
    for addr, _ in specs:
        ws.send(json.dumps({"event": "cpu.breakpoint.add", "address": addr, "enabled": True}))
    rmap = dict(specs)
    n = 0
    try:
        while n < hits:
            m = json.loads(ws.recv())
            if m.get("event") != "cpu.stepping":
                continue
            pc = m.get("pc")
            regs = rmap.get(pc)
            if regs is None:
                ws.send(json.dumps({"event": "cpu.resume"})); continue
            vals = []
            for r in regs:
                ws.send(json.dumps({"event": "cpu.getReg", "name": r, "ticket": r}))
                while True:
                    x = json.loads(ws.recv())
                    if x.get("ticket") == r:
                        vals.append(f"{r}={x.get('uintValue', 0):08x}"); break
            print(hex(pc - BASE), *vals, flush=True)
            n += 1
            ws.send(json.dumps({"event": "cpu.resume"}))
    finally:
        for addr, _ in specs:
            ws.send(json.dumps({"event": "cpu.breakpoint.remove", "address": addr}))
        ws.send(json.dumps({"event": "cpu.resume"}))
        time.sleep(0.3)


if __name__ == "__main__":
    main()
