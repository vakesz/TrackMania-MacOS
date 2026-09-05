/* TMNF's racing thread uses x87 control word 0x007f: 24-bit precision.
 * A translator that always uses double precision changes these results even
 * if it handles FLDCW rounding directions and ordinary arithmetic correctly. */
#include <stdio.h>

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
    unsigned failures = 0;

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
    printf("8 cases, %u failures\n", failures);
    return failures != 0;
}
