/* Window-owning session fixture, no Wine runtime needed to build.
 * The first window in a prefix without a virtual desktop makes win32u start
 * explorer.exe /desktop inside the caller's job. Compile with -mwindows so no
 * console host takes part. The command line is an optional lifetime in ms
 * (default 1500, at most 60000); the fixture then destroys its window and
 * exits 0. */
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <stdlib.h>

static LRESULT CALLBACK fixture_window(HWND window, UINT message, WPARAM w, LPARAM l)
{
    if (message == WM_TIMER) { DestroyWindow(window); return 0; }
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, w, l);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR command, int show)
{
    WNDCLASSW class = {0};
    MSG message;
    HWND window;
    unsigned long lifetime = 1500;
    (void)previous;
    (void)show;
    if (command && *command)
    {
        WCHAR *end;
        lifetime = wcstoul(command, &end, 10);
        if (*end || !lifetime || lifetime > 60000) return 3;
    }
    class.lpfnWndProc = fixture_window;
    class.hInstance = instance;
    class.lpszClassName = L"mactmnf session gui fixture";
    if (!RegisterClassW(&class)) return 4;
    window = CreateWindowW(class.lpszClassName, L"session gui fixture", WS_OVERLAPPEDWINDOW,
            10, 10, 200, 100, NULL, NULL, instance, NULL);
    if (!window) return 5;
    ShowWindow(window, SW_SHOWNORMAL);
    if (!SetTimer(window, 1, (UINT)lifetime, NULL)) return 6;
    while (GetMessageW(&message, NULL, 0, 0) > 0)
    {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return (int)message.wParam;
}
