/* Regression for D3DX9 legacy function targets, verified against Microsoft SDKs 30 and 36.
 * Compare executable shader words with unqualified references, excluding comment metadata. */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3dx9.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef HRESULT (WINAPI *compile_fn)(const char *, UINT, const D3DXMACRO *, ID3DXInclude *, const char *, const char *, DWORD, ID3DXBuffer **, ID3DXBuffer **, ID3DXConstantTable **);
typedef HRESULT (WINAPI *disasm_fn)(const DWORD *, BOOL, const char *, ID3DXBuffer **);
static unsigned int failures, cases;
static DWORD compile_flags;
static compile_fn compile;
static disasm_fn disasm;
static int tokens(const DWORD *p, unsigned bytes, DWORD *out, unsigned capacity);

static void test_effect(HMODULE module, int wine_compiler)
{
    static const char source[] =
        "ps_2_0 float4 f():COLOR{return float4(1,0,0,1);}\n"
        "ps_3_0 float4 f():COLOR{return float4(0,1,0,1);}\n"
        "technique T {pass P {PixelShader=compile ps_2_0 f();}}";
    typedef HRESULT (WINAPI *create_effect_fn)(IDirect3DDevice9 *, const void *, UINT,
            const D3DXMACRO *, ID3DXInclude *, DWORD, ID3DXEffectPool *, ID3DXEffect **, ID3DXBuffer **);
    typedef IDirect3D9 *(WINAPI *create_d3d_fn)(UINT);
    HMODULE d3d_module = LoadLibraryA("d3d9.dll");
    create_d3d_fn create_d3d = d3d_module ? (create_d3d_fn)(void *)GetProcAddress(d3d_module, "Direct3DCreate9") : NULL;
    create_effect_fn load = (create_effect_fn)(void *)GetProcAddress(module, "D3DXCreateEffect");
    IDirect3D9 *d3d = NULL;
    IDirect3DDevice9 *device = NULL;
    ID3DXEffect *loaded = NULL;
    HWND window = NULL;
    D3DPRESENT_PARAMETERS pp = {0};
    ID3DXBuffer *errors = NULL, *reference = NULL;
    ID3DBlob *binary = NULL, *compile_errors = NULL;
    HMODULE wined3d = NULL;
    D3DXTECHNIQUE_DESC technique;
    D3DXPASS_DESC pass;
    DWORD a[4096], b[4096];
    int count, ok = 0;
    HRESULT hr = E_FAIL;
    ++cases;
    if (!load || !create_d3d || !(d3d = create_d3d(D3D_SDK_VERSION))) goto done;
    window = CreateWindowA("STATIC", "Effect regression", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, NULL, NULL, NULL, NULL);
    pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = window;
    if (FAILED(IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, window,
            D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &device))) goto done;
    if (errors) {ID3DXBuffer_Release(errors); errors = NULL;}
    if (wine_compiler)
    {
        typedef HRESULT (WINAPI *compile2_fn)(const void *, SIZE_T, const char *,
                const D3D_SHADER_MACRO *, ID3DInclude *, const char *, const char *, UINT,
                UINT, UINT, const void *, SIZE_T, ID3DBlob **, ID3DBlob **, unsigned int);
        compile2_fn compile2;
        char path[MAX_PATH];
        wined3d = LoadLibraryA("wined3d.dll");
        compile2 = wined3d ? (compile2_fn)(void *)GetProcAddress(wined3d, "vkd3d_D3DCompile2VKD3D") : NULL;
        if (!compile2) goto done;
        if (!GetModuleFileNameA(wined3d, path, sizeof(path))) goto done;
        printf("INFO: wined3d module=%s\n", path);
        hr = compile2(source, sizeof(source)-1, NULL, NULL, NULL, NULL, "fx_2_0",
                0, 0, 0, NULL, 0, &binary, &compile_errors, 30);
        if (FAILED(hr)) goto done;
    }
    hr = load(device, binary ? ID3D10Blob_GetBufferPointer(binary) : source,
            binary ? ID3D10Blob_GetBufferSize(binary) : sizeof(source) - 1,
            NULL, NULL, compile_flags, NULL, &loaded, &errors);
    if (FAILED(hr)) goto done;
    if (FAILED(loaded->lpVtbl->GetTechniqueDesc(loaded,
            loaded->lpVtbl->GetTechnique(loaded, 0), &technique)) || technique.Passes != 1) goto done;
    if (FAILED(loaded->lpVtbl->GetPassDesc(loaded,
            loaded->lpVtbl->GetPass(loaded, loaded->lpVtbl->GetTechnique(loaded, 0), 0), &pass))) goto done;
    if (!pass.pPixelShaderFunction) goto done;
    /* GetPassDesc returns shader bytecode; D3DXGetShaderSize is not statically linked. */
    {
        typedef UINT (WINAPI *size_fn)(const DWORD *);
        size_fn size = (size_fn)(void *)GetProcAddress(module, "D3DXGetShaderSize");
        const char ref[] = "float4 main():COLOR{return float4(1,0,0,1);}";
        if (!size || FAILED(compile(ref, sizeof(ref)-1, NULL, NULL, "main", "ps_2_0",
                compile_flags, &reference, NULL, NULL))) goto done;
        count = tokens(pass.pPixelShaderFunction, size(pass.pPixelShaderFunction), a, 4096);
        ok = count > 0 && tokens(ID3DXBuffer_GetBufferPointer(reference),
                ID3DXBuffer_GetBufferSize(reference), b, 4096) == count && !memcmp(a, b, count * sizeof(*a));
    }
done:
    printf("%s: effect explicit ps_2_0 target hr=%08lx reflected_output=%d\n", ok ? "PASS" : "FAIL", hr, ok);
    if (!ok) ++failures;
    if (errors) {puts(ID3DXBuffer_GetBufferPointer(errors)); ID3DXBuffer_Release(errors);}
    if (compile_errors) {puts(ID3D10Blob_GetBufferPointer(compile_errors)); ID3D10Blob_Release(compile_errors);}
    if (binary) ID3D10Blob_Release(binary);
    if (reference) ID3DXBuffer_Release(reference);
    if (loaded) loaded->lpVtbl->Release(loaded);
    if (device) IDirect3DDevice9_Release(device);
    if (d3d) IDirect3D9_Release(d3d);
    if (window) DestroyWindow(window);
    if (d3d_module) FreeLibrary(d3d_module);
    if (wined3d) FreeLibrary(wined3d);
}
#define RED "float4 main():COLOR{return float4(1,0,0,1);}\n"
#define GREEN "float4 main():COLOR{return float4(0,1,0,1);}\n"
#define BLUE "float4 main():COLOR{return float4(0,0,1,1);}\n"
#define POSITION "float4 main(float4 p:POSITION):POSITION{return p;}\n"
#define SCALED_POSITION "float4 main(float4 p:POSITION):POSITION{return p*2;}\n"
#define NEGATED_POSITION "float4 main(float4 p:POSITION):POSITION{return -p;}\n"
/* SM2/3 encode operand counts. SM1 vertex instructions need an opcode table.
 * Only inspect opcodes at instruction boundaries: DEF immediates can end in
 * 0xfffe too, and must not be mistaken for comment tokens. */
static int tokens(const DWORD *p, unsigned bytes, DWORD *out, unsigned capacity)
{
    unsigned n = bytes / sizeof(*p), count = 0, i = 1, major;
    if (bytes % sizeof(*p) || n < 2 || !capacity) return -1;
    if ((p[0] >> 16) != 0xfffe && (p[0] >> 16) != 0xffff) return -1;
    major = (p[0] >> 8) & 0xff;
    if (major < 1 || major > 3) return -1;
    if (major == 1 && p[0] != D3DVS_VERSION(1, 1)) return -1;
    out[count++] = p[0];
    while (i < n)
    {
        unsigned opcode = p[i] & D3DSI_OPCODE_MASK, length;
        if (opcode == D3DSIO_COMMENT)
        {
            length = 1 + ((p[i] >> 16) & 0x7fff);
            if (length > n - i) return -1;
            i += length;
            continue;
        }
        if (opcode == D3DSIO_END)
        {
            if (p[i] != D3DSIO_END || i != n - 1 || count >= capacity) return -1;
            out[count++] = p[i];
            return count;
        }
        if (major >= 2) length = 1 + ((p[i] & D3DSI_INSTLENGTH_MASK) >> D3DSI_INSTLENGTH_SHIFT);
        else
        {
            switch (opcode)
            {
                case D3DSIO_NOP: length = 1; break;
                case D3DSIO_DCL: length = 3; break;
                case D3DSIO_MOV: case D3DSIO_RCP: case D3DSIO_RSQ:
                case D3DSIO_EXP: case D3DSIO_LOG: case D3DSIO_LIT:
                case D3DSIO_FRC: case D3DSIO_EXPP: case D3DSIO_LOGP:
                    length = 3; break;
                case D3DSIO_ADD: case D3DSIO_SUB: case D3DSIO_MUL:
                case D3DSIO_DP3: case D3DSIO_DP4: case D3DSIO_MIN:
                case D3DSIO_MAX: case D3DSIO_SLT: case D3DSIO_SGE:
                case D3DSIO_DST: case D3DSIO_M4x4: case D3DSIO_M4x3:
                case D3DSIO_M3x4: case D3DSIO_M3x3: case D3DSIO_M3x2:
                    length = 4; break;
                case D3DSIO_MAD: length = 5; break;
                case D3DSIO_DEF: length = 6; break;
                default: return -1;
            }
        }
        if (length > n - i || length > capacity - count) return -1;
        memcpy(out + count, p + i, length * sizeof(*p));
        count += length;
        i += length;
    }
    return -1; /* Missing END. */
}

static int test_tokens(void)
{
    DWORD plain[] = {D3DPS_VERSION(2, 0), 0x05000051, 0xa00f0000,
                     0x0001fffe, 0, 0, 0x3f800000, D3DSIO_END};
    DWORD commented[] = {D3DPS_VERSION(2, 0), 0x0002fffe, D3DSIO_END, 0x0000fffe,
                         0x05000051, 0xa00f0000, 0x0001fffe, 0, 0, 0x3f800000,
                         0x0001fffe, 0x12345678, D3DSIO_END};
    DWORD bad_comment[] = {D3DPS_VERSION(2, 0), 0x0003fffe, D3DSIO_END};
    DWORD bad_instruction[] = {D3DPS_VERSION(3, 0), 0x05000051, D3DSIO_END};
    /* Native SDK 30 and Wine emit the same DCL/MOV words for POSITION. */
    DWORD position[] = {D3DVS_VERSION(1, 1), D3DSIO_DCL, 0x80000000, 0x900f0000,
                       D3DSIO_MOV, 0xc00f0000, 0x90e40000, D3DSIO_END};
    const DWORD versions[] = {D3DVS_VERSION(1, 1), D3DVS_VERSION(2, 0),
                             D3DPS_VERSION(2, 0), D3DVS_VERSION(3, 0), D3DPS_VERSION(3, 0)};
    DWORD a[16], b[16];
    int count = tokens(plain, sizeof(plain), a, 16);
    if (count != 8 || memcmp(a, plain, sizeof(plain)) ||
        tokens(commented, sizeof(commented), b, 16) != count ||
        memcmp(a, b, count * sizeof(*a)) ||
        tokens(plain, sizeof(plain), b, 7) != -1 ||
        tokens(plain, sizeof(plain) - 1, b, 16) != -1 ||
        tokens(plain, sizeof(plain) - sizeof(DWORD), b, 16) != -1 ||
        tokens(bad_comment, sizeof(bad_comment), b, 16) != -1 ||
        tokens(bad_instruction, sizeof(bad_instruction), b, 16) != -1) return 1;
    plain[0] = D3DVS_VERSION(1, 1);
    plain[1] = D3DSIO_DEF;
    if (tokens(plain, sizeof(plain), a, 16) != 8 || memcmp(a, plain, sizeof(plain))) return 1;
    if (tokens(position, sizeof(position), a, 16) != 8 || memcmp(a, position, sizeof(position))) return 1;
    position[6] = 0x91e40000; /* The stage-fallback reference is MOV oPos, -v0. */
    if (tokens(position, sizeof(position), b, 16) != 8 || memcmp(b, position, sizeof(position)) ||
        !memcmp(a, b, sizeof(position))) return 1;
    for (unsigned i = 0; i < sizeof(versions) / sizeof(*versions); ++i)
    {
        /* DCL = opcode + semantic + register; DEF = opcode + register + four
         * literals. SM1 omits the length field; SM2/3 encode operand counts.
         * Comment/END-looking DEF literals must not interrupt the walk. */
        DWORD sequence[] = {versions[i], D3DSIO_DCL, 0x80000005, 0x900f0001,
                            D3DSIO_DEF, 0xa00f0000, 0x0001fffe, D3DSIO_END, 0, 0x3f800000,
                            D3DSIO_MOV, 0x800f0000, 0xa0e40000, D3DSIO_END};
        if (i)
        {
            sequence[1] |= 2u << D3DSI_INSTLENGTH_SHIFT;
            sequence[4] |= 5u << D3DSI_INSTLENGTH_SHIFT;
            sequence[10] |= 2u << D3DSI_INSTLENGTH_SHIFT;
        }
        if (tokens(sequence, sizeof(sequence), a, 16) != 14 || memcmp(a, sequence, sizeof(sequence)) ||
            tokens(sequence, sizeof(sequence), a, 13) != -1) return 1;
        /* Includes every partial DCL/DEF and the complete stream missing END. */
        for (unsigned bytes = 0; bytes < sizeof(sequence); bytes += sizeof(DWORD))
            if (tokens(sequence, bytes, a, 16) != -1) return 1;
    }
    position[0] = D3DPS_VERSION(1, 1); /* This parser intentionally supports only vertex SM1. */
    if (tokens(position, sizeof(position), a, 16) != -1) return 1;
    puts("PASS: bounded binary shader token parser");
    return 0;
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
        int ac = tokens(ID3DXBuffer_GetBufferPointer(b), ID3DXBuffer_GetBufferSize(b), a, 4096);
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
                if (ac >= 0 && tokens(ID3DXBuffer_GetBufferPointer(ref), ID3DXBuffer_GetBufferSize(ref),
                                     c, 4096) == ac && !memcmp(a, c, ac * sizeof(*a))) found = i;
                ID3DXBuffer_Release(ref);
            }
        }
        actual = found;
        if (found < 0) ++failures;
        printf(" output=%d", found);
        if (found < 0 && ac >= 0 && disasm)
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
    HMODULE m;
    if (test_tokens()) {fputs("FAIL: shader token parser\n", stderr); return 1;}
    if (argc > 1 && !strcmp(argv[1], "--tokens-only")) return 0;
    m = LoadLibraryA(argc > 1 ? argv[1] : "d3dx9_30.dll");
    if (!m) return 2;
    compile = (compile_fn)(void *)GetProcAddress(m, "D3DXCompileShader");
    disasm = (disasm_fn)(void *)GetProcAddress(m, "D3DXDisassembleShader");
    if (!compile) return 2;
    if (argc > 2 && !strcmp(argv[2], "legacy-flag"))
        compile_flags = D3DXSHADER_USE_LEGACY_D3DX9_31_DLL;
    if (argc > 2 && (!strcmp(argv[2], "effect-only") || !strcmp(argv[2], "effect-wine")))
    {
        test_effect(m, !strcmp(argv[2], "effect-wine"));
        FreeLibrary(m);
        return failures ? 1 : 0;
    }
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
