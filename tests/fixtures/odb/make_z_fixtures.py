"""
Make the .Z fixtures of tests/fixtures/odb with the ncompress package (PyPI 'ncompress', the C code of ncompress),
so the fixtures do not depend on the decoder of the app. Not a test; run it once by hand:
    pip install --target <folder> ncompress
    set PYTHONPATH=<folder> & python tests/fixtures/odb/make_z_fixtures.py tests/fixtures/odb
"""
import os
import random
import sys

import ncompress

out = sys.argv[1]
os.makedirs(out, exist_ok=True)

small = b'UNITS=MM\n#\n$0 r100\n#\nP 1.0 2.0 0 P 0 0\nL 0 0 5 5 0 P 0\n'
# about 200 kB of varied text: the code table fills, so the width passes 9 -> 16 bits
rng = random.Random(7)
lines = []
for i in range(8000):
    lines.append(b'P %.4f %.4f %d P %d 0;1=%d\n' % (rng.uniform(-50, 50), rng.uniform(-50, 50),
                                                     i % 97, rng.randint(0, 7), i))
wide = b''.join(lines)
# random bytes after the table is full: the compression ratio drops, so ncompress emits CLEAR
clear = wide[:137000] + bytes(rng.getrandbits(8) for _ in range(60000)) + wide[:20000]

for name, data in (('small', small), ('wide', wide), ('clear', clear)):
    with open(os.path.join(out, name + '.txt'), 'wb') as handle:
        handle.write(data)
    with open(os.path.join(out, name + '.txt.Z'), 'wb') as handle:
        handle.write(ncompress.compress(data))
    print(name, len(data), os.path.getsize(os.path.join(out, name + '.txt.Z')))

