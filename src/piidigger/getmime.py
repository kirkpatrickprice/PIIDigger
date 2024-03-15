# This looks like a better cross-platform implementation.  Will need to look at implementing as the current "magic" package fails on Windows
# https://github.com/cdgriffith/puremagic

moduleName='getmime'

import os
import sys

try:
    import puremagic 
except Exception:
    pass

def testMagic() -> bool:
    return 'puremagic' in sys.modules

def getMime(filename: str) -> str:
    if not isinstance(filename, str):
        filename=str(filename)
    if os.path.isdir(filename):
        return "Directory"
    
    try:
        mimeType = puremagic.from_file(filename, mime=True) if testMagic() else None
    except Exception: 
        mimeType = None
        
    
    return mimeType

def main():
    if testMagic():
        for arg in sys.argv[1:]:
            if os.path.exists(arg):
                print('Filename: %s\nMime: %s\n' % (arg, getMime(arg)))
            else:
                print ("%s: File not found" % arg)
    else:
        print('Mime detection library could not be loaded')

if __name__ == "__main__":
    main()