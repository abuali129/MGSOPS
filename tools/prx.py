import struct, capstone
class Prx:
    def __init__(s, path):
        s.D=open(path,"rb").read()
        shoff,=struct.unpack_from("<I",s.D,0x20); shentsize,shnum,shstrndx=struct.unpack_from("<HHH",s.D,0x2e)
        secs=[struct.unpack_from("<IIIIIIIIII",s.D,shoff+i*shentsize) for i in range(shnum)]
        st=secs[shstrndx][4]
        s.secs={s.D[st+x[0]:s.D.index(b"\0",st+x[0])].decode():x for x in secs}
        t=s.secs[".text"]; s.OFF=t[4]-t[3]
        s.md=capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32+capstone.CS_MODE_LITTLE_ENDIAN); s.md.skipdata=True
    def u32(s,a): return struct.unpack_from("<I",s.D,a+s.OFF)[0]
    def dis(s,a,n):
        for i in s.md.disasm(s.D[a+s.OFF:a+s.OFF+4*n],a): print("%08x: %-8s %s"%(i.address,i.mnemonic,i.op_str))
    def stubs(s):
        res={}; a=s.secs[".lib.stub"][3]; end=a+s.secs[".lib.stub"][5]
        while a<end:
            name,va,szn,nidp,stubp=struct.unpack_from("<IIIII",s.D,a+s.OFF)
            sz=szn&0xff; nv=szn>>16
            n=s.D[name+s.OFF:s.D.index(b"\0",name+s.OFF)].decode()
            for i in range(nv): res[stubp+8*i]=(n,s.u32(nidp+4*i))
            a+=sz*4
        return res
