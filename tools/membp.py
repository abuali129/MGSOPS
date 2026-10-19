import json, sys, time
from ppctl import connect
addr=int(sys.argv[1],16); size=int(sys.argv[2],16); n=int(sys.argv[3]); mode=(sys.argv[4] if len(sys.argv)>4 else "r")
ws=connect(); ws.settimeout(60)
ws.send(json.dumps({"event":"memory.breakpoint.add","address":addr,"size":size,"read":mode=="r","write":mode=="w","change":False,"enabled":True,"log":False,"ticket":"x"}))
seen={}
try:
    while sum(seen.values())<n:
        m=json.loads(ws.recv())
        if m.get("ticket")=="x": print("add:",m); continue
        if m.get("event")!="cpu.stepping": continue
        pc=m.get("pc"); seen[pc]=seen.get(pc,0)+1
        ws.send(json.dumps({"event":"cpu.getReg","name":"ra","ticket":"ra"}))
        while True:
            x=json.loads(ws.recv())
            if x.get("ticket")=="ra": ra=x.get("uintValue");break
        if seen[pc]==1: print(hex(pc), hex(pc-0x8804000), "ra", hex(ra), hex(ra-0x8804000), flush=True)
        ws.send(json.dumps({"event":"cpu.resume"}))
finally:
    ws.send(json.dumps({"event":"memory.breakpoint.remove","address":addr,"size":size}))
    ws.send(json.dumps({"event":"cpu.resume"})); time.sleep(0.3)
print({hex(k):v for k,v in seen.items()})
