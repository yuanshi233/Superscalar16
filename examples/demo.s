; Superscalar16 default demo, using 16-bit word addresses.
.text
start:
    LI   r1, 5
    ADDI r2, r1, 3
    ADD  r3, r1, r2
    SW   r3, 0(r0)
    LW   r4, 0(r0)
    HALT

