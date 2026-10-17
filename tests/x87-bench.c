/* x87 workloads shaped like TMNF's per-frame math, timed under a CPU translator.
 * Built as a 32-bit x87 program by tools/bench-x87.py.
 *
 * Usage: x87-bench.exe WORKLOAD ITERATIONS [24|53]
 *   mat4    4x4 float matrix multiply chain (like the game's GmMat4/GmIso4 math)
 *   sprite  4 particle vertices per sprite, 20 single-precision stores
 *   int     integer hash loop: a control that uses no floating point
 * 24 or 53 sets the x87 precision control first; TMNF's racing thread uses 24.
 * Prints one line: "<workload> <ns per iteration> <checksum>".
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

static double seconds(void)
{
    LARGE_INTEGER now, frequency;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / (double)frequency.QuadPart;
}

static void set_precision(int bits)
{
    unsigned short cw;
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    cw = (unsigned short)((cw & ~0x0300) | (bits == 53 ? 0x0200 : 0x0000));
    __asm__ volatile("fldcw %0" :: "m"(cw));
}

static double mat4(long iterations)
{
    volatile float seed = 1.0001f;
    float a[16], b[16], c[16];
    for (int i = 0; i < 16; i++) {
        a[i] = seed + i * 0.001f;
        b[i] = seed - i * 0.0007f;
    }
    for (long n = 0; n < iterations; n++) {
        for (int r = 0; r < 4; r++)
            for (int k = 0; k < 4; k++)
                c[r * 4 + k] = a[r * 4] * b[k] + a[r * 4 + 1] * b[4 + k] + a[r * 4 + 2] * b[8 + k] +
                               a[r * 4 + 3] * b[12 + k];
        for (int i = 0; i < 16; i++)
            a[i] = c[i] * 0.25f + 0.75f;
    }
    return a[5];
}

static double sprite(long iterations)
{
    volatile float seed = 0.5f;
    static volatile float out[20];  /* a vertex buffer: every store happens */
    float cx = seed, cy = seed * 2, cz = seed * 3, size = 0.25f, rot = seed * 0.1f;
    const float rx = 0.8f, ry = 0.1f, rz = 0.2f, ux = -0.1f, uy = 0.9f, uz = 0.3f;
    for (long n = 0; n < iterations; n++) {
        float s = size * (1.0f + rot), c = size * (1.0f - rot);
        float ax = rx * s + ux * c, ay = ry * s + uy * c, az = rz * s + uz * c;
        float bx = rx * s - ux * c, by = ry * s - uy * c, bz = rz * s - uz * c;
        out[0] = cx - ax; out[1] = cy - ay; out[2] = cz - az; out[3] = 0.0f; out[4] = 0.0f;
        out[5] = cx + bx; out[6] = cy + by; out[7] = cz + bz; out[8] = 1.0f; out[9] = 0.0f;
        out[10] = cx + ax; out[11] = cy + ay; out[12] = cz + az; out[13] = 1.0f; out[14] = 1.0f;
        out[15] = cx - bx; out[16] = cy - by; out[17] = cz - bz; out[18] = 0.0f; out[19] = 1.0f;
        /* Feed results back but stay bounded, like positions jittering around an emitter. */
        cx = cx * 0.999f + out[6] * 0.001f;
        cy = cy * 0.999f + out[11] * 0.001f;
        cz = cz * 0.999f + out[17] * 0.001f;
        rot = rot * 0.999f + 0.0001f;
        __asm__ volatile("" ::: "memory");
    }
    return cx + cy + cz;
}

static double integer(long iterations)
{
    volatile unsigned seed = 2166136261u;
    unsigned h = seed, x = 12345;
    for (long n = 0; n < iterations; n++) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        h = (h ^ (x & 0xff)) * 16777619u;
        if (h & 1)
            h += (unsigned)n;
    }
    return (double)h;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: x87-bench.exe mat4|sprite|int ITERATIONS [24|53]\n");
        return 2;
    }
    long iterations = atol(argv[2]);
    if (argc > 3)
        set_precision(atoi(argv[3]));
    double (*run)(long) = strcmp(argv[1], "int") == 0 ? integer
                        : strcmp(argv[1], "sprite") == 0 ? sprite : mat4;
    run(iterations / 20 + 1);  /* translate once before timing */
    double start = seconds();
    double check = run(iterations);
    double elapsed = seconds() - start;
    printf("%s %.2f %.6g\n", argv[1], elapsed / iterations * 1e9, check);
    return 0;
}
