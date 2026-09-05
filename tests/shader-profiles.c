/* Regression for D3DX9 legacy function targets, verified against Microsoft SDKs 30 and 36.
 * Compare executable shader words with unqualified references, excluding comment metadata. */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3dx9.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef HRESULT (WINAPI *compile_fn)(const char *, UINT, const D3DXMACRO *, ID3DXInclude *, const char *, const char *, DWORD, ID3DXBuffer **, ID3DXBuffer **, ID3DXConstantTable **);
typedef HRESULT (WINAPI *disasm_fn)(const DWORD *, BOOL, const char *, ID3DXBuffer **);
static unsigned int failures, cases;
static DWORD compile_flags;
static compile_fn compile;
static disasm_fn disasm;
#define RED "float4 main():COLOR{return float4(1,0,0,1);}\n"
#define GREEN "float4 main():COLOR{return float4(0,1,0,1);}\n"
#define BLUE "float4 main():COLOR{return float4(0,0,1,1);}\n"
#define POSITION "float4 main(float4 p:POSITION):POSITION{return p;}\n"
#define SCALED_POSITION "float4 main(float4 p:POSITION):POSITION{return p*2;}\n"
#define NEGATED_POSITION "float4 main(float4 p:POSITION):POSITION{return -p;}\n"
static unsigned tokens(ID3DXBuffer *b, DWORD *out)
{
    const DWORD *p = ID3DXBuffer_GetBufferPointer(b);
    unsigned n = ID3DXBuffer_GetBufferSize(b) / sizeof(*p), count = 0;
    for (unsigned i = 0; i < n; ++i)
    {
        if ((p[i] & 0xffff) == D3DSIO_COMMENT) i += (p[i] >> 16) & 0x7fff;
        else out[count++] = p[i];
    }
    return count;
}
static void test(const char *name, const char *src, const char *profile, int expected)
{
    ID3DXBuffer *b = NULL, *e = NULL;
    HRESULT hr = compile(src, strlen(src), NULL, NULL, "main", profile, compile_flags, &b, &e, NULL);
    int actual = -1;
    ++cases;
    printf("%s (%s): %08lx", name, profile, hr);
    if (b)
    {
        const char *outputs[] = {RED, GREEN, BLUE};
        DWORD a[4096], c[4096];
        unsigned ac = tokens(b, a);
        int found = -1;
        if (!strncmp(profile, "vs_", 3))
        {
            outputs[0] = POSITION;
            outputs[1] = SCALED_POSITION;
            outputs[2] = NEGATED_POSITION;
        }
        for (unsigned i = 0; i < 3; ++i)
        {
            ID3DXBuffer *ref = NULL;
            compile(outputs[i], strlen(outputs[i]), NULL, NULL, "main", profile, compile_flags, &ref, NULL, NULL);
            if (ref)
            {
                if (tokens(ref, c) == ac && !memcmp(a, c, ac * sizeof(*a))) found = i;
                ID3DXBuffer_Release(ref);
            }
        }
        actual = found;
        if (found < 0) ++failures;
        printf(" output=%d", found);
        if (found < 0 && disasm)
        {
            ID3DXBuffer *d = NULL;
            disasm(ID3DXBuffer_GetBufferPointer(b), FALSE, NULL, &d);
            if (d) {printf("\n%s", (char *)ID3DXBuffer_GetBufferPointer(d)); ID3DXBuffer_Release(d);}
        }
        ID3DXBuffer_Release(b);
    }
    if ((expected >= 0) != SUCCEEDED(hr) || actual != expected)
    {
        ++failures;
        printf(" FAIL expected=%d actual=%d", expected, actual);
    }
    printf("\n");
    if (e) {printf("%s\n", (char *)ID3DXBuffer_GetBufferPointer(e)); ID3DXBuffer_Release(e);}
    fflush(stdout);
}
int main(int argc, char **argv)
{
    HMODULE m = LoadLibraryA(argc > 1 ? argv[1] : "d3dx9_30.dll");
    if (!m) return 2;
    compile = (compile_fn)(void *)GetProcAddress(m, "D3DXCompileShader");
    disasm = (disasm_fn)(void *)GetProcAddress(m, "D3DXDisassembleShader");
    if (!compile) return 2;
    if (argc > 2 && !strcmp(argv[2], "legacy-flag"))
        compile_flags = D3DXSHADER_USE_LEGACY_D3DX9_31_DLL;
    if (argc > 2 && !strcmp(argv[2], "modern"))
    {
        test("plain", RED, "ps_2_0", 0);
        test("reject legacy target", "ps_2_0 " RED, "ps_2_0", -1);
        test("profile identifier remains a typedef", "typedef float ps_2_0;\nps_2_0 f(){return 1;}\n" RED, "ps_2_0", 0);
        printf("%u cases, %u failures\n", cases, failures);
        FreeLibrary(m);
        return failures ? 1 : 0;
    }
    test("plain", RED, "ps_2_0", 0);
    test("exact", "ps_2_0 " RED, "ps_2_0", 0);
    test("wrong profile", "ps_3_0 " RED, "ps_2_0", -1);
    test("two profiles", "ps_2_0 " RED "ps_3_0 " GREEN, "ps_2_0", 0);
    test("two profiles reverse", "ps_3_0 " GREEN "ps_2_0 " RED, "ps_2_0", 0);
    test("two profiles SM3", "ps_2_0 " RED "ps_3_0 " GREEN, "ps_3_0", 1);
    test("generic then exact", BLUE "ps_2_0 " RED, "ps_2_0", 0);
    test("exact then generic", "ps_2_0 " RED BLUE, "ps_2_0", 0);
    test("generic fallback", BLUE "ps_3_0 " GREEN, "ps_2_0", 2);
    test("family", "ps " BLUE "ps_2 " GREEN "ps_2_0 " RED, "ps_2_0", 0);
    test("family match", "ps " BLUE "ps_2 " GREEN "ps_3_0 " RED, "ps_2_0", 2);
    test("family only", "ps " BLUE, "ps_2_0", 2);
    test("same profile duplicate", "ps_2_0 " RED "ps_2_0 " GREEN, "ps_2_0", -1);
    test("unknown profile", "nonsense " RED, "ps_2_0", -1);
    test("other API profiles", "arbfp1 " GREEN "glslf " BLUE "ps_2_0 " RED, "ps_2_0", 0);
    test("wrong stage", "vs_1_1 " GREEN "ps_2_0 " RED, "ps_2_0", 0);
    test("multiple qualifiers", "ps_2_0 ps_3_0 " RED, "ps_2_0", -1);
    test("prototype", "ps_2_0 float4 main():COLOR;\nps_2_0 " RED, "ps_2_0", 0);
    test("profile helper", "ps_3_0 float4 f(){return float4(0,1,0,1);}\nps_2_0 float4 f(){return float4(1,0,0,1);}\nfloat4 main():COLOR{return f();}", "ps_2_0", 0);
    test("helper fallback", "float4 f(){return float4(0,0,1,1);}\nps_3_0 float4 f(){return float4(0,1,0,1);}\nfloat4 main():COLOR{return f();}", "ps_2_0", 2);
    test("bad unused body", "ps_3_0 float4 f(){return missing();}\nps_2_0 " RED, "ps_2_0", -1);
    test("profile variable", "ps_2_0 float4 value;\n" RED, "ps_2_0", -1);
    test("typedef profile", "typedef float ps_2_0;\nps_2_0 f(){return 1;}\n" RED, "ps_2_0", 0);
    test("family vs generic", BLUE "ps " GREEN, "ps_2_0", 1);
    test("SM2 family alone", "ps_2 " GREEN, "ps_2_0", -1);
    test("profile after modifier", "static ps_2_0 float4 f(){return float4(1,0,0,1);}\nfloat4 main():COLOR{return f();}", "ps_2_0", 0);
    test("profile before modifier", "ps_2_0 static float4 f(){return float4(1,0,0,1);}\nfloat4 main():COLOR{return f();}", "ps_2_0", 0);
    test("profile vs type rank", "float4 f(float p){return float4(0,0,1,1);}\nps_2_0 float4 f(float2 p){return float4(1,0,0,1);}\nfloat4 main():COLOR{return f(1.0);}", "ps_2_0", 2);
    test("unused profile calls own helper", "ps_3_0 float4 f(){return float4(0,1,0,1);}\nps_3_0 float4 other(){return f();}\nps_2_0 " RED, "ps_2_0", 0);
    test("SM2a exact", "ps_2_0 " RED "ps_2_a " GREEN, "ps_2_a", 1);
    test("SM2a matches SM2", "ps_2_0 " RED, "ps_2_a", -1);
    test("SM2a family", "ps " BLUE "ps_2 " GREEN "ps_2_a " RED, "ps_2_a", 0);
    test("SM2sw exact", "ps_2_0 " RED "ps_2_sw " GREEN, "ps_2_sw", 1);
    test("global initializer uses compilation target", "ps_2_0 float4 f(){return float4(1,0,0,1);}\nps_3_0 float4 f(){return float4(0,1,0,1);}\nstatic float4 value=f();\nfloat4 main():COLOR{return value;}", "ps_2_0", 0);
    test("vertex model 1", "vs_1_1 " POSITION "vs_2_0 " SCALED_POSITION, "vs_1_1", 0);
    test("vertex model 2", "vs_1_1 " POSITION "vs_2_0 " SCALED_POSITION, "vs_2_0", 1);
    test("vertex stage fallback", "vs " NEGATED_POSITION "vs_2_0 " SCALED_POSITION, "vs_1_1", 2);
    printf("%u cases, %u failures\n", cases, failures);
    FreeLibrary(m);
    return failures ? 1 : 0;
}
