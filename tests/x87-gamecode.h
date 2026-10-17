/* Shared between x87-gamecode.c and the table tools/physics-gate-x87.py generates. */
#include <stdint.h>
#include <string.h>

#define MAX_BUFS 8
#define BUF_BYTES 512

struct call {
    uint8_t *base;             /* where TmForever.exe is mapped */
    void *buf[MAX_BUFS];       /* this pointer, then struct arguments, in order */
    float f;                   /* float argument */
    double d;                  /* double argument */
    unsigned char flag;        /* bool argument */
    unsigned small;            /* integer argument: 0..2 (indices, axes) */
    uint8_t ret[8];            /* return value */
};

struct function {
    const char *name;          /* decorated name from TmForever.map */
    void (*call)(struct call *c);
    int bufs;                  /* buffers the call uses */
};

extern const struct function FUNCTIONS[];
extern const int FUNCTION_COUNT;
