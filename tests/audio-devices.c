#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <mmdeviceapi.h>
#include <stdio.h>
#include <string.h>

/* Exercise Wine's real audio backend, which bypasses WINEDLLOVERRIDES. */
int main(int argc, char **argv)
{
    IMMDeviceEnumerator *enumerator = NULL;
    IMMDeviceCollection *devices = NULL;
    UINT count = 0;
    HRESULT hr;
    int enabled;

    if (argc != 2 || (strcmp(argv[1], "off") && strcmp(argv[1], "on")))
        return 2;
    enabled = !strcmp(argv[1], "on");
    hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(hr)) return 2;
    hr = CoCreateInstance(&CLSID_MMDeviceEnumerator, NULL, CLSCTX_INPROC_SERVER,
                         &IID_IMMDeviceEnumerator, (void **)&enumerator);
    if (SUCCEEDED(hr))
        hr = IMMDeviceEnumerator_EnumAudioEndpoints(enumerator, eRender,
                                                   DEVICE_STATE_ACTIVE, &devices);
    if (SUCCEEDED(hr)) hr = IMMDeviceCollection_GetCount(devices, &count);
    printf("Audio %s: %u active playback devices; result 0x%08lx\n", argv[1], count, hr);
    if (devices) IMMDeviceCollection_Release(devices);
    if (enumerator) IMMDeviceEnumerator_Release(enumerator);
    CoUninitialize();
    if (FAILED(hr)) return 2;
    return (count > 0) != enabled;
}
