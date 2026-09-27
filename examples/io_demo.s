; Memory-mapped I/O smoke example.
.text
    LI   r1, 0xF000
    LI   r2, 0x0041
    SW   r2, 0(r1)       ; write 0x41 to peripheral register F000
    LW   r3, 1(r1)       ; read peripheral register F001
    HALT

.data
message:
    .asciz "ready"
