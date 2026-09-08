/* Managed 2D/volume uploads and GPU-generated 16-bit render-target readback.
 * Run with synchronous pipeline compilation. A skipped draw must fail. */
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(void *) == 4, "this regression must run as a 32-bit client");

#define UNORM(value, maximum) (((value) * 255 + (maximum) / 2) / (maximum))
#define CHECK(call) do { HRESULT hr = (call); if (FAILED(hr)) { \
    printf("FAIL: %s: %s hr=%08lx\n", name, #call, (unsigned long)hr); \
    goto done; } } while (0)

static const struct format_case {
    const char *name;
    D3DFORMAT format;
    DWORD packed;
    unsigned bytes;
    unsigned rgba[4];
} cases[] = {
    {"A8R8G8B8", D3DFMT_A8R8G8B8, 0x7953a6cf, 4, {83, 166, 207, 121}},
    {"A4R4G4B4", D3DFMT_A4R4G4B4, 0x7b3d, 2,
        {UNORM(11, 15), UNORM(3, 15), UNORM(13, 15), UNORM(7, 15)}},
    {"A1R5G5B5-alpha0", D3DFMT_A1R5G5B5, 0x2cf7, 2,
        {UNORM(11, 31), UNORM(7, 31), UNORM(23, 31), 0}},
    {"A1R5G5B5-alpha1", D3DFMT_A1R5G5B5, 0xacf7, 2,
        {UNORM(11, 31), UNORM(7, 31), UNORM(23, 31), 255}},
    {"R5G6B5", D3DFMT_R5G6B5, 0x3d73, 2,
        {UNORM(7, 31), UNORM(43, 63), UNORM(19, 31), 255}},
};

static const struct readback_case {
    const char *name;
    D3DFORMAT format;
    DWORD colors[3];
    WORD packed[3];
} readback_cases[] = {
    {"A4R4G4B4", D3DFMT_A4R4G4B4,
        {0x77bb33dd, 0x22ee8855, 0xdd4499aa}, {0x7b3d, 0x2e85, 0xd49a}},
    {"A1R5G5B5-alpha0", D3DFMT_A1R5G5B5,
        {0x005a3abd, 0x00bd5a3a, 0x003abd5a}, {0x2cf7, 0x5d67, 0x1eeb}},
    {"A1R5G5B5-alpha1", D3DFMT_A1R5G5B5,
        {0xff5a3abd, 0xffbd5a3a, 0xff3abd5a}, {0xacf7, 0xdd67, 0x9eeb}},
    {"R5G6B5", D3DFMT_R5G6B5,
        {0xff3aae9c, 0xff9c1c5a, 0xffbd7d19}, {0x3d73, 0x98eb, 0xbbe3}},
};

static int render_case(IDirect3DDevice9 *device, IDirect3DSurface9 *target,
                       IDirect3DSurface9 *readback, const struct format_case *test)
{
    const char *name = test->name;
    IDirect3DTexture9 *texture = NULL;
    D3DLOCKED_RECT lock;
    const struct vertex { float x, y, z, rhw, u, v; } quad[] = {
        {-0.5f, -0.5f, 0, 1, 0, 0}, {31.5f, -0.5f, 0, 1, 1, 0},
        {-0.5f, 31.5f, 0, 1, 0, 1}, {31.5f, 31.5f, 0, 1, 1, 1},
    };
    unsigned x, y, channel, actual[4];
    DWORD pixel;
    int failed = 1, scene = 0;
    ULONGLONG start = GetTickCount64();

    /* Odd width exercises the 16-bit upload's aligned row pitch. */
    CHECK(IDirect3DDevice9_CreateTexture(device, 3, 3, 1, 0, test->format,
                                        D3DPOOL_MANAGED, &texture, NULL));
    CHECK(IDirect3DTexture9_LockRect(texture, 0, &lock, NULL, 0));
    for (y = 0; y < 3; ++y)
        for (x = 0; x < 3; ++x)
            memcpy((BYTE *)lock.pBits + y * lock.Pitch + x * test->bytes,
                   &test->packed, test->bytes);
    CHECK(IDirect3DTexture9_UnlockRect(texture, 0));
    CHECK(IDirect3DDevice9_SetTexture(device, 0, (IDirect3DBaseTexture9 *)texture));
    CHECK(IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0x10203040, 1, 0));
    CHECK(IDirect3DDevice9_BeginScene(device));
    scene = 1;
    CHECK(IDirect3DDevice9_DrawPrimitiveUP(device, D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0])));
    CHECK(IDirect3DDevice9_EndScene(device));
    scene = 0;
    CHECK(IDirect3DDevice9_GetRenderTargetData(device, target, readback));
    CHECK(IDirect3DSurface9_LockRect(readback, &lock, NULL, D3DLOCK_READONLY));
    memcpy(&pixel, (BYTE *)lock.pBits + 16 * lock.Pitch + 16 * 4, sizeof(pixel));
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    actual[0] = (pixel >> 16) & 255;
    actual[1] = (pixel >> 8) & 255;
    actual[2] = pixel & 255;
    actual[3] = pixel >> 24;
    failed = 0;
    for (channel = 0; channel < 4; ++channel)
        if (abs((int)actual[channel] - (int)test->rgba[channel]) > 1) failed = 1;
    printf("%s: %s RGBA=%u,%u,%u,%u expected=%u,%u,%u,%u elapsed_ms=%llu\n",
           failed ? "FAIL" : "PASS", name, actual[0], actual[1], actual[2], actual[3],
           test->rgba[0], test->rgba[1], test->rgba[2], test->rgba[3], GetTickCount64() - start);
done:
    if (scene) IDirect3DDevice9_EndScene(device);
    IDirect3DDevice9_SetTexture(device, 0, NULL);
    if (texture) IDirect3DTexture9_Release(texture);
    return failed;
}

static int render_volume(IDirect3DDevice9 *device, IDirect3DSurface9 *target,
                         IDirect3DSurface9 *readback)
{
    const char *name = "volume-A4R4G4B4";
    const WORD packed[] = {0x7b3d, 0x2e85, 0xd49a};
    const DWORD expected[] = {0x77bb33dd, 0x22ee8855, 0xdd4499aa};
    IDirect3DVolumeTexture9 *texture = NULL;
    D3DLOCKED_BOX box;
    D3DLOCKED_RECT lock;
    struct vertex { float x, y, z, rhw, u, v, w; } quad[] = {
        {-0.5f, -0.5f, 0, 1, 0.5f, 0.5f, 0}, {31.5f, -0.5f, 0, 1, 0.5f, 0.5f, 0},
        {-0.5f, 31.5f, 0, 1, 0.5f, 0.5f, 0}, {31.5f, 31.5f, 0, 1, 0.5f, 0.5f, 0},
    };
    unsigned x, y, z, i, shift, mismatches = 0;
    DWORD pixel;
    int failed = 1, scene = 0;
    ULONGLONG start = GetTickCount64();

    CHECK(IDirect3DDevice9_CreateVolumeTexture(device, 4, 3, 3, 1, 0, D3DFMT_A4R4G4B4,
                                              D3DPOOL_MANAGED, &texture, NULL));
    CHECK(IDirect3DVolumeTexture9_LockBox(texture, 0, &box, NULL, 0));
    printf("INFO: %s size=4x3x3 RowPitch=%d SlicePitch=%d\n", name, box.RowPitch, box.SlicePitch);
    for (z = 0; z < 3; ++z)
        for (y = 0; y < 3; ++y)
            for (x = 0; x < 4; ++x)
                memcpy((BYTE *)box.pBits + z * box.SlicePitch + y * box.RowPitch + x * 2,
                       &packed[z], sizeof(packed[z]));
    CHECK(IDirect3DVolumeTexture9_UnlockBox(texture, 0));
    CHECK(IDirect3DDevice9_SetFVF(device, D3DFVF_XYZRHW | D3DFVF_TEX1 | D3DFVF_TEXCOORDSIZE3(0)));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 0, D3DSAMP_ADDRESSW, D3DTADDRESS_CLAMP));
    CHECK(IDirect3DDevice9_SetTexture(device, 0, (IDirect3DBaseTexture9 *)texture));
    for (z = 0; z < 3; ++z) {
        int mismatch = 0;
        for (i = 0; i < 4; ++i) quad[i].w = (z + 0.5f) / 3.0f;
        CHECK(IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0x10203040, 1, 0));
        CHECK(IDirect3DDevice9_BeginScene(device));
        scene = 1;
        CHECK(IDirect3DDevice9_DrawPrimitiveUP(device, D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0])));
        CHECK(IDirect3DDevice9_EndScene(device));
        scene = 0;
        CHECK(IDirect3DDevice9_GetRenderTargetData(device, target, readback));
        CHECK(IDirect3DSurface9_LockRect(readback, &lock, NULL, D3DLOCK_READONLY));
        memcpy(&pixel, (BYTE *)lock.pBits + 16 * lock.Pitch + 16 * 4, sizeof(pixel));
        CHECK(IDirect3DSurface9_UnlockRect(readback));
        for (shift = 0; shift < 32; shift += 8)
            if (abs((int)((pixel >> shift) & 255) - (int)((expected[z] >> shift) & 255)) > 1)
                mismatch = 1;
        mismatches += mismatch;
        printf("%s: %s slice=%u uploaded=%04x ARGB=%08lx expected=%08lx elapsed_ms=%llu\n",
               mismatch ? "FAIL" : "PASS", name, z, packed[z], pixel, expected[z], GetTickCount64() - start);
    }
    failed = mismatches != 0;
done:
    if (scene) IDirect3DDevice9_EndScene(device);
    IDirect3DDevice9_SetTexture(device, 0, NULL);
    if (texture) IDirect3DVolumeTexture9_Release(texture);
    return failed;
}

static int render_readback(IDirect3DDevice9 *device, IDirect3DSurface9 *original_target,
                           const struct readback_case *test)
{
    char name[64];
    IDirect3DSurface9 *target = NULL, *readback = NULL;
    D3DVIEWPORT9 viewport = {0, 0, 3, 3, 0, 1};
    D3DLOCKED_RECT lock;
    struct vertex { float x, y, z, rhw; DWORD diffuse; } quad[] = {
        {-0.5f, -0.5f, 0, 1, 0}, {2.5f, -0.5f, 0, 1, 0},
        {-0.5f, 2.5f, 0, 1, 0}, {2.5f, 2.5f, 0, 1, 0},
    };
    const WORD poison = 0xa55a; /* Different from every expected pixel. */
    unsigned i, x, y, mismatches = 0;
    WORD pixel, centres[3] = {0};
    int failed = 1, scene = 0;
    ULONGLONG start = GetTickCount64();

    snprintf(name, sizeof(name), "readback-%s", test->name);
    CHECK(IDirect3DDevice9_CreateRenderTarget(device, 3, 3, test->format,
          D3DMULTISAMPLE_NONE, 0, FALSE, &target, NULL));
    CHECK(IDirect3DDevice9_CreateOffscreenPlainSurface(device, 3, 3, test->format,
          D3DPOOL_SYSTEMMEM, &readback, NULL));
    CHECK(IDirect3DSurface9_LockRect(readback, &lock, NULL, 0));
    for (y = 0; y < 3; ++y)
        for (x = 0; x < 3; ++x)
            memcpy((BYTE *)lock.pBits + y * lock.Pitch + x * 2, &poison, sizeof(poison));
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    CHECK(IDirect3DDevice9_SetRenderTarget(device, 0, target));
    CHECK(IDirect3DDevice9_SetViewport(device, &viewport));
    CHECK(IDirect3DDevice9_SetTexture(device, 0, NULL));
    CHECK(IDirect3DDevice9_SetFVF(device, D3DFVF_XYZRHW | D3DFVF_DIFFUSE));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_COLORARG1, D3DTA_DIFFUSE));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_ALPHAARG1, D3DTA_DIFFUSE));
    CHECK(IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0, 1, 0));
    CHECK(IDirect3DDevice9_BeginScene(device));
    scene = 1;
    for (y = 0; y < 3; ++y) {
        for (i = 0; i < 4; ++i) {
            quad[i].y = y + (i < 2 ? -0.5f : 0.5f);
            quad[i].diffuse = test->colors[y];
        }
        CHECK(IDirect3DDevice9_DrawPrimitiveUP(device, D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0])));
    }
    CHECK(IDirect3DDevice9_EndScene(device));
    scene = 0;
    /* No CPU upload/lock of the render target: these bytes must come from the GPU.
     * Distinct rows catch pitch/permutation errors. Honor the returned pitch;
     * padding is not required, and only active pixels were poisoned. */
    CHECK(IDirect3DDevice9_GetRenderTargetData(device, target, readback));
    CHECK(IDirect3DSurface9_LockRect(readback, &lock, NULL, D3DLOCK_READONLY));
    for (y = 0; y < 3; ++y) {
        for (x = 0; x < 3; ++x) {
            memcpy(&pixel, (BYTE *)lock.pBits + y * lock.Pitch + x * 2, sizeof(pixel));
            if (x == 1) centres[y] = pixel;
            if (pixel != test->packed[y]) ++mismatches;
        }
    }
    CHECK(IDirect3DSurface9_UnlockRect(readback));
    failed = mismatches != 0;
    printf("%s: %s rows_packed16=%04x,%04x,%04x expected=%04x,%04x,%04x "
           "drawn_ARGB=%08lx,%08lx,%08lx poison=%04x mismatches=%u/9 pitch=%d elapsed_ms=%llu\n",
           failed ? "FAIL" : "PASS", name, centres[0], centres[1], centres[2],
           test->packed[0], test->packed[1], test->packed[2],
           test->colors[0], test->colors[1], test->colors[2], poison, mismatches, lock.Pitch,
           GetTickCount64() - start);
done:
    if (scene) IDirect3DDevice9_EndScene(device);
    IDirect3DDevice9_SetRenderTarget(device, 0, original_target);
    if (readback) IDirect3DSurface9_Release(readback);
    if (target) IDirect3DSurface9_Release(target);
    return failed;
}

int main(int argc, char **argv)
{
    const char *name = "setup";
    char module[32768];
    HMODULE dll = GetModuleHandleA("d3d9.dll");
    DWORD length = dll ? GetModuleFileNameA(dll, module, sizeof(module)) : 0;
    IDirect3D9 *d3d = NULL;
    IDirect3DDevice9 *device = NULL;
    IDirect3DSurface9 *target = NULL, *readback = NULL;
    D3DADAPTER_IDENTIFIER9 adapter;
    D3DPRESENT_PARAMETERS present = {0};
    D3DVIEWPORT9 viewport = {0, 0, 32, 32, 0, 1};
    WNDCLASSA window_class = {0};
    HWND window = NULL;
    unsigned i, failures = 0, count = 0;
    int result = 1;
    ULONGLONG start = GetTickCount64();

    setvbuf(stdout, NULL, _IONBF, 0);
    if (!length || length >= sizeof(module)) return 2;
    printf("INFO: d3d9 module=%s\n", module);
    if (argc != 2 || _stricmp(module, argv[1])) {
        puts("FAIL: expected exact app-local d3d9 module path argument");
        return 2;
    }
    window_class.lpfnWndProc = DefWindowProcA;
    window_class.hInstance = GetModuleHandleA(NULL);
    window_class.lpszClassName = "MacTMNFRendererProbe";
    if (!RegisterClassA(&window_class)) goto done;
    window = CreateWindowA(window_class.lpszClassName, "MacTMNF D3D9 format test",
                           WS_OVERLAPPEDWINDOW, 40, 40, 192, 128, NULL, NULL,
                           window_class.hInstance, NULL);
    if (!window) goto done;
    /* MoltenVK may need a visible NSView/CAMetalLayer even for offscreen work. */
    ShowWindow(window, SW_SHOWNOACTIVATE);
    UpdateWindow(window);
    d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) { puts("FAIL: Direct3DCreate9"); goto done; }
    CHECK(IDirect3D9_GetAdapterIdentifier(d3d, D3DADAPTER_DEFAULT, 0, &adapter));
    printf("INFO: D3D9 GPU=%s driver=%s vendor=%04lx device=%04lx\n",
           adapter.Description, adapter.Driver, adapter.VendorId, adapter.DeviceId);
    present.Windowed = TRUE;
    present.SwapEffect = D3DSWAPEFFECT_DISCARD;
    present.hDeviceWindow = window;
    present.BackBufferWidth = present.BackBufferHeight = 32;
    present.BackBufferFormat = D3DFMT_UNKNOWN;
    present.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    CHECK(IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, window,
          D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &present, &device));
    CHECK(IDirect3DDevice9_CreateRenderTarget(device, 32, 32, D3DFMT_A8R8G8B8,
          D3DMULTISAMPLE_NONE, 0, FALSE, &target, NULL));
    CHECK(IDirect3DDevice9_CreateOffscreenPlainSurface(device, 32, 32, D3DFMT_A8R8G8B8,
          D3DPOOL_SYSTEMMEM, &readback, NULL));
    CHECK(IDirect3DDevice9_SetRenderTarget(device, 0, target));
    CHECK(IDirect3DDevice9_SetViewport(device, &viewport));
    CHECK(IDirect3DDevice9_SetFVF(device, D3DFVF_XYZRHW | D3DFVF_TEX1));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_LIGHTING, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_ZENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_CULLMODE, D3DCULL_NONE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_ALPHABLENDENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_ALPHATESTENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_SRGBWRITEENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_DITHERENABLE, FALSE));
    CHECK(IDirect3DDevice9_SetRenderState(device, D3DRS_COLORWRITEENABLE, 15));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_COLOROP, D3DTOP_SELECTARG1));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_COLORARG1, D3DTA_TEXTURE));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_ALPHAOP, D3DTOP_SELECTARG1));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 0, D3DTSS_ALPHAARG1, D3DTA_TEXTURE));
    CHECK(IDirect3DDevice9_SetTextureStageState(device, 1, D3DTSS_COLOROP, D3DTOP_DISABLE));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 0, D3DSAMP_MINFILTER, D3DTEXF_POINT));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 0, D3DSAMP_MAGFILTER, D3DTEXF_POINT));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 0, D3DSAMP_MIPFILTER, D3DTEXF_NONE));
    CHECK(IDirect3DDevice9_SetSamplerState(device, 0, D3DSAMP_SRGBTEXTURE, FALSE));
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        failures += render_case(device, target, readback, &cases[i]);
        ++count;
    }
    failures += render_volume(device, target, readback);
    ++count;
    for (i = 0; i < sizeof(readback_cases) / sizeof(readback_cases[0]); ++i) {
        failures += render_readback(device, target, &readback_cases[i]);
        ++count;
    }
    printf("RESULT: cases=%u failures=%u elapsed_ms=%llu\n", count, failures, GetTickCount64() - start);
    result = failures ? 1 : 0;
done:
    if (readback) IDirect3DSurface9_Release(readback);
    if (target) IDirect3DSurface9_Release(target);
    if (device) IDirect3DDevice9_Release(device);
    if (d3d) IDirect3D9_Release(d3d);
    if (window) DestroyWindow(window);
    UnregisterClassA(window_class.lpszClassName, window_class.hInstance);
    return result;
}
