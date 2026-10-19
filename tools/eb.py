import struct, capstone
D=open(r"C:\Users\HP\Downloads\psp\MGSPO_Arabic\work\dump\ULUS10202_EBOOT.BIN","rb").read()
OFF=0xc0
def u32(a): return struct.unpack_from("<I",D,a+OFF)[0]
md=capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32+capstone.CS_MODE_LITTLE_ENDIAN)
md.skipdata=True
def dis(a,n):
    for i in md.disasm(D[a+OFF:a+OFF+4*n],a): print("%08x: %-8s %s"%(i.address,i.mnemonic,i.op_str))
def stubs():
    res={}
    st=0x1d6dec
    while st<0x1d706c:
        name,va,szn,nidp,stubp=struct.unpack_from("<IIIII",D,st+OFF)
        sz=szn&0xff; nv=szn>>16
        n=D[name+OFF:D.index(b"\0",name+OFF)].decode()
        for i in range(nv): res[stubp+8*i]=(n,u32(nidp+4*i))
        st+=sz*4
    return res
def xrefs(target):
    out=[]
    jal=0x0c000000|(target>>2)
    for a in range(0x40,0x1d6868,4):
        if u32(a)==jal: out.append(a)
    return out
