/* Compare the Nations ESWC particle patch with the game's own x87 loop.
 *
 * Usage: eswc-sse-particles.exe PATCHED_EXE orig|stub check CALLS
 *        eswc-sse-particles.exe PATCHED_EXE orig|stub time PARTICLES CALLS
 *
 * Maps the installed, patched TmNationsESWC.exe without running it (like
 * x87-gamecode.c). In this private copy the loop exit 0x811b0f jumps back here, and
 * "orig" puts the original bytes back at both loop heads, so the x87 loop runs; "stub"
 * keeps the jumps into the patch. Each call builds a stack frame and particles from
 * its seed: game-like values, edge values (subnormal, huge, infinite, NaN, colours
 * outside 0..1, texture indices out of range) or a mix, under the game's 24-bit
 * precision or another control word, enters the loop setup (0x810640 for particles
 * in depth order, 0x810cca in array order) and prints one line per call:
 *
 *   H CALL GROUP N FALLBACKS sse HASH x87 HASH state HASH mxcsr BEFORE AFTER
 *
 * The vertex hashes cover the particles the patch computes itself (sse) and those
 * it hands to the x87 loop (x87), from the guard in billboards.c; state covers the
 * loop's live-out frame words, ESP and the x87 stack and control word. Wine maps
 * the exe away from 0x400000 here, which is fine: the loop and the patch use only
 * relative branches and stack or pointer addressing. Run by
 * trackmania_mac.probes.eswc_sse_gate.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "../src/c/eswc-sse-particles/billboards.c"

/* h_enter(frame, entry, eax, cw): run the game's loop setup with ESP = frame; the
 * patched exit comes back through h_exit and returns from h_enter. */
__asm__(".data\n"
        "_h_saved_esp: .long 0\n"
        "_h_exit_esp: .long 0\n"
        "_h_mxcsr: .long 0\n"
        "_h_env: .space 28\n"
        ".text\n"
        ".globl _h_enter, _h_exit\n"
        "_h_enter:\n"
        "  pushl %ebp\n  pushl %ebx\n  pushl %esi\n  pushl %edi\n"
        "  movl %esp, _h_saved_esp\n"
        "  movl 20(%esp), %ecx\n  movl 24(%esp), %edx\n  movl 28(%esp), %eax\n"
        "  fninit\n  fldcw 32(%esp)\n"
        "  movl %ecx, %esp\n  movl $1, %esi\n  jmp *%edx\n"
        "_h_exit:\n"
        "  movl %esp, _h_exit_esp\n  fnstenv _h_env\n  stmxcsr _h_mxcsr\n"
        "  movl _h_saved_esp, %esp\n"
        "  popl %edi\n  popl %esi\n  popl %ebx\n  popl %ebp\n  ret\n");
extern void h_enter(void *frame, void *entry, unsigned eax, unsigned cw);
extern void h_exit(void);
extern uint32_t h_exit_esp, h_mxcsr;
extern uint16_t h_env[14];

static uint8_t *image; /* where Wine mapped the exe: image + (VA - 0x400000) */
#define AT(va) (image + ((va) - 0x400000))

static uint64_t rng;
static uint64_t next(void)
{
    rng ^= rng << 13;
    rng ^= rng >> 7;
    rng ^= rng << 17;
    return rng;
}
static double unit(void) { return (double)(next() >> 11) / 9007199254740992.0; }
static float between(float low, float high) { return (float)(low + (high - low) * unit()); }
static float from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, 4);
    return value;
}

/* The mix of x87-gamecode.c's sample(), plus NaNs and subnormals. */
static float edge(void)
{
    unsigned pick = (unsigned)(next() % 100);
    double sign = (next() & 1) ? -1.0 : 1.0;
    if (pick < 50) return (float)(sign * unit() * 10.0);
    if (pick < 60) return (float)(sign * unit() * 1e4);
    if (pick < 67) return (float)(sign * (1.0 + unit()) * ldexp(1.0, -100 - (int)(next() % 50)));
    if (pick < 74) return (float)(sign * (1.0 + unit()) * ldexp(1.0, 100 + (int)(next() % 28)));
    if (pick < 81) {
        static const float fixed[] = {0.0f, -0.0f, 1.0f, -1.0f, 0.5f, 2.0f};
        return fixed[next() % 6];
    }
    if (pick < 87) return (float)((int)(next() % 11) - 5);
    if (pick < 94) return (float)(sign * (1.0 + unit()) * ldexp(1.0, (int)(next() % 254) - 126));
    if (pick < 96) return (float)(sign * HUGE_VAL);
    if (pick < 97) return from_bits(0x7fc00000u | (uint32_t)(next() & 0x803fffffu));
    if (pick < 98) return from_bits(0x7f800001u | (uint32_t)(next() & 0x803fffffu));
    if (pick < 99) return from_bits((uint32_t)(next() & 0x807fffffu));
    return (float)(sign * 3.40282347e38);
}

enum { GAME, EDGE, MIXED };
static const char *const GROUPS[] = {"game", "edge", "mixed"};
static const unsigned MXCSRS[] = {0x1f80, 0x9fc0, 0x5f80, 0x1fbf, 0x3f80};

typedef struct {
    int linear, group;
    unsigned cw, mxcsr;
    uint32_t count, stored;
    uint8_t *particles, *system, *rects_header, *rects, *out;
    uint32_t *order;
    float right[3], up[3];
    uint8_t filler[0x200];
} Call;

static void make_call(Call *c, int seed, int game_only, uint32_t count)
{
    rng = 0x9e3779b97f4a7c15ull ^ ((uint64_t)seed * 0x100000001b3ull);
    for (int k = 0; k < 4; k++) next();
    c->linear = (int)(next() & 1);
    unsigned pick = (unsigned)(next() % 10);
    c->cw = game_only || pick < 8 ? 0x007f : pick < 9 ? 0x027f : 0x037f;
    c->mxcsr = MXCSRS[next() % 5];
    c->group = game_only ? GAME : (int)(next() % 3);
    c->count = count     ? count
               : next() % 16 == 0 ? 200 + (uint32_t)(next() % 57)
                                  : 1 + (uint32_t)(next() % 48);
    c->stored = c->linear ? c->count : c->count + (uint32_t)(next() % 8);
    c->particles = malloc(c->stored * PARTICLE_SIZE);
    c->order = malloc(c->count * 4);
    c->system = calloc(1, 0x100);
    c->rects_header = calloc(1, 12);
    c->rects = malloc(4 * 16);
    c->out = malloc(c->count * 4 * VERTEX_SIZE + 64);
    memset(c->out, 0xcd, c->count * 4 * VERTEX_SIZE);
    memset(c->out + c->count * 4 * VERTEX_SIZE, 0xa5, 64);
    const uint32_t rects = (uint32_t)(next() % 5);
    for (int k = 0; k < 3; k++) {
        c->right[k] = c->group == EDGE ? edge() : between(-1.5f, 1.5f);
        c->up[k] = c->group == EDGE ? edge() : between(-1.5f, 1.5f);
    }
    for (uint32_t i = 0; i < c->stored; i++) {
        float *f = (float *)(c->particles + PARTICLE_SIZE * i);
        if (c->group == EDGE || (c->group == MIXED && next() % 8 == 0)) {
            for (int k = 0; k < 10; k++) f[k] = edge();
        } else {
            f[0] = between(-2000, 2000), f[1] = between(-50, 600), f[2] = between(-2000, 2000);
            f[3] = between(0.01f, 20.0f);
            f[5] = between(0.1f, 4.0f);
            for (int k = 6; k < 10; k++) f[k] = next() % 16 ? between(0, 1) : between(-0.2f, 1.3f);
        }
        uint32_t index = (uint32_t)(next() % (rects + 1));
        if (next() % 16 == 0) index = (uint32_t)next();
        memcpy(&f[4], &index, 4);
    }
    for (uint32_t i = 0; i < c->count; i++) c->order[i] = (uint32_t)(next() % c->stored);
    for (int k = 0; k < 16; k++) ((float *)c->rects)[k] = c->group == EDGE ? edge() : between(0, 1);
    *(uint32_t *)(c->system + 0x80) = c->count;
    *(uint8_t **)(c->system + 0x84) = c->particles;
    ((uint32_t *)c->rects_header)[1] = rects;
    ((uint8_t **)c->rects_header)[2] = c->rects;
    for (int k = 0; k < 0x200; k++) c->filler[k] = (uint8_t)next();
}

static void free_call(Call *c)
{
    free(c->particles), free(c->order), free(c->system), free(c->rects_header), free(c->rects);
    free(c->out);
}

typedef struct {
    uint32_t vertices, position, left, esp, cw, top, tag, mxcsr;
} Result;

static __attribute__((noinline)) void run_call(const Call *c, Result *r)
{
    volatile uint8_t stack[0x10000]; /* the frame lives on this thread's stack */
    uint8_t *frame = (uint8_t *)(((uintptr_t)stack + 0xc000) & ~(uintptr_t)0xf);
    memcpy(frame, c->filler, 0x200);
    *(uint32_t *)(frame + FRAME_INDEX) = 0;
    *(uint32_t *)(frame + FRAME_COUNT) = c->count;
    *(uint32_t *)(frame + FRAME_ORDER) = c->linear ? 0 : (uint32_t)c->order;
    *(uint8_t **)(frame + FRAME_SYSTEM) = c->system;
    memcpy(frame + FRAME_RIGHT, c->right, 12);
    memcpy(frame + FRAME_UP, c->up, 12);
    *(uint8_t **)(frame + FRAME_VERTICES) = c->out;
    *(uint8_t **)(frame + FRAME_UV_RECTS) = c->rects_header;
    *(uint16_t *)(frame + FRAME_SAVED_CW) = (uint16_t)c->cw;
    _mm_setcsr(c->mxcsr);
    /* The function ORs round toward zero into the game's control word. */
    h_enter(frame, c->linear ? AT(0x810cca) : AT(0x810640), c->count, c->cw | 0x0c00);
    r->mxcsr = h_mxcsr;
    _mm_setcsr(0x1f80);
    __asm__ volatile("fninit");
    r->vertices = *(uint32_t *)(frame + FRAME_VERTICES) - (uint32_t)c->out;
    r->position = *(uint32_t *)(frame + (c->linear ? FRAME_OFFSET : FRAME_INDEX));
    r->left = c->linear ? *(uint32_t *)(frame + FRAME_ORDER) : 0;
    r->esp = h_exit_esp == (uint32_t)frame;
    r->cw = h_env[0], r->top = (h_env[2] >> 11) & 7, r->tag = h_env[4];
}

static uint64_t fnv(uint64_t h, const void *p, size_t n)
{
    for (size_t i = 0; i < n; i++) h = (h ^ ((const uint8_t *)p)[i]) * 1099511628211ull;
    return h;
}

/* Marks the particles billboards.c hands to the x87 loop; returns how many. */
static uint32_t fallbacks(const Call *c, uint8_t *mark)
{
    if (c->cw & 0x0300) {
        memset(mark, 1, c->count);
        return c->count;
    }
    uint8_t frame[0x200] = {0}, scratch[4 * VERTEX_SIZE];
    memcpy(frame + FRAME_RIGHT, c->right, 12);
    memcpy(frame + FRAME_UP, c->up, 12);
    *(uint8_t **)(frame + FRAME_UV_RECTS) = c->rects_header;
    const unsigned saved = _mm_getcsr();
    _mm_setcsr(0x7f80);
    uint32_t total = 0;
    for (uint32_t i = 0; i < c->count; i++) {
        const uint32_t k = c->linear ? i : c->order[i];
        mark[i] = (uint8_t)!particle(frame, c->particles + PARTICLE_SIZE * k, scratch);
        total += mark[i];
    }
    _mm_setcsr(saved);
    return total;
}

static void patch(uint8_t *at, const void *bytes, size_t n)
{
    DWORD old;
    VirtualProtect(at, n, PAGE_EXECUTE_READWRITE, &old);
    memcpy(at, bytes, n);
    VirtualProtect(at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, n);
}

static LONG WINAPI on_crash(EXCEPTION_POINTERS *info)
{
    printf("CRASH %08lx\n", info->ExceptionRecord->ExceptionCode);
    fflush(stdout);
    ExitProcess(3);
    return EXCEPTION_EXECUTE_HANDLER;
}

static void time_calls(int stub, uint32_t count, uint32_t calls)
{
    for (int linear = 0; linear < 2; linear++) {
        Call c;
        Result r;
        make_call(&c, 7 + linear, 1, count);
        c.linear = linear, c.mxcsr = 0x1f80;
        for (int warm = 0; warm < 3; warm++) run_call(&c, &r);
        LARGE_INTEGER frequency, start, end;
        QueryPerformanceFrequency(&frequency);
        QueryPerformanceCounter(&start);
        for (uint32_t k = 0; k < calls; k++) run_call(&c, &r);
        QueryPerformanceCounter(&end);
        const double ns = (double)(end.QuadPart - start.QuadPart) * 1e9 / (double)frequency.QuadPart;
        printf("T %s %s %.2f\n", stub ? "stub" : "orig", linear ? "array-order" : "depth-order",
               ns / ((double)count * calls));
        free_call(&c);
    }
}

int main(int argc, char **argv)
{
    if (argc < 5 || (strcmp(argv[2], "orig") && strcmp(argv[2], "stub"))) {
        fprintf(stderr, "usage: eswc-sse-particles.exe EXE orig|stub check CALLS | time PARTICLES CALLS\n");
        return 2;
    }
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    SetUnhandledExceptionFilter(on_crash);
    image = (uint8_t *)LoadLibraryExA(argv[1], NULL, DONT_RESOLVE_DLL_REFERENCES);
    if (image == NULL) {
        printf("ERROR cannot map %s (%lu)\n", argv[1], GetLastError());
        return 1;
    }
    if (*AT(0x810685) != 0xe9 || *AT(0x810d17) != 0xe9 || memcmp(AT(0xce8000), "\x54\xe8", 2)) {
        printf("ERROR %s does not have the particle patch\n", argv[1]);
        return 1;
    }
    const int stub = strcmp(argv[2], "stub") == 0;
    if (!stub) {
        patch(AT(0x810685), "\x8b\x54\x24\x7c\x8b", 5);
        patch(AT(0x810d17), "\x8b\x8c\x24\x98\x00", 5);
    }
    uint8_t jump[5] = {0xe9};
    const int32_t distance = (int32_t)((uintptr_t)h_exit - (uintptr_t)(AT(0x811b0f) + 5));
    memcpy(jump + 1, &distance, 4);
    patch(AT(0x811b0f), jump, 5);

    if (strcmp(argv[3], "time") == 0) {
        time_calls(stub, (uint32_t)atoi(argv[4]), argc > 5 ? (uint32_t)atoi(argv[5]) : 100);
        printf("DONE\n");
        return 0;
    }
    const int calls = atoi(argv[4]);
    for (int k = 0; k < calls; k++) {
        Call c;
        Result r;
        make_call(&c, k, 0, 0);
        run_call(&c, &r);
        uint8_t *mark = malloc(c.count);
        const uint32_t handed = fallbacks(&c, mark);
        uint64_t sse = 1469598103934665603ull, x87 = sse;
        for (uint32_t i = 0; i < c.count; i++) {
            const uint8_t *vertices = c.out + 4 * VERTEX_SIZE * i;
            if (mark[i]) x87 = fnv(x87, vertices, 4 * VERTEX_SIZE);
            else sse = fnv(sse, vertices, 4 * VERTEX_SIZE);
        }
        free(mark);
        int canary = 1;
        for (int b = 0; b < 64; b++) canary &= c.out[c.count * 4 * VERTEX_SIZE + b] == 0xa5;
        const uint32_t state[] = {r.vertices, r.position, r.left, r.esp, r.cw, r.top, r.tag, (uint32_t)canary};
        printf("H %d %s%s %u %u sse %016llx x87 %016llx state %016llx mxcsr %04x %04x\n", k,
               GROUPS[c.group], c.cw == 0x007f ? "" : "-not-pc24", c.count, handed,
               (unsigned long long)sse, (unsigned long long)x87,
               (unsigned long long)fnv(1469598103934665603ull, state, sizeof state), c.mxcsr, r.mxcsr);
        free_call(&c);
    }
    printf("DONE\n");
    return 0;
}
