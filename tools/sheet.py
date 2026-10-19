import sys
from PIL import Image
files=sys.argv[2:]; out=sys.argv[1]
ims=[Image.open(f).convert("RGB") for f in files]
w,h=640,315; cols=3; rows=(len(ims)+cols-1)//cols
S=Image.new("RGB",(w*cols,h*rows))
for i,im in enumerate(ims): S.paste(im.resize((w,h)),((i%cols)*w,(i//cols)*h))
S.save(out)
