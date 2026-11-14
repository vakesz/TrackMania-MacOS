/* SSE2 version of the particle billboard loop in TrackMania Nations ESWC 1.8.0.
 *
 * TmNationsESWC.exe function 0x80ff40 expands each particle of a visual into a
 * camera-facing quad of four vertices, written to a locked Direct3D vertex buffer. It
 * runs in x87 code with the control word at 24-bit precision and round toward zero.
 * This file computes the same vertices with SSE2 at single precision and MXCSR round
 * toward zero, which gives identical bits for every particle the guard below accepts.
 * Other particles, and every particle when the control word is not 24-bit precision,
 * run through the original x87 code. See "Nations ESWC particle speed patch" in
 * docs/runtime.md.
 *
 * The two loop heads jump to entry.s, which calls these functions with the game
 * function's stack frame (ESP at the loop head). They return 0 when the loop is done
 * and 1 when the current particle must go through the original loop body.
 *
 * Built into billboards.s with the pinned compiler and assembled with entry.s by
 * trackmania_mac.build.sse_particles_builder. The result may not use data sections or
 * absolute addresses, so constants are built from immediates.
 */
#include <emmintrin.h>
#include <stdint.h>

typedef uint32_t u32;
typedef uint8_t u8;

/* The game function's stack frame, relative to ESP at the loop head. */
#define FRAME_INDEX 0x18     /* indexed loop: position in the sorted list */
#define FRAME_OFFSET 0x1c    /* linear loop: byte offset of the particle */
#define FRAME_COUNT 0x78     /* indexed loop: number of particles */
#define FRAME_ORDER 0x7c     /* indexed loop: sorted index list; linear loop: particles left */
#define FRAME_SYSTEM 0x98    /* particle system; its particle array pointer is at +0x84 */
#define FRAME_RIGHT 0x9c     /* camera right vector, 3 floats */
#define FRAME_UP 0xa8        /* camera up vector, 3 floats */
#define FRAME_VERTICES 0xf8  /* next vertex to write */
#define FRAME_UV_RECTS 0xfc  /* texture rectangles: count at +4, array at +8 */
#define FRAME_SAVED_CW 0x158 /* the game's x87 control word, saved by the function */

#define PARTICLE_SIZE 40
#define VERTEX_SIZE 24

static inline __m128 splat(u32 bits)
{
    __asm__("" : "+r"(bits)); /* keeps gcc from moving the constant to .rdata */
    return _mm_castsi128_ps(_mm_shuffle_epi32(_mm_cvtsi32_si128((int)bits), 0));
}

static inline u32 load32(const u8 *p)
{
    u32 value;
    __builtin_memcpy(&value, p, 4);
    return value;
}

static inline void store32(u8 *p, u32 value) { __builtin_memcpy(p, &value, 4); }

/* What an x87 load and store does to a float: a signalling NaN becomes quiet. */
static inline u32 quieted(u32 bits)
{
    return ((bits & 0x7fc00000u) == 0x7f800000u && (bits & 0x003fffffu)) ? bits | 0x00400000u
                                                                         : bits;
}

static inline u32 lane(__m128i v, int k)
{
    switch (k) {
    case 0: return (u32)_mm_cvtsi128_si32(v);
    case 1: return (u32)_mm_cvtsi128_si32(_mm_shuffle_epi32(v, 0x55));
    case 2: return (u32)_mm_cvtsi128_si32(_mm_shuffle_epi32(v, 0xaa));
    default: return (u32)_mm_cvtsi128_si32(_mm_shuffle_epi32(v, 0xff));
    }
}

static inline void write_vertex(u8 *vertex, __m128 position, u32 color, u32 u, u32 v)
{
    _mm_storel_pi((__m64 *)vertex, position);
    _mm_store_ss((float *)(vertex + 8), _mm_movehl_ps(position, position));
    store32(vertex + 12, color);
    store32(vertex + 16, u);
    store32(vertex + 20, v);
}

/* Write the four vertices of one particle. Returns 0, writing nothing, when the
 * result could differ from the x87 code:
 * - a NaN in the position or the scaled vectors (x87 and SSE pick different NaNs
 *   when two meet);
 * - size times aspect is subnormal, underflowed to zero, or may have overflowed (x87
 *   keeps it at full precision until the next multiply);
 * - the x coordinate's first sum may have overflowed (x87 keeps it until the second
 *   add). The y and z sums are stored after each add, as in SSE. */
static inline __attribute__((always_inline)) int particle(const u8 *frame, const u8 *p, u8 *out)
{
    const __m128 sign = splat(0x80000000u), magnitude = splat(0x7fffffffu);
    const __m128 right = _mm_loadu_ps((const float *)(frame + FRAME_RIGHT)); /* lane 3 unused */
    const __m128 up = _mm_loadu_ps((const float *)(frame + FRAME_UP));       /* lane 3 unused */

    const __m128 position = _mm_loadu_ps((const float *)p); /* x, y, z, size */
    const __m128 size = _mm_shuffle_ps(position, position, 0xff);
    const __m128 aspect = _mm_load_ss((const float *)(p + 0x14));
    const __m128 height = _mm_mul_ss(size, aspect);
    const __m128 across = _mm_mul_ps(right, size);
    const __m128 along = _mm_mul_ps(up, _mm_shuffle_ps(height, height, 0));
    const __m128 back = _mm_add_ps(position, _mm_xor_ps(across, sign));
    const __m128 front = _mm_add_ps(position, across);

    const __m128 nan = _mm_or_ps(
        _mm_or_ps(_mm_cmpunord_ps(position, position), _mm_cmpunord_ps(across, across)),
        _mm_cmpunord_ps(along, along));
    if (_mm_movemask_ps(nan) & 7)
        return 0;
    const __m128 largest = splat(0x7f7fffffu), smallest = splat(0x00800000u);
    const __m128 zero = _mm_setzero_ps();
    const __m128 height_magnitude = _mm_and_ps(height, magnitude);
    if (_mm_comieq_ss(height_magnitude, zero)) {
        if (!_mm_comieq_ss(size, zero) && !_mm_comieq_ss(aspect, zero))
            return 0;
    } else if (_mm_comilt_ss(height_magnitude, smallest)) {
        return 0;
    }
    if (_mm_comieq_ss(height_magnitude, largest)
        || _mm_comieq_ss(_mm_and_ps(front, magnitude), largest)
        || _mm_comieq_ss(_mm_and_ps(back, magnitude), largest))
        return 0;

    /* Colour: x87 fistp to 16 bits of channel * 255, toward zero; out of range or
     * NaN gives 0x8000. Packed as the game does: ARGB from channels R, G, B, A. */
    const __m128 scaled = _mm_mul_ps(_mm_loadu_ps((const float *)(p + 0x18)), splat(0x437f0000u));
    const __m128 in_range = _mm_and_ps(_mm_cmpgt_ps(scaled, splat(0xc7000100u)),  /* -32769 */
                                       _mm_cmplt_ps(scaled, splat(0x47000000u))); /* 32768 */
    const __m128i truncated = _mm_cvttps_epi32(scaled);
    const __m128i channels =
        _mm_or_si128(_mm_and_si128(_mm_castps_si128(in_range), truncated),
                     _mm_andnot_si128(_mm_castps_si128(in_range), _mm_castps_si128(splat(0x8000u))));
    const u32 low = (lane(channels, 2) & 0xffff) | (lane(channels, 0) << 16);
    const u32 high = (lane(channels, 1) & 0xffff) | (lane(channels, 3) << 16);
    const u32 color = low | (high << 8);

    /* Texture rectangle {u0, v0, u1, v1}, chosen by the particle's frame index. */
    const u8 *rects = (const u8 *)load32(frame + FRAME_UV_RECTS);
    u32 index = load32(p + 0x10);
    if (index >= load32(rects + 4))
        index = 0;
    const u8 *rect = (const u8 *)(load32(rects + 8) + (index << 4));
    const u32 u0 = quieted(load32(rect)), v0 = quieted(load32(rect + 4));
    const u32 u1 = quieted(load32(rect + 8)), v1 = quieted(load32(rect + 12));

    /* Corners in the game's order: (position -+ across) +- along. */
    write_vertex(out, _mm_add_ps(back, along), color, u0, v0);
    write_vertex(out + VERTEX_SIZE, _mm_add_ps(back, _mm_xor_ps(along, sign)), color, u1, v0);
    write_vertex(out + 2 * VERTEX_SIZE, _mm_add_ps(front, along), color, u0, v1);
    write_vertex(out + 3 * VERTEX_SIZE, _mm_add_ps(front, _mm_xor_ps(along, sign)), color, u1, v1);
    return 1;
}

static inline int precision_is_24_bit(const u8 *frame)
{
    return (*(const uint16_t *)(frame + FRAME_SAVED_CW) & 0x0300) == 0;
}

static inline const u8 *particles(const u8 *frame)
{
    return (const u8 *)load32((const u8 *)load32(frame + FRAME_SYSTEM) + 0x84);
}

/* Loop head 0x810685: particles in depth order. */
int indexed_loop(u8 *frame)
{
    if (!precision_is_24_bit(frame))
        return 1;
    const u32 saved = _mm_getcsr();
    _mm_setcsr(0x7f80); /* round toward zero, exceptions masked, no flush to zero */
    const u8 *order = (const u8 *)load32(frame + FRAME_ORDER);
    const u32 count = load32(frame + FRAME_COUNT);
    u32 index = load32(frame + FRAME_INDEX);
    u8 *out = (u8 *)load32(frame + FRAME_VERTICES);
    int result = 0;
    do {
        const u8 *p = particles(frame) + load32(order + 4 * index) * PARTICLE_SIZE;
        if (!particle(frame, p, out)) {
            result = 1;
            break;
        }
        out += 4 * VERTEX_SIZE;
        index++;
    } while (index < count);
    store32(frame + FRAME_INDEX, index);
    store32(frame + FRAME_VERTICES, (u32)out);
    _mm_setcsr(saved);
    return result;
}

/* Loop head 0x810d17: particles in array order. */
int linear_loop(u8 *frame)
{
    if (!precision_is_24_bit(frame))
        return 1;
    const u32 saved = _mm_getcsr();
    _mm_setcsr(0x7f80);
    u32 offset = load32(frame + FRAME_OFFSET), left = load32(frame + FRAME_ORDER);
    u8 *out = (u8 *)load32(frame + FRAME_VERTICES);
    int result = 0;
    do {
        if (!particle(frame, particles(frame) + offset, out)) {
            result = 1;
            break;
        }
        out += 4 * VERTEX_SIZE;
        offset += PARTICLE_SIZE;
    } while (--left != 0);
    store32(frame + FRAME_OFFSET, offset);
    store32(frame + FRAME_ORDER, left);
    store32(frame + FRAME_VERTICES, (u32)out);
    _mm_setcsr(saved);
    return result;
}
