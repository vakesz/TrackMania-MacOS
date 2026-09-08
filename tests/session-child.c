/* Controlled session fixture, no Wine runtime needed to build.
 * --exit N                 final unsigned Windows status (default 0)
 * --sleep MS               delay before the selected action (default 0)
 * --hold                   hold after spawning for 10 seconds, or --sleep MS;
 *                          without spawning, just hold for that duration
 * --crash                  RaiseException(0xe0424242), without a debugger dialog
 * --spawn PATH DELAY ROOTEXIT FLAGS
 *                          spawn PATH after DELAY ms, then exit ROOTEXIT;
 *                          FLAGS is decimal/hex CreateProcess flags (0 normally)
 * --handoff PATH           shorthand for --spawn PATH 100 0 0
 * --log-args PATH          write original argv as UTF-8 JSON, including argv[0]
 * -- [ARGS...]             child argv for spawn/handoff; otherwise ignored so
 *                          arbitrary quoting cases can be logged
 * Default child argv is --sleep 250 --exit 0. All delays are <= 60000 ms.
 * A requested CREATE_SUSPENDED child is resumed before this fixture exits.
 */
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#define CAP 32768

static int number(const WCHAR *arg, DWORD *value)
{
    uint64_t n = 0;
    unsigned int base = 10;
    if (arg[0] == L'0' && (arg[1] == L'x' || arg[1] == L'X')) { base = 16; arg += 2; }
    if (!*arg) return 0;
    for (; *arg; ++arg)
    {
        unsigned int digit;
        if (*arg >= L'0' && *arg <= L'9') digit = *arg - L'0';
        else if (*arg >= L'a' && *arg <= L'f') digit = *arg - L'a' + 10;
        else if (*arg >= L'A' && *arg <= L'F') digit = *arg - L'A' + 10;
        else return 0;
        if (digit >= base) return 0;
        n = n * base + digit;
        if (n > MAXDWORD) return 0;
    }
    *value = (DWORD)n;
    return 1;
}

static WCHAR *path_name(const WCHAR *arg)
{
    union { FARPROC proc; WCHAR *(__cdecl *dos_name)(const char *); } wine;
    WCHAR *converted = NULL, *full;
    DWORD length;
    if (*arg == L'/')
    {
        char *utf8;
        int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, arg, -1, NULL, 0, NULL, NULL);
        wine.proc = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "wine_get_dos_file_name");
        if (!bytes || !wine.proc || !(utf8 = malloc((size_t)bytes))) return NULL;
        if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, arg, -1, utf8, bytes, NULL, NULL))
            converted = wine.dos_name(utf8);
        free(utf8);
        if (!converted) return NULL;
        arg = converted;
    }
    full = malloc(CAP * sizeof(*full));
    length = full ? GetFullPathNameW(arg, CAP, full, NULL) : 0;
    if (converted) HeapFree(GetProcessHeap(), 0, converted);
    if (!length || length >= CAP) { free(full); return NULL; }
    return full;
}

static int quote(WCHAR *command, size_t *used, const WCHAR *arg)
{
    size_t slashes;
#define APPEND(c) do { if (*used >= CAP - 2) return 0; command[(*used)++] = (c); } while (0)
    if (*used) APPEND(L' ');
    if (*arg && !wcspbrk(arg, L" \t\n\v\""))
    {
        while (*arg) APPEND(*arg++);
        command[*used] = 0;
        return 1;
    }
    APPEND(L'"');
    for (;;)
    {
        for (slashes = 0; *arg == L'\\'; ++arg) ++slashes;
        if (!*arg || *arg == L'"') slashes *= 2;
        while (slashes--) APPEND(L'\\');
        if (!*arg) break;
        if (*arg == L'"') APPEND(L'\\');
        APPEND(*arg++);
    }
    APPEND(L'"');
    command[*used] = 0;
#undef APPEND
    return 1;
}

static int log_args(const WCHAR *path, int argc, WCHAR **argv)
{
    WCHAR *full = path_name(path);
    FILE *file = full ? _wfopen(full, L"wb") : NULL;
    int i, ok = 1;
    free(full);
    if (!file) return 0;
    fputs("[", file);
    for (i = 0; i < argc; ++i)
    {
        int j, bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, NULL, 0, NULL, NULL);
        char *utf8 = bytes ? malloc((size_t)bytes) : NULL;
        if (!utf8 || !WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, utf8, bytes, NULL, NULL))
        { free(utf8); ok = 0; break; }
        if (i) fputc(',', file);
        fputc('"', file);
        for (j = 0; j < bytes - 1; ++j)
        {
            unsigned char c = (unsigned char)utf8[j];
            if (c == '"' || c == '\\') { fputc('\\', file); fputc(c, file); }
            else if (c < 32) fprintf(file, "\\u%04x", (unsigned int)c);
            else fputc(c, file);
        }
        fputc('"', file);
        free(utf8);
    }
    fputs("]\n", file);
    if (ferror(file)) ok = 0;
    if (fclose(file)) ok = 0;
    return ok;
}

static LONG WINAPI crashed(EXCEPTION_POINTERS *exception)
{
    ExitProcess(exception->ExceptionRecord->ExceptionCode);
    return EXCEPTION_EXECUTE_HANDLER;
}

int wmain(int argc, WCHAR **argv)
{
    DWORD exit_code = 0, sleep_ms = 0, delay = 0, flags = 0;
    const WCHAR *spawn = NULL, *log = NULL;
    int i, hold = 0, crash = 0, have_sleep = 0, extra = argc;
    for (i = 1; i < argc; ++i)
    {
        if (!wcscmp(argv[i], L"--")) { extra = i + 1; break; }
        if (!wcscmp(argv[i], L"--hold")) hold = 1;
        else if (!wcscmp(argv[i], L"--crash")) crash = 1;
        else if (!wcscmp(argv[i], L"--exit") && i + 1 < argc)
        { if (!number(argv[++i], &exit_code)) goto usage; }
        else if (!wcscmp(argv[i], L"--sleep") && i + 1 < argc)
        {
            if (!number(argv[++i], &sleep_ms) || sleep_ms > 60000) goto usage;
            have_sleep = 1;
        }
        else if (!wcscmp(argv[i], L"--log-args") && i + 1 < argc) log = argv[++i];
        else if (!wcscmp(argv[i], L"--handoff") && i + 1 < argc && !spawn)
        { spawn = argv[++i]; delay = 100; exit_code = 0; }
        else if (!wcscmp(argv[i], L"--spawn") && i + 4 < argc && !spawn)
        {
            spawn = argv[++i];
            if (!number(argv[++i], &delay) || delay > 60000 || !number(argv[++i], &exit_code) ||
                    !number(argv[++i], &flags) || (flags & (DEBUG_PROCESS | DEBUG_ONLY_THIS_PROCESS))) goto usage;
        }
        else goto usage;
    }
    if (log && !log_args(log, argc, argv)) { fputs("fixture: argument log failed\n", stderr); return 2; }
    fprintf(stderr, "fixture: pid=%lu started\n", GetCurrentProcessId());
    if (!hold) Sleep(sleep_ms);
    if (spawn)
    {
        STARTUPINFOW startup = {0};
        PROCESS_INFORMATION child = {0};
        WCHAR *path = path_name(spawn), *command = calloc(CAP, sizeof(*command));
        size_t used = 0;
        int ok = path && command && quote(command, &used, path);
        if (extra == argc)
            ok = ok && quote(command, &used, L"--sleep") && quote(command, &used, L"250") &&
                    quote(command, &used, L"--exit") && quote(command, &used, L"0");
        else for (i = extra; ok && i < argc; ++i) ok = quote(command, &used, argv[i]);
        startup.cb = sizeof(startup);
        Sleep(delay);
        if (ok) ok = CreateProcessW(path, command, NULL, NULL, FALSE, flags, NULL, NULL, &startup, &child);
        free(path);
        free(command);
        if (!ok) { fprintf(stderr, "fixture: spawn failed error=%lu\n", GetLastError()); return 2; }
        fprintf(stderr, "fixture: spawned pid=%lu flags=0x%08lx\n", child.dwProcessId, flags);
        if ((flags & CREATE_SUSPENDED) && ResumeThread(child.hThread) == (DWORD)-1)
        {
            CloseHandle(child.hThread);
            CloseHandle(child.hProcess);
            fputs("fixture: child resume failed\n", stderr);
            return 2;
        }
        CloseHandle(child.hThread);
        CloseHandle(child.hProcess);
    }
    if (hold) Sleep(have_sleep ? sleep_ms : 10000);
    if (crash)
    {
        SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
        SetUnhandledExceptionFilter(crashed);
        RaiseException(0xe0424242, EXCEPTION_NONCONTINUABLE, 0, NULL);
    }
    fprintf(stderr, "fixture: pid=%lu exited=%lu\n", GetCurrentProcessId(), exit_code);
    ExitProcess(exit_code);
    return 0;
usage:
    fputs("Usage: session-child.exe [--exit N] [--sleep MS] [--hold] [--crash] "
            "[--spawn PATH DELAY ROOTEXIT FLAGS | --handoff PATH] [--log-args PATH] [-- ARGS...]\n", stderr);
    return 2;
}
