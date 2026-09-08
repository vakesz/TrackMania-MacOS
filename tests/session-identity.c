/* Synthetic identity regression tests. No case-sensitive filesystem is needed.
 * Include the actual comparison without running the launch supervisor. */
#define wmain session_main
#include "../src/session.c"
#undef wmain

#define CHECK(name, condition) do { \
    if (!(condition)) { fprintf(stderr, "FAIL: %s\n", name); return 1; } \
    printf("PASS: %s\n", name); \
} while (0)

int wmain(void)
{
    BY_HANDLE_FILE_INFORMATION a = {0}, b, missing = {0};
    a.dwVolumeSerialNumber = 7;
    a.nFileIndexHigh = 9;
    a.nFileIndexLow = 11;
    b = a;
    CHECK("identical usable IDs match", same_file(&a, &b));

    ++b.nFileIndexLow;
    CHECK("case-folded path collision cannot override different IDs",
            CompareStringOrdinal(L"C:\\Game.exe", -1, L"C:\\game.exe", -1, TRUE) == CSTR_EQUAL &&
            !same_file(&a, &b));
    b = a;
    ++b.nFileIndexHigh;
    CHECK("different high file-index bits do not match", !same_file(&a, &b));
    b = a;
    ++b.dwVolumeSerialNumber;
    CHECK("same file index on different volumes does not match", !same_file(&a, &b));

    b = a;
    CHECK("same-ID aliases still match",
            CompareStringOrdinal(L"C:\\Game.exe", -1, L"S:\\alias\\Game.exe", -1, TRUE) != CSTR_EQUAL &&
            same_file(&a, &b));
    CHECK("missing image ID does not match", !same_file(&missing, &a));
    CHECK("missing watch ID does not match", !same_file(&a, &missing));
    CHECK("two missing IDs do not match", !same_file(&missing, &missing));

    a.nFileIndexHigh = 0;
    b = a;
    CHECK("nonzero low file-index bits are usable", same_file(&a, &b));
    a.nFileIndexHigh = 9;
    a.nFileIndexLow = 0;
    b = a;
    CHECK("nonzero high file-index bits are usable", same_file(&a, &b));
    return 0;
}
