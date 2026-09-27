.equ DISP, 0xF000

.text
start:
    LI   r1, DISP       ; r1 = 外设地址 F000
    LI   r2, 0          ; a = 0
    LI   r3, 1          ; b = 1

loop:
    SW   r2, 0(r1)      ; 把当前斐波那契数写到 F000

    ; 延时，不然变化太快看不见
    LI   r4, 4          ; 16 Hz 时钟下约每 4 秒更新一次
delay:
    ADDI r4, r4, -1
    BNEZ r4, delay

    ; c = a + b
    ADD  r5, r2, r3
    MOV  r2, r3         ; a = b
    MOV  r3, r5         ; b = c
    B    loop
