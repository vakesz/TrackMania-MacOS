/* TMNF's racing thread uses x87 control word 0x007f: 24-bit precision.
 * A translator that always uses double precision changes these results even
 * if it handles FLDCW rounding directions and ordinary arithmetic correctly. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

/* Integer encodings are the oracle, not arithmetic run by the translator.
 * 1 + 2^-53 is halfway between binary64 1 and its successor. */
static unsigned rc_cases(void)
{
    unsigned failures = 0;
    for (unsigned negative = 0; negative < 2; ++negative)
        for (unsigned mode = 0; mode < 4; ++mode) {
            uint64_t sign = (uint64_t)negative << 63;
            uint64_t a_bits = UINT64_C(0x3ff0000000000000) | sign;
            uint64_t b_bits = UINT64_C(0x3ca0000000000000) | sign;
            double a, b, result;
            unsigned short saved, cw = 0x027f | (mode << 10);
            memcpy(&a, &a_bits, 8);
            memcpy(&b, &b_bits, 8);
            __asm__ volatile("fnstcw %0; fldcw %2; fldl %3; faddl %4; fstpl %1; fldcw %0"
                             : "=m"(saved), "=m"(result)
                             : "m"(cw), "m"(a), "m"(b) : "st", "memory");
            uint64_t actual, expected = a_bits + (mode == (negative ? 1u : 2u));
            memcpy(&actual, &result, 8);
            printf("PC53 sign=%u RC=%u bits=%016llx expected=%016llx\n", negative, mode,
                   (unsigned long long)actual, (unsigned long long)expected);
            failures += actual != expected;

            /* FLD m64 ignores PC24. Only FST m32 narrows this halfway value. */
            a_bits = UINT64_C(0x3ff0000010000000) | sign;
            memcpy(&a, &a_bits, 8);
            for (unsigned pc = 0; pc < 3; ++pc) {
                cw = 0x007f | (pc == 0 ? 0 : pc == 1 ? 0x0200 : 0x0300) | (mode << 10);
                float out, nonpop;
                __asm__ volatile("fnstcw %0; fldcw %3; fldl %4; fsts %2; fstps %1; fldcw %0"
                                 : "=m"(saved), "=m"(out), "=m"(nonpop)
                                 : "m"(cw), "m"(a) : "st", "memory");
                uint32_t got, got_nonpop;
                uint32_t want = (negative << 31) | (0x3f800000u + (mode == (negative ? 1u : 2u)));
                memcpy(&got, &out, 4);
                memcpy(&got_nonpop, &nonpop, 4);
                printf("FST/FSTP PC=%u sign=%u RC=%u bits=%08x/%08x expected=%08x\n",
                       pc == 0 ? 24 : pc == 1 ? 53 : 64, negative, mode, got_nonpop, got, want);
                failures += got != want || got_nonpop != want;
            }
        }
    return failures;
}

static unsigned operation_cases(void)
{
    /* All expectations follow integer significands: (1+2^-52)^2 lies
     * strictly above 1+2^-51; 1/3 repeats binary 01; sqrt(2)'s adjacent
     * significands square below/above 2, with the midpoint below sqrt(2). */
    static const uint64_t lhs_bits[] = {0x3ff0000000000000ULL, 0x3ff0000000000001ULL,
                                        0x3ff0000000000000ULL, 0x4000000000000000ULL};
    static const uint64_t rhs_bits[] = {0xbca0000000000000ULL, 0x3ff0000000000001ULL,
                                        0x4008000000000000ULL, 0};
    static const uint64_t floor_bits[] = {0x3ff0000000000000ULL, 0x3ff0000000000002ULL,
                                          0x3fd5555555555555ULL, 0x3ff6a09e667f3bccULL};
    unsigned failures = 0;
    for (unsigned op = 0; op < 4; ++op)
        for (unsigned rc = 0; rc < 4; ++rc) {
            unsigned short saved, cw = 0x027f | (rc << 10);
            double a, b, out;
            memcpy(&a, &lhs_bits[op], 8);
            memcpy(&b, &rhs_bits[op], 8);
#define CALCULATE(instruction) \
            __asm__ volatile("fnstcw %0; fldcw %2; fldl %3; " instruction "; fstpl %1; fldcw %0" \
                             : "=m"(saved), "=m"(out) : "m"(cw), "m"(a), "m"(b) : "st", "memory")
            switch (op) {
                case 0: CALCULATE("fsubl %4"); break;
                case 1: CALCULATE("fmull %4"); break;
                case 2: CALCULATE("fdivl %4"); break;
                default: CALCULATE("fsqrt"); break;
            }
#undef CALCULATE
            uint64_t got, want = floor_bits[op] + (rc == 2 || (op == 3 && rc == 0));
            memcpy(&got, &out, 8);
            printf("PC53 op=%u RC=%u bits=%016llx expected=%016llx\n", op, rc,
                   (unsigned long long)got, (unsigned long long)want);
            failures += got != want;
        }
    return failures;
}

static unsigned store_sequences(void)
{
    unsigned failures = 0;
    const double a = 0x1.000001p0, zero = 0.;
    for (unsigned shape = 0; shape < 8; ++shape)
        for (unsigned rc = 0; rc < 4; ++rc) {
            unsigned short saved, cw = 0x027f | (rc << 10);
            float out;
            double scratch;
#define STORE(sequence) \
            __asm__ volatile("fnstcw %0; fldcw %3; " sequence "; fldcw %0" \
                             : "=m"(saved), "=m"(out), "=m"(scratch) \
                             : "m"(cw), "m"(a), "m"(zero) : "st", "memory")
            switch (shape) {
                case 0: STORE("fldl %4; fstps %1"); break;
                case 1: STORE("fldl %4; faddl %5; fstps %1"); break;
                case 2: STORE("fldl %4; fldl %5; faddp; fstps %1"); break;
                case 3: STORE("fldl %4; fldl %5; fxch; fstps %1; fstpl %2"); break;
                case 4: STORE("fldl %4; fldl %5; fstpl %2; faddl %5; fstps %1"); break;
                case 5: STORE("fldl %4; fstps %1; fldl %5; fstpl %2"); break;
                case 6: STORE("fldl %4; fstps %1; fldl %4; fstps %1"); break;
                /* Integer instruction splits the x87 run for single-op lowering. */
                default: STORE("fldl %4; jmp 1f; 1: fstps %1"); break;
            }
#undef STORE
            uint32_t got, want = 0x3f800000u + (rc == 2);
            memcpy(&got, &out, 4);
            printf("store shape=%u RC=%u bits=%08x expected=%08x\n", shape, rc, got, want);
            failures += got != want;
        }
    return failures;
}

static double rounded_add(unsigned short control)
{
    const double one = 1.0, half_ulp = 0x1p-24;
    unsigned short saved;
    double result;

    __asm__ volatile(
        "fnstcw %0\n\t"
        "fldcw %2\n\t"
        "fldl %3\n\t"
        "faddl %4\n\t"
        "fsubl %3\n\t"
        "fstpl %1\n\t"
        "fldcw %0"
        : "=m"(saved), "=m"(result)
        : "m"(control), "m"(one), "m"(half_ulp)
        : "st", "memory");
    return result;
}

int main(void)
{
    const char *rounding[] = {"nearest", "down", "up", "truncate"};
    unsigned failures = rc_cases() + operation_cases() + store_sequences();

    for (unsigned precision = 0; precision < 2; ++precision)
        for (unsigned mode = 0; mode < 4; ++mode)
        {
            unsigned short control = 0x007f | (precision ? 0x0200 : 0) | (mode << 10);
            double expected = precision ? 0x1p-24 : mode == 2 ? 0x1p-23 : 0.0;
            double actual = rounded_add(control);

            printf("%s PC=%u rounding=%s result=%.17g expected=%.17g\n",
                   actual == expected ? "PASS" : "FAIL", precision ? 53 : 24,
                   rounding[mode], actual, expected);
            failures += actual != expected;
        }
    printf("88 cases, %u failures\n", failures);
    return failures != 0;
}
