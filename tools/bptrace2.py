"""Trace kjfs open/seek/read with the opened path string; stops after N reads."""
import base64, json, sys, time
from ppctl import connect
BASE=0x8804000
OPEN,SEEK,READ,CLOSE=0x1c5a28,0x1c5aa8,0x1c5a50,0x1c5a70
ws=connect(); ws.settimeout(40)
def req(ev,**kw):
    t=str(time.time_ns()); ws.send(json.dumps({"event":ev,"ticket":t,**kw}))
    while True:
        m=json.loads(ws.recv())
        if m.get("ticket")==t: return m
def reg(r): return req("cpu.getReg",name=r).get("uintValue",0)
for a in (OPEN,SEEK,READ,CLOSE): ws.send(json.dumps({"event":"cpu.breakpoint.add","address":BASE+a,"enabled":True}))
n=int(sys.argv[1]); seen=0
try:
    while seen<n:
        m=json.loads(ws.recv())
        if m.get("event")!="cpu.stepping": continue
        pc=m["pc"]-BASE
        a0,a1,a2,a3,ra=(reg(r) for r in ("a0","a1","a2","a3","ra"))
        if pc==OPEN:
            raw=base64.b64decode(req("memory.read",address=a0,size=96)["base64"]).split(b"\0")[0]
            print("OPEN",raw.decode("latin1"),hex(ra-BASE),flush=True)
        elif pc==SEEK: print(" SEEK fd",a0,"a1",hex(a1),"a2",hex(a2),hex(ra-BASE),flush=True)
        elif pc==READ: print(" READ fd",a0,"buf",hex(a1),"size",hex(a2),hex(ra-BASE),flush=True); seen+=1
        else: print(" CLOSE",a0,flush=True)
        ws.send(json.dumps({"event":"cpu.resume"}))
finally:
    for a in (OPEN,SEEK,READ,CLOSE): ws.send(json.dumps({"event":"cpu.breakpoint.remove","address":BASE+a}))
    ws.send(json.dumps({"event":"cpu.resume"})); time.sleep(0.3)
