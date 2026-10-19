import base64, json, sys
from ppctl import connect, call
pat = sys.argv[1].encode().decode("unicode_escape").encode("latin1")
ws = connect()
hits = []
for base in range(0x08800000, 0x0a000000, 0x100000):
    r = call(ws, "memory.read", address=base, size=0x100000)
    if "base64" not in r: print(r); break
    d = base64.b64decode(r["base64"])
    i = d.find(pat)
    while i >= 0:
        hits.append(base + i); i = d.find(pat, i + 1)
print([hex(h) for h in hits])
