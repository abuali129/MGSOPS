"""Tiny PPSSPP remote-debugger client (WebSocket API) for automated testing.

usage:
  python ppctl.py shot out.png
  python ppctl.py press cross [frames]
  python ppctl.py seq "start:5 wait:60 cross:5" out.png
  python ppctl.py status
  python ppctl.py raw '{"event":"game.status"}'
"""
import base64, json, sys, time
import websocket

URL = "ws://127.0.0.1:51228/debugger"


def call(ws, event, **params):
    ticket = str(time.time_ns())
    ws.send(json.dumps({"event": event, "ticket": ticket, **params}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("ticket") == ticket or msg.get("event") in ("error", event):
            return msg


def connect():
    return websocket.create_connection(URL, timeout=30)


def shot(ws, path):
    call(ws, "cpu.stepping")
    r = call(ws, "gpu.buffer.screenshot", type="uri")
    call(ws, "cpu.resume")
    if "uri" not in r:
        print(r)
        return
    open(path, "wb").write(base64.b64decode(r["uri"].split(",", 1)[1]))
    print("saved", path)


def cap(name):
    """Grab the PPSSPP window into work/<name> (debugger screenshots fail on Vulkan)."""
    import os, subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "work", "shots", name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(here, "wincap.ps1"), "-Out", os.path.abspath(out)])


def press(ws, button, frames=4):
    return call(ws, "input.buttons.press", button=button, duration=int(frames))


def seq(ws, spec):
    for tok in spec.split():
        k, _, v = tok.partition(":")
        v = int(v or 4)
        if k == "wait":
            time.sleep(v / 60)
        else:
            print(k, press(ws, k, v).get("event"))
            time.sleep(v / 60 + 0.1)


if __name__ == "__main__":
    ws = connect()
    cmd = sys.argv[1]
    if cmd == "shot":
        shot(ws, sys.argv[2])
    elif cmd == "press":
        print(press(ws, sys.argv[2], *(sys.argv[3:4] or [4])))
    elif cmd == "seq":
        seq(ws, sys.argv[2])
        if len(sys.argv) > 3:
            cap(sys.argv[3])
    elif cmd == "cap":
        cap(sys.argv[2])
    elif cmd == "status":
        print(call(ws, "game.status"))
    elif cmd == "raw":
        m = json.loads(sys.argv[2])
        ev = m.pop("event")
        print(json.dumps(call(ws, ev, **m))[:3000])
