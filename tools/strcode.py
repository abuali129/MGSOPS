def strcode(s):
    h = 0
    for c in s.encode():
        h = (((h << 5) | (h >> 19)) + c) & 0xffffff
    return h or 1
