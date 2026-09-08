/* Raw ps_1_1 bytecode reaches D9VK's DXSO translator, not Wine's HLSL compiler.
 * Nearest volume sampling observes |E|; cube faces also check non-unit N. */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#ifdef VKD3D_TRANSLATION_PROBE
#include <vkd3d_shader.h>
#endif

#define CHECK(call) do { HRESULT hr = (call); if (FAILED(hr)) { \
    printf("FAIL: %s hr=%08lx\n", #call, hr); goto done; } } while (0)

static const DWORD shader[] = {
    0xffff0101, D3DSIO_TEXCOORD, 0xb00f0000,
    D3DSIO_TEXM3x3PAD, 0xb00f0001, 0xb0e40000,
    D3DSIO_TEXM3x3PAD, 0xb00f0002, 0xb0e40000,
    D3DSIO_TEXM3x3VSPEC, 0xb00f0003, 0xb0e40000,
    D3DSIO_MOV, 0x800f0000, 0xb0e40003, D3DSIO_END
};

#ifdef VKD3D_TRANSLATION_PROBE
static int translate(void)
{
    typedef int (__cdecl *compile_fn)(const struct vkd3d_shader_compile_info *, struct vkd3d_shader_code *, char **);
    typedef void (__cdecl *free_code_fn)(struct vkd3d_shader_code *);
    typedef void (__cdecl *free_messages_fn)(char *);
    HMODULE module = LoadLibraryA("wined3d.dll");
    compile_fn compile = module ? (compile_fn)(void *)GetProcAddress(module, "vkd3d_shader_compile") : NULL;
    free_code_fn free_code = module ? (free_code_fn)(void *)GetProcAddress(module, "vkd3d_shader_free_shader_code") : NULL;
    free_messages_fn free_messages = module ? (free_messages_fn)(void *)GetProcAddress(module, "vkd3d_shader_free_messages") : NULL;
    unsigned cube, failures = 0;
    char path[MAX_PATH];
    if (!compile || !free_code || !free_messages) return 2;
    if (!GetModuleFileNameA(module, path, sizeof(path))) return 2;
    printf("INFO: wined3d module=%s\n", path);
    for (cube = 0; cube < 2; ++cube)
    {
        struct vkd3d_shader_d3dbc_source_info source = {0};
        struct vkd3d_shader_compile_info info = {0};
        struct vkd3d_shader_code output = {0};
        char *messages = NULL;
        const DWORD *words;
        size_t i, count;
        unsigned normalize = 0;
        int ret;
        source.type = VKD3D_SHADER_STRUCTURE_TYPE_D3DBC_SOURCE_INFO;
        source.texture_dimensions[3] = cube ? VKD3D_SHADER_RESOURCE_TEXTURE_CUBE : VKD3D_SHADER_RESOURCE_TEXTURE_3D;
        info.type = VKD3D_SHADER_STRUCTURE_TYPE_COMPILE_INFO;
        info.next = &source;
        info.source.code = shader; info.source.size = sizeof(shader);
        info.source_type = VKD3D_SHADER_SOURCE_D3D_BYTECODE;
        info.target_type = VKD3D_SHADER_TARGET_SPIRV_BINARY;
        info.log_level = VKD3D_SHADER_LOG_INFO;
        ret = compile(&info, &output, &messages);
        if (messages) {puts(messages); free_messages(messages);}
        words = output.code; count = output.size / sizeof(*words);
        /* Bounded SPIR-V instruction walk. NRM lowers to dot/RSQ/mul;
         * GLSL.std.450 InverseSqrt is 32.
         * This checks lowering, not execution of Wine's rendered shader. */
        i = 5;
        if (ret >= 0 && count >= 5 && words[0] == 0x07230203)
            while (i < count)
            {
                unsigned length = words[i] >> 16;
                if (!length || length > count - i) break;
                if ((words[i] & 0xffff) == 12 && length >= 6 && words[i+4] == 32) ++normalize;
                i += length;
            }
        if (ret < 0 || i != count || normalize != 1) ++failures;
        printf("%s: Wine translation %s ret=%d InverseSqrt=%u expected=1\n",
                ret >= 0 && i == count && normalize == 1 ? "PASS" : "FAIL", cube ? "cube" : "volume", ret, normalize);
        free_code(&output);
    }
    FreeLibrary(module);
    return failures ? 1 : 0;
}
#endif

static DWORD color(unsigned x, unsigned y, unsigned z)
{
    return 0xff000000 | ((16 + 28*x) << 16) | ((16 + 28*y) << 8) | (16 + 28*z);
}

static int render(IDirect3DDevice9 *device, const float *n, const float *e, int cube, unsigned index)
{
    struct vertex {float position[4], t[4][4];} vertices[4] = {0};
    IDirect3DVolumeTexture9 *volume = NULL;
    IDirect3DCubeTexture9 *cubemap = NULL;
    IDirect3DSurface9 *target = NULL, *readback = NULL;
    IDirect3DPixelShader9 *ps = NULL;
    D3DLOCKED_RECT rect;
    float r[3], dot = 0, square = 0;
    DWORD expected, actual;
    unsigned x, y, z, i, face;
    int failed = 1, scene = 0;
    for (i = 0; i < 3; ++i) {dot += n[i]*e[i]; square += n[i]*n[i];}
    for (i = 0; i < 3; ++i) r[i] = 2*dot/square*n[i] - e[i];
    if (cube)
    {
        unsigned axis = 0;
        for (i = 1; i < 3; ++i) if (fabsf(r[i]) > fabsf(r[axis])) axis = i;
        face = 2*axis + (r[axis] < 0);
        expected = color(face, 1, 2);
        CHECK(IDirect3DDevice9_CreateCubeTexture(device, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &cubemap, NULL));
        for (face = 0; face < 6; ++face)
        {
            CHECK(IDirect3DCubeTexture9_LockRect(cubemap, face, 0, &rect, NULL, 0));
            for (y = 0; y < 8; ++y) for (x = 0; x < 8; ++x)
                ((DWORD *)((BYTE *)rect.pBits + y*rect.Pitch))[x] = color(face, 1, 2);
            CHECK(IDirect3DCubeTexture9_UnlockRect(cubemap, face, 0));
        }
        CHECK(IDirect3DDevice9_SetTexture(device, 3, (IDirect3DBaseTexture9 *)cubemap));
    }
    else
    {
        D3DLOCKED_BOX box;
        expected = color((unsigned)(r[0]*8), (unsigned)(r[1]*8), (unsigned)(r[2]*8));
        CHECK(IDirect3DDevice9_CreateVolumeTexture(device, 8, 8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &volume, NULL));
        CHECK(IDirect3DVolumeTexture9_LockBox(volume, 0, &box, NULL, 0));
        for (z = 0; z < 8; ++z) for (y = 0; y < 8; ++y) for (x = 0; x < 8; ++x)
            ((DWORD *)((BYTE *)box.pBits + z*box.SlicePitch + y*box.RowPitch))[x] = color(x, y, z);
        CHECK(IDirect3DVolumeTexture9_UnlockBox(volume, 0));
        CHECK(IDirect3DDevice9_SetTexture(device, 3, (IDirect3DBaseTexture9 *)volume));
    }
    for (i = 0; i < 4; ++i)
    {
        vertices[i].position[0] = (i & 1) ? 31.5f : -0.5f;
        vertices[i].position[1] = (i & 2) ? 31.5f : -0.5f;
        vertices[i].position[3] = 1;
        vertices[i].t[0][0] = 1;
        for (x = 0; x < 3; ++x) {vertices[i].t[x+1][0] = n[x]; vertices[i].t[x+1][3] = e[x];}
    }
    CHECK(IDirect3DDevice9_SetFVF(device, D3DFVF_XYZRHW | D3DFVF_TEX4 |
            D3DFVF_TEXCOORDSIZE4(0) | D3DFVF_TEXCOORDSIZE4(1) | D3DFVF_TEXCOORDSIZE4(2) | D3DFVF_TEXCOORDSIZE4(3)));
    CHECK(IDirect3DDevice9_CreatePixelShader(device, shader, &ps));
    CHECK(IDirect3DDevice9_SetPixelShader(device, ps));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_CULLMODE, D3DCULL_NONE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_ZENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 3, D3DSAMP_MINFILTER, D3DTEXF_POINT));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 3, D3DSAMP_MAGFILTER, D3DTEXF_POINT));
    CHECK(IDirect3DDevice9_GetRenderTarget(device, 0, &target));
    CHECK(IDirect3DDevice9_CreateOffscreenPlainSurface(device, 32, 32, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &readback, NULL));
    CHECK(IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0xffabcdef, 1, 0));
    CHECK(IDirect3DDevice9_BeginScene(device)); scene = 1;
    CHECK(IDirect3DDevice9_DrawPrimitiveUP(device, D3DPT_TRIANGLESTRIP, 2, vertices, sizeof(vertices[0])));
    CHECK(IDirect3DDevice9_EndScene(device)); scene = 0;
    CHECK(IDirect3DDevice9_GetRenderTargetData(device, target, readback));
    CHECK(IDirect3DSurface9_LockRect(readback, &rect, NULL, D3DLOCK_READONLY));
    memcpy(&actual, (BYTE *)rect.pBits + 16*rect.Pitch + 16*4, 4);
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    failed = actual != expected;
    printf("%s: %s case=%u R=%.4f,%.4f,%.4f actual=%08lx expected=%08lx\n",
            failed ? "FAIL" : "PASS", cube ? "cube" : "volume", index, r[0], r[1], r[2], actual, expected);
done:
    if (scene) IDirect3DDevice9_EndScene(device);
    IDirect3DDevice9_SetTexture(device, 3, NULL);
    IDirect3DDevice9_SetPixelShader(device, NULL);
    if (ps) IDirect3DPixelShader9_Release(ps);
    if (readback) IDirect3DSurface9_Release(readback);
    if (target) IDirect3DSurface9_Release(target);
    if (volume) IDirect3DVolumeTexture9_Release(volume);
    if (cubemap) IDirect3DCubeTexture9_Release(cubemap);
    return failed;
}

int main(int argc, char **argv)
{
    const float normals[][3] = {{0,0,1}, {0,0,2}, {2,1,0}};
    const float eyes[][3] = {{-.25f,-.375f,.5625f}, {-.25f,-.375f,.5625f}, {.5f,-.25f,-.375f}};
    IDirect3D9 *d3d;
    IDirect3DDevice9 *device = NULL;
    HWND window;
    D3DPRESENT_PARAMETERS pp = {0};
    unsigned failures = 0, i;
#ifdef VKD3D_TRANSLATION_PROBE
    if (argc == 2 && !strcmp(argv[1], "--translate")) return translate();
#endif
    (void)argv;
    if (argc != 1) return 2;
    {
        char path[MAX_PATH];
        if (!GetModuleFileNameA(GetModuleHandleA("d3d9.dll"), path, sizeof(path))) return 2;
        printf("INFO: d3d9 module=%s\n", path);
    }
    d3d = Direct3DCreate9(D3D_SDK_VERSION);
    window = CreateWindowA("STATIC", "Legacy shader regression", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, NULL, NULL, NULL, NULL);
    if (!d3d || !window) return 2;
    pp.Windowed = TRUE; pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = window;
    pp.BackBufferWidth = pp.BackBufferHeight = 32; pp.BackBufferFormat = D3DFMT_A8R8G8B8;
    if (FAILED(IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, window, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &device))) return 2;
    for (i = 0; i < 3; ++i) {failures += render(device, normals[i], eyes[i], 0, i); failures += render(device, normals[i], eyes[i], 1, i);}
    printf("RESULT: cases=6 failures=%u\n", failures);
    IDirect3DDevice9_Release(device); IDirect3D9_Release(d3d); DestroyWindow(window);
    return failures ? 1 : 0;
}
