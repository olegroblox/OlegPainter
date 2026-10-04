# Paint brush regression

`tiny-before.png` and `tiny-after.png` are unmodified 310×310 screen patches
from the real Paint test on 2026-09-23 (Windows DPI 125%). They show the blank
test canvas and a tiny black stamp at (155, 155); a cursor halo changes at the
top-left edge. No user artwork is included.

Source: `test-results/quick-brush-paint-trace/brush-trace/001-{before,after}.png`.
The old measurement erased the tiny mark and reported the edge changes as
a 211.228px brush. The central mark has contrast-weighted area 5.8px².
