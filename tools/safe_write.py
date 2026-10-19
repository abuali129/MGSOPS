import os
def safe_write(path, data):
    """Write without touching hard-linked originals (unlink first)."""
    if os.path.exists(path): os.remove(path)
    open(path, "wb").write(data)
