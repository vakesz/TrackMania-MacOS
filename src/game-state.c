/* Report TMNF's game state (menu, race, finish, ...) and map while the game runs.
 *
 * Diagnostic helper for MACTMNF_DIAG=on; lib/monitor.py builds it on demand and
 * runs it in the game's prefix. It is not part of the runtime.
 *
 * It waits for TmForever.exe, checks that the executable is the build whose
 * layout is known, then reads the state and the current map's name from the
 * game's memory every 100 ms and prints a line whenever one changes:
 *   mactmnf-state: <seconds> 0x<state>    or    mactmnf-state: <seconds> unknown
 *   mactmnf-map: <seconds> <name>         (none when no map is loaded)
 * <seconds> is QueryPerformanceCounter time, the clock of Wine's +timestamp
 * debug prefix, so the lines can be matched with Wine's +fps lines.
 */
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include <tlhelp32.h>

/* CGameApp::s_TheGame, then the loads CTrackMania::GetCurrentState makes (TmForever.map). */
static const DWORD chain[] = {0xD68C44, 0x12C, 0x300, 0x0, 0x124};
static const DWORD image_base = 0x400000;
static const DWORD known_stamp = 0x4d431494;  /* TMNF 2.11.26 and its Competition Patch build */
/* CGameCtnApp::GetChallenge reads the map at +0x198 of the same object; the map's
 * name is a counted UTF-16 string (length, then pointer) at +0x108. */
static const DWORD map_offset = 0x198, map_name_offset = 0x108;

static double seconds(void)
{
    LARGE_INTEGER now, frequency;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return (double)now.QuadPart / (double)frequency.QuadPart;
}

static BOOL read32(HANDLE process, DWORD address, DWORD *value)
{
    SIZE_T done = 0;
    return address != 0 && ReadProcessMemory(process, (LPCVOID)(ULONG_PTR)address, value, 4, &done) && done == 4;
}

/* The current map's name as UTF-8: "none" without a map, "" if unreadable. */
static void read_map(HANDLE process, char *out, size_t size)
{
    DWORD app = 0, map = 0, length = 0, text = 0;
    WCHAR name[128];
    out[0] = '\0';
    if (!read32(process, chain[0], &app) || !read32(process, app + map_offset, &map))
        return;
    if (map == 0) {
        snprintf(out, size, "none");
        return;
    }
    SIZE_T done = 0;
    if (!read32(process, map + map_name_offset, &length) || !read32(process, map + map_name_offset + 4, &text)
            || length == 0 || length >= sizeof(name) / sizeof(name[0]) || text == 0
            || !ReadProcessMemory(process, (LPCVOID)(ULONG_PTR)text, name, length * sizeof(WCHAR), &done)
            || done != length * sizeof(WCHAR))
        return;
    int bytes = WideCharToMultiByte(CP_UTF8, 0, name, (int)length, out, (int)size - 1, NULL, NULL);
    out[bytes > 0 ? bytes : 0] = '\0';
    for (char *c = out; *c; c++)
        if ((unsigned char)*c < 0x20)
            *c = '?';  /* keep one line per change */
}

static DWORD find_game(void)
{
    DWORD pid = 0;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry = {.dwSize = sizeof(entry)};
    if (snapshot == INVALID_HANDLE_VALUE)
        return 0;
    for (BOOL more = Process32FirstW(snapshot, &entry); more && !pid; more = Process32NextW(snapshot, &entry))
        if (_wcsicmp(entry.szExeFile, L"TmForever.exe") == 0)
            pid = entry.th32ProcessID;
    CloseHandle(snapshot);
    return pid;
}

int main(void)
{
    HANDLE process = NULL;
    for (int tries = 0; process == NULL; tries++) {  /* the game starts after the launcher */
        DWORD pid = find_game();
        if (pid)
            process = OpenProcess(PROCESS_VM_READ | SYNCHRONIZE, FALSE, pid);
        if (process == NULL) {
            if (tries > 1200)  /* two minutes */
                return 1;
            Sleep(100);
        }
    }

    DWORD header = 0, stamp = 0;
    for (int tries = 0; !(read32(process, image_base + 0x3c, &header) && header < 0x1000
                          && read32(process, image_base + header + 8, &stamp)); tries++) {
        if (tries > 100 || WaitForSingleObject(process, 100) == WAIT_OBJECT_0)
            return 1;
    }
    if (stamp != known_stamp) {
        printf("mactmnf-state: %.3f unsupported 0x%lx\n", seconds(), (unsigned long)stamp);
        return 0;
    }

    BOOL reported = FALSE, had = FALSE;
    DWORD last = 0;
    char map[400], last_map[400] = "\001";
    while (WaitForSingleObject(process, 100) == WAIT_TIMEOUT) {
        DWORD pointer = chain[0], value = 0;
        BOOL ok = TRUE;
        for (size_t i = 1; ok && i < sizeof(chain) / sizeof(chain[0]); i++) {
            DWORD next = 0;
            ok = read32(process, pointer, &next);
            pointer = next + chain[i];
        }
        ok = ok && read32(process, pointer, &value);
        if (!reported || ok != had || (ok && value != last)) {
            if (ok)
                printf("mactmnf-state: %.3f 0x%lx\n", seconds(), (unsigned long)value);
            else
                printf("mactmnf-state: %.3f unknown\n", seconds());
            fflush(stdout);
            reported = TRUE;
            had = ok;
            last = value;
        }
        read_map(process, map, sizeof(map));
        if (strcmp(map, last_map) != 0) {
            printf("mactmnf-map: %.3f %s\n", seconds(), map[0] ? map : "unknown");
            fflush(stdout);
            strcpy(last_map, map);
        }
    }
    printf("mactmnf-state: %.3f exited\n", seconds());
    return 0;
}
