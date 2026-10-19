import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from zarls import parse
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
d = open(src, "rb").read()
for name, off, size in parse(d)[0]:
    open(os.path.join(dst, name), "wb").write(d[off:off+size])
