/* Call TrackMania's own Gm* math with deterministic inputs and hash the results.
 *
 * Usage: x87-gamecode.exe PATH\TmForever.exe CALLS [START [discover|dump]]
 * Maps the game executable without running it, then calls every function of the
 * generated table (tools/physics-gate-x87.py) from index START, CALLS times per x87
 * mode, and prints  HASH <function> <mode> <16 hex digits>. A crash prints
 * CRASH <index> and exits quietly. "discover" runs each function once and prints
 * BEGIN <index> first, so a hang names the function too. "dump" calls only
 * function START and prints its inputs and every changed output word instead of
 * hashes (tools/physics-gate-x87.py --explain). Ends with DONE.
 */
#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <windows.h>
#include "x87-gamecode.h"

static uint64_t rng;

static uint64_t next(void)
{
    rng ^= rng << 13;
    rng ^= rng >> 7;
    rng ^= rng << 17;
    return rng;
}

static double unit(void)
{
    return (double)(next() >> 11) / 9007199254740992.0;
}

/* Mostly ordinary game magnitudes, plus the edges where exact PC24 handling matters:
 * values near the float limits (overflow), tiny and subnormal floats (underflow),
 * signed zeros, small integers and the odd infinity. */
static float sample(void)
{
    unsigned pick = (unsigned)(next() % 100);
    double sign = (next() & 1) ? -1.0 : 1.0;
    if (pick < 55) return (float)(sign * unit() * 10.0);
    if (pick < 65) return (float)(sign * unit() * 1e4);
    if (pick < 72) return (float)(sign * (1.0 + unit()) * ldexp(1.0, -100 - (int)(next() % 50)));
    if (pick < 79) return (float)(sign * (1.0 + unit()) * ldexp(1.0, 100 + (int)(next() % 28)));
    if (pick < 86) {
        static const float fixed[] = {0.0f, -0.0f, 1.0f, -1.0f, 0.5f, 2.0f};
        return fixed[next() % 6];
    }
    if (pick < 93) return (float)((int)(next() % 11) - 5);
    if (pick < 99) return (float)(sign * (1.0 + unit()) * ldexp(1.0, (int)(next() % 254) - 126));
    return (float)(sign * HUGE_VAL);
}

static uint64_t hash;

static void mix(const uint8_t *p, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        hash ^= p[i];
        hash *= 1099511628211ULL;
    }
}

/* Hash floats word by word with NaNs made canonical: NaN payloads do not reach game
 * state the way ordinary values do, and binary64-based translation may pick another. */
static void mix_floats(const void *p, size_t n)
{
    const uint32_t *w = p;
    for (size_t i = 0; i < n / 4; i++) {
        uint32_t v = w[i];
        if ((v & 0x7f800000u) == 0x7f800000u && (v & 0x007fffffu) != 0) v = 0x7fc00000u;
        mix((const uint8_t *)&v, 4);
    }
}

static void set_cw(unsigned short cw)
{
    __asm__ volatile("fninit\n\tfldcw %0" :: "m"(cw));
}

static volatile int current = -1;

/* Report a crash and exit quietly instead of letting Wine start its debugger and
 * crash dialog. Functions that need game state the harness does not set up (for
 * example the CRT's math dispatch, initialised by the game's startup code) land here. */
static LONG WINAPI on_crash(EXCEPTION_POINTERS *info)
{
    printf("CRASH %d %08lx\n", current, info->ExceptionRecord->ExceptionCode);
    fflush(stdout);
    ExitProcess(3);
    return EXCEPTION_EXECUTE_HANDLER;
}

static const struct { const char *name; unsigned short cw; } MODES[] = {
    {"pc24-near", 0x007f}, {"pc24-down", 0x047f}, {"pc24-up", 0x087f}, {"pc24-zero", 0x0c7f},
    {"pc53-near", 0x027f},
};

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: x87-gamecode.exe TmForever.exe CALLS [START [discover|dump]]\n");
        return 2;
    }
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    SetUnhandledExceptionFilter(on_crash);
    HMODULE game = LoadLibraryExA(argv[1], NULL, DONT_RESOLVE_DLL_REFERENCES);
    if (game == NULL) {
        printf("ERROR cannot map %s (%lu)\n", argv[1], GetLastError());
        return 1;
    }
    const int calls = atoi(argv[2]);
    const int start = argc > 3 ? atoi(argv[3]) : 0;
    const int discover = argc > 4 && strcmp(argv[4], "discover") == 0;
    /* dump: only function START; print every float word a call changed, as
     * "W mode call buffer word input output" (buffer -1 is the return value). */
    const int dump = argc > 4 && strcmp(argv[4], "dump") == 0;
    static uint8_t before[MAX_BUFS][BUF_BYTES];
    static uint8_t storage[MAX_BUFS][BUF_BYTES] __attribute__((aligned(16)));
    struct call c = {.base = (uint8_t *)game};
    for (int b = 0; b < MAX_BUFS; b++) c.buf[b] = storage[b];

    for (int i = start; i < (dump ? start + 1 : FUNCTION_COUNT); i++) {
        const struct function *fn = &FUNCTIONS[i];
        current = i;
        if (discover) {
            printf("BEGIN %d\n", i);
            fflush(stdout);
        }
        for (unsigned m = 0; m < sizeof(MODES) / sizeof(MODES[0]); m++) {
            rng = 0x9e3779b97f4a7c15ULL ^ ((uint64_t)i << 20) ^ m;
            hash = 1469598103934665603ULL;
            for (int k = 0; k < (discover ? 1 : calls); k++) {
                for (int b = 0; b < fn->bufs; b++)
                    for (int w = 0; w < BUF_BYTES / 4; w++) ((float *)storage[b])[w] = sample();
                c.f = sample();
                c.d = sample();
                c.flag = (unsigned char)(next() & 1);
                c.small = (unsigned)(next() % 3);
                memset(c.ret, 0, sizeof(c.ret));
                if (dump) memcpy(before, storage, sizeof(before));
                if (dump) printf("F %s %d %08x\n", MODES[m].name, k, *(uint32_t *)&c.f);
                for (int b = 0; dump && b < fn->bufs; b++)
                    for (int w = 0; w < 12; w++)
                        printf("I %s %d %d %d %08x\n", MODES[m].name, k, b, w, ((uint32_t *)storage[b])[w]);
                set_cw(MODES[m].cw);
                fn->call(&c);
                set_cw(0x027f);
                for (int b = 0; dump && b < fn->bufs; b++)
                    for (int w = 0; w < BUF_BYTES / 4; w++) {
                        uint32_t was = ((uint32_t *)before[b])[w], now = ((uint32_t *)storage[b])[w];
                        if (was != now) printf("W %s %d %d %d %08x %08x\n", MODES[m].name, k, b, w, was, now);
                    }
                for (unsigned w = 0; dump && w < sizeof(c.ret) / 4; w++)
                    printf("W %s %d -1 %u 0 %08x\n", MODES[m].name, k, w, ((uint32_t *)c.ret)[w]);
                for (int b = 0; b < fn->bufs; b++) mix_floats(storage[b], BUF_BYTES);
                mix_floats(c.ret, sizeof(c.ret));
            }
            if (!discover && !dump) printf("HASH %s %s %016llx\n", fn->name, MODES[m].name, (unsigned long long)hash);
        }
    }
    printf("DONE\n");
    return 0;
}
