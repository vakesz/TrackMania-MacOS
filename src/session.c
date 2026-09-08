/* Prefix-local launch supervision. Never set JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
 * Toolhelp enumerates Wine's current server/prefix, not host Unix processes.
 * A retained handle plus creation time identifies a process, never a PID alone.
 * Cumulative job accounting detects children missed by live snapshots. */
#ifndef UNICODE
#define UNICODE
#endif
#define _UNICODE
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <tlhelp32.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define PATH_CAP 32768
#define PROCESS_CAP 256
#define WATCH_CAP 8
#define SETTLE_MS 2000
/* Wine closes an unused desktop about a second after its last window owner exits. */
#define DESKTOP_MS 5000
#define POLL_MS 250

typedef struct process
{
    HANDLE handle;
    DWORD pid, exit_code, parent_pid;
    FILETIME created;
    int baseline, watched, in_job, exited, root, parent_known;
    int console_candidate, console_verified;
    int desktop_candidate, desktop_verified;
} Process;

/* A verified native runtime image, compared byte-for-byte against the prefix copy. */
typedef struct reference
{
    WCHAR *path;
    HANDLE handle;
    BY_HANDLE_FILE_INFORMATION file;
} Reference;

typedef struct session
{
    HANDLE job;
    Process *processes;
    size_t count, capacity;
    WCHAR *watch[WATCH_CAP];
    BY_HANDLE_FILE_INFORMATION watch_file[WATCH_CAP];
    Reference conhost, desktop;
    unsigned int watch_count;
    DWORD target_exit;
    int seen_target;
    const char *reason;
} Session;

static int problem(Session *s, const char *reason)
{
    if (!s->reason) s->reason = reason;
    return 0;
}

/* Names can case-fold to the same path on a case-sensitive Wine host. Only
 * usable volume/file IDs establish equality, including aliases and hard links. */
static int same_file(const BY_HANDLE_FILE_INFORMATION *a, const BY_HANDLE_FILE_INFORMATION *b)
{
    return (a->nFileIndexHigh || a->nFileIndexLow) && (b->nFileIndexHigh || b->nFileIndexLow) &&
            a->dwVolumeSerialNumber == b->dwVolumeSerialNumber &&
            a->nFileIndexHigh == b->nFileIndexHigh && a->nFileIndexLow == b->nFileIndexLow;
}

static int number(const WCHAR *text, DWORD *value)
{
    uint64_t n = 0;
    if (!*text) return 0;
    for (; *text; ++text)
    {
        if (*text < L'0' || *text > L'9') return 0;
        n = n * 10 + (*text - L'0');
        if (n > MAXDWORD) return 0;
    }
    *value = (DWORD)n;
    return 1;
}

/* Wine's Unix filenames are UTF-8 on macOS. Its returned buffer is heap-owned.
 * Wine may retain Unix symlink names even with FILE_NAME_NORMALIZED, so watch
 * watch comparison uses file identity, never a case-folded name as fallback. */
static WCHAR *path_name(const WCHAR *input, int existing, BY_HANDLE_FILE_INFORMATION *identity)
{
    union
    {
        FARPROC proc;
        WCHAR *(__cdecl *dos_name)(const char *);
    } wine;
    WCHAR *converted = NULL, *full = NULL, *final = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    DWORD length, error;
    char *utf8 = NULL;
    if (input[0] == L'/')
    {
        int bytes;
        wine.proc = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "wine_get_dos_file_name");
        if (!wine.proc) return NULL;
        bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input, -1, NULL, 0, NULL, NULL);
        if (!bytes || !(utf8 = malloc((size_t)bytes))) return NULL;
        if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input, -1, utf8, bytes, NULL, NULL))
            converted = wine.dos_name(utf8);
        free(utf8);
        if (!converted) return NULL;
        input = converted;
    }
    full = malloc(PATH_CAP * sizeof(*full));
    if (!full) goto failed;
    length = GetFullPathNameW(input, PATH_CAP, full, NULL);
    if (!length || length >= PATH_CAP) goto failed;
    if (converted) HeapFree(GetProcessHeap(), 0, converted);
    converted = NULL;
    if (!existing) return full;
    file = CreateFileW(full, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE |
            FILE_SHARE_DELETE, NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (file == INVALID_HANDLE_VALUE) goto failed;
    if (identity && (!GetFileInformationByHandle(file, identity) ||
            !(identity->nFileIndexHigh || identity->nFileIndexLow)))
    {
        SetLastError(ERROR_NOT_SUPPORTED);
        goto failed;
    }
    final = malloc(PATH_CAP * sizeof(*final));
    if (!final) goto failed;
    length = GetFinalPathNameByHandleW(file, final, PATH_CAP, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (!length || length >= PATH_CAP) goto failed;
    CloseHandle(file);
    free(full);
    if (!wcsncmp(final, L"\\\\?\\UNC\\", 8))
    {
        memmove(final + 2, final + 8, (wcslen(final + 8) + 1) * sizeof(*final));
        final[0] = final[1] = L'\\';
    }
    else if (!wcsncmp(final, L"\\\\?\\", 4))
        memmove(final, final + 4, (wcslen(final + 4) + 1) * sizeof(*final));
    return final;
failed:
    error = GetLastError();
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (converted) HeapFree(GetProcessHeap(), 0, converted);
    free(full);
    free(final);
    SetLastError(error);
    return NULL;
}

/* Verify an exact native runtime image once, against the helper's own runtime.
 * Missing/mismatched references disable only that exemption. Keep the image
 * open without write/delete sharing so the cached bytes and file ID stay valid. */
static int native_reference(Session *s, const WCHAR *name, Reference *out)
{
    static const WCHAR runtime[] = L"\\..\\lib\\wine\\x86_64-windows\\";
    WCHAR module[PATH_CAP], native[PATH_CAP], *slash, *reference_path = NULL, *canonical = NULL;
    HANDLE reference = INVALID_HANDLE_VALUE, image = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION identity;
    LARGE_INTEGER image_size, reference_size;
    BYTE image_bytes[16384], reference_bytes[16384];
    DWORD length, remaining, count, image_read, reference_read;
    ULONGLONG begin = GetTickCount64();
    int matched = 0, ok = 1;
    (void)s;
#ifndef _WIN64
    BOOL wow64, redirected = FALSE;
    void *redirection = NULL;
    if (!IsWow64Process(GetCurrentProcess(), &wow64)) goto done;
    if (wow64)
    {
        if (!Wow64DisableWow64FsRedirection(&redirection)) goto done;
        redirected = TRUE;
    }
#endif
    length = GetModuleFileNameW(NULL, module, PATH_CAP);
    if (!length || length >= PATH_CAP || !(slash = wcsrchr(module, L'\\')) ||
            (size_t)(slash - module) + sizeof(runtime) / sizeof(*runtime) + wcslen(name) > PATH_CAP) goto done;
    wcscpy(slash, runtime);
    wcscat(slash, name);
    reference_path = path_name(module, 0, NULL);
    if (!reference_path) goto done;
    reference = CreateFileW(reference_path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
            FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (reference == INVALID_HANDLE_VALUE) goto done;
    length = GetSystemDirectoryW(native, PATH_CAP);
    if (!length || length + wcslen(name) >= PATH_CAP - 2) goto done;
    wcscat(native, L"\\");
    wcscat(native, name);
    image = CreateFileW(native, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
            FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (image == INVALID_HANDLE_VALUE || GetFileType(image) != FILE_TYPE_DISK ||
            GetFileType(reference) != FILE_TYPE_DISK || !GetFileInformationByHandle(image, &identity) ||
            !(identity.nFileIndexHigh || identity.nFileIndexLow) ||
            !GetFileSizeEx(image, &image_size) || !GetFileSizeEx(reference, &reference_size) ||
            image_size.QuadPart <= 0 || image_size.QuadPart > 64 * 1024 * 1024 ||
            image_size.QuadPart != reference_size.QuadPart) goto done;
    canonical = path_name(native, 1, NULL);
    if (!canonical) goto done;
    for (remaining = (DWORD)image_size.QuadPart; remaining; remaining -= count)
    {
        count = remaining < sizeof(image_bytes) ? remaining : (DWORD)sizeof(image_bytes);
        if (GetTickCount64() - begin >= SETTLE_MS ||
                !ReadFile(image, image_bytes, count, &image_read, NULL) || image_read != count ||
                !ReadFile(reference, reference_bytes, count, &reference_read, NULL) || reference_read != count ||
                memcmp(image_bytes, reference_bytes, count)) goto done;
    }
    if (GetTickCount64() - begin >= SETTLE_MS) goto done;
    matched = 1;
done:
#ifndef _WIN64
    if (redirected && !Wow64RevertWow64FsRedirection(redirection))
    {
        matched = 0;
        ok = problem(s, "filesystem_redirection_failed");
    }
#endif
    if (matched)
    {
        out->path = canonical;
        out->file = identity;
        out->handle = image;
    }
    else
    {
        free(canonical);
        if (image != INVALID_HANDLE_VALUE) CloseHandle(image);
        fprintf(stderr, "session: %ls reference unavailable or different; such descendants remain unrecognized\n", name);
    }
    if (reference != INVALID_HANDLE_VALUE) CloseHandle(reference);
    free(reference_path);
    return ok;
}

/* Windows CRT quoting, including empty strings. Safe arguments stay bare:
 * Wine Explorer's /desktop size parser does not remove a trailing quote. */
static int quote(WCHAR *command, size_t *used, const WCHAR *arg)
{
    size_t slashes;
#define APPEND(c) do { if (*used >= PATH_CAP - 2) return 0; command[(*used)++] = (c); } while (0)
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

static int result_file(const WCHAR *path, const char *state, DWORD code, const char *reason)
{
    WCHAR *temp;
    HANDLE file;
    char json[256];
    DWORD written;
    size_t length = wcslen(path);
    int bytes, ok;
    static unsigned int serial;
    if (length + 80 >= PATH_CAP || !(temp = malloc((length + 80) * sizeof(*temp)))) return 0;
    swprintf(temp, length + 80, L"%ls.tmp-%lu-%llu-%u", path, GetCurrentProcessId(),
            (unsigned long long)GetTickCount64(), ++serial);
    if (!strcmp(state, "starting") || !strcmp(state, "running"))
        bytes = snprintf(json, sizeof(json), "{\"state\":\"%s\"}\n", state);
    else if (!strcmp(state, "exited"))
        bytes = snprintf(json, sizeof(json), "{\"state\":\"exited\",\"exit_code\":%lu,\"cleanup\":true}\n", code);
    else bytes = snprintf(json, sizeof(json), "{\"state\":\"%s\",\"reason\":\"%s\",\"cleanup\":false}\n", state, reason);
    file = CreateFileW(temp, GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) { free(temp); return 0; }
    ok = WriteFile(file, json, (DWORD)bytes, &written, NULL) && written == (DWORD)bytes && FlushFileBuffers(file);
    if (!CloseHandle(file)) ok = 0;
    if (ok) ok = MoveFileExW(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileW(temp);
    free(temp);
    return ok;
}

/* Start COM infrastructure before the baseline, not as a cleanup exception. */
static int service_ready(Session *s)
{
    SC_HANDLE manager, service = NULL;
    SERVICE_STATUS status;
    ULONGLONG begin = GetTickCount64();
    const char *reason = "rpcss_open_failed";
    int started = 0, ready = 0;
    manager = OpenSCManagerW(NULL, NULL, SC_MANAGER_CONNECT);
    if (!manager) goto done;
    service = OpenServiceW(manager, L"RpcSs", SERVICE_START | SERVICE_QUERY_STATUS);
    if (!service) goto done;
    for (;;)
    {
        reason = "rpcss_query_failed";
        if (!QueryServiceStatus(service, &status)) goto done;
        reason = "rpcss_start_timeout";
        if (GetTickCount64() - begin >= 10000) goto done;
        if (status.dwCurrentState == SERVICE_RUNNING)
        {
            ready = 1;
            fprintf(stderr, "session: RpcSs ready in %llums\n", (unsigned long long)(GetTickCount64() - begin));
            goto done;
        }
        if (status.dwCurrentState == SERVICE_STOPPED)
        {
            reason = "rpcss_start_failed";
            if (started) goto done;
            started = 1;
            if (!StartServiceW(service, 0, NULL) && GetLastError() != ERROR_SERVICE_ALREADY_RUNNING) goto done;
        }
        else if (status.dwCurrentState != SERVICE_START_PENDING && status.dwCurrentState != SERVICE_STOP_PENDING)
        {
            reason = "rpcss_unavailable";
            goto done;
        }
        Sleep(POLL_MS);
    }
done:
    if (!ready) fprintf(stderr, "session: %s winerror=%lu\n", reason, GetLastError());
    if (service) CloseServiceHandle(service);
    if (manager) CloseServiceHandle(manager);
    return ready || problem(s, reason);
}

static int poll_process(Session *s, Process *p)
{
    DWORD wait;
    if (p->exited) return 1;
    wait = WaitForSingleObject(p->handle, 0);
    if (wait == WAIT_TIMEOUT) return 1;
    if (wait != WAIT_OBJECT_0 || !GetExitCodeProcess(p->handle, &p->exit_code))
        return problem(s, "process_status_failed");
    p->exited = 1;
    if (p->watched && !s->target_exit && p->exit_code) s->target_exit = p->exit_code;
    if (!p->baseline)
        fprintf(stderr, "session: pid=%lu exited=%lu%s\n", p->pid, p->exit_code, p->watched ? " target" : "");
    return 1;
}

/* Takes ownership of handle, including on failure. All historical handles are
 * retained; reaching the cap is uncertainty, not permission to forget a PID. */
static int register_process(Session *s, HANDLE handle, DWORD pid, int baseline, int root, int expect_job)
{
    FILETIME created, ended, kernel, user;
    BY_HANDLE_FILE_INFORMATION identity;
    BOOL in_job;
    WCHAR *image = NULL, *canonical = NULL;
    DWORD length = PATH_CAP;
    ULONGLONG image_begin;
    size_t i;
    Process *p;
    const char *reason = "process_identity_failed";
    if (GetProcessId(handle) != pid || !GetProcessTimes(handle, &created, &ended, &kernel, &user) ||
            !IsProcessInJob(handle, s->job, &in_job)) goto failed;
    for (i = 0; i < s->count; ++i)
    {
        p = &s->processes[i];
        if (p->pid == pid && !CompareFileTime(&p->created, &created))
        {
            if (expect_job && !in_job && !p->in_job) goto failed;
            if (in_job) p->in_job = 1;
            CloseHandle(handle);
            return poll_process(s, p);
        }
    }
    if (expect_job && !in_job) goto failed;
    reason = "process_limit";
    if (s->count == PROCESS_CAP) goto failed;
    reason = "process_image_failed";
    image = malloc(PATH_CAP * sizeof(*image));
    if (!image) goto failed;
    /* Wine can list a new process before publishing its image and creation
     * time. Retry metadata on this retained handle for at most one poll period;
     * an exited/unreadable process still means uncertainty, never success. */
    image_begin = GetTickCount64();
    for (;;)
    {
        length = PATH_CAP;
        if (QueryFullProcessImageNameW(handle, 0, image, &length))
        {
            if (!GetProcessTimes(handle, &created, &ended, &kernel, &user))
            { reason = "process_identity_failed"; goto failed; }
            if (created.dwHighDateTime || created.dwLowDateTime) break;
        }
        else if (GetLastError() != ERROR_INVALID_HANDLE) goto failed;
        if (GetTickCount64() - image_begin >= POLL_MS || WaitForSingleObject(handle, 25) != WAIT_TIMEOUT)
            goto failed;
    }
#ifndef _WIN64
    {
        BOOL wow64;
        void *redirection = NULL;
        DWORD error;
        /* QueryFullProcessImageName returns the actual image path, including
         * native System32 images. Do not reopen them through WOW64 redirection. */
        if (!IsWow64Process(GetCurrentProcess(), &wow64) ||
                (wow64 && !Wow64DisableWow64FsRedirection(&redirection))) goto failed;
        canonical = path_name(image, 1, &identity);
        error = GetLastError();
        if (wow64 && !Wow64RevertWow64FsRedirection(redirection)) goto failed;
        SetLastError(error);
    }
#else
    canonical = path_name(image, 1, &identity);
#endif
    if (!canonical)
    {
        if (GetLastError() == ERROR_NOT_SUPPORTED) reason = "file_identity_unavailable";
        goto failed;
    }
    if (s->count == s->capacity)
    {
        size_t capacity = s->capacity ? s->capacity * 2 : 32;
        Process *grown = realloc(s->processes, capacity * sizeof(*grown));
        reason = "out_of_memory";
        if (!grown) goto failed;
        s->capacity = capacity;
        s->processes = grown;
    }
    p = &s->processes[s->count++];
    memset(p, 0, sizeof(*p));
    p->handle = handle;
    p->pid = pid;
    p->created = created;
    p->baseline = baseline;
    p->root = root;
    p->in_job = in_job;
    for (i = 0; i < s->watch_count; ++i)
        if (same_file(&identity, &s->watch_file[i])) p->watched = 1;
    p->console_candidate = !baseline && !root && !p->watched && in_job && s->conhost.path &&
            CompareStringOrdinal(canonical, -1, s->conhost.path, -1, TRUE) == CSTR_EQUAL &&
            same_file(&identity, &s->conhost.file);
    /* win32u starts the native explorer for the first window owner without a
     * desktop; it runs inside the job and outlives the game by about a second. */
    p->desktop_candidate = !baseline && !root && !p->watched && in_job && s->desktop.path &&
            CompareStringOrdinal(canonical, -1, s->desktop.path, -1, TRUE) == CSTR_EQUAL &&
            same_file(&identity, &s->desktop.file);
    fprintf(stderr, "session: pid=%lu observed%s%s%s%s image=%ls\n", pid, root ? " root" : "",
            p->watched ? " target" : "", in_job ? " job" : " outside-job",
            baseline ? " baseline" : "", canonical);
    free(image);
    free(canonical);
    if (p->watched) s->seen_target = 1;
    if (!poll_process(s, p)) return 0;
    if (baseline && p->watched && !p->exited) return problem(s, "watch_already_running");
    return 1;
failed:
    fprintf(stderr, "session: pid=%lu %s winerror=%lu\n", pid, reason, GetLastError());
    free(image);
    free(canonical);
    CloseHandle(handle);
    return problem(s, reason);
}

static int discover(Session *s, DWORD pid, int baseline, int expect_job)
{
    size_t i;
    HANDLE handle;
    for (i = 0; i < s->count; ++i)
    {
        Process *p = &s->processes[i];
        if (p->pid != pid) continue;
        if (!poll_process(s, p)) return 0;
        if (!p->exited)
        {
            BOOL in_job;
            /* A live retained handle prevents PID reuse. Recheck membership. */
            if (!IsProcessInJob(p->handle, s->job, &in_job) || (expect_job && !in_job))
                return problem(s, "job_membership_failed");
            if (in_job) p->in_job = 1;
            return 1;
        }
    }
    /* IsProcessInJob requires QUERY_INFORMATION, not just LIMITED_INFORMATION. */
    handle = OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, FALSE, pid);
    if (!handle) return problem(s, "process_open_failed");
    return register_process(s, handle, pid, baseline, 0, expect_job);
}

static int prefix_snapshot(Session *s, int baseline)
{
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry;
    DWORD error;
    int more;
    if (snapshot == INVALID_HANDLE_VALUE) return problem(s, "prefix_snapshot_failed");
    memset(&entry, 0, sizeof(entry));
    entry.dwSize = sizeof(entry);
    more = Process32FirstW(snapshot, &entry);
    while (more)
    {
        size_t i;
        /* PID zero is the idle pseudo-process, not an executable. */
        if (entry.th32ProcessID && entry.th32ProcessID != GetCurrentProcessId() &&
                !discover(s, entry.th32ProcessID, baseline, 0))
        {
            CloseHandle(snapshot);
            return 0;
        }
        /* A snapshot parent PID alone is not proof of process ownership. */
        for (i = 0; i < s->count; ++i)
        {
            Process *p = &s->processes[i];
            if (p->pid != entry.th32ProcessID || p->parent_known) continue;
            fprintf(stderr, "session: pid=%lu snapshot-parent=%lu\n", p->pid, entry.th32ParentProcessID);
            p->parent_pid = entry.th32ParentProcessID;
            p->parent_known = 1;
        }
        more = Process32NextW(snapshot, &entry);
    }
    error = GetLastError();
    CloseHandle(snapshot);
    if (error != ERROR_NO_MORE_FILES) return problem(s, "prefix_snapshot_failed");
    return 1;
}

static int reconcile(Session *s, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION *accounting)
{
    struct
    {
        DWORD assigned, listed;
        ULONG_PTR pids[PROCESS_CAP];
    } list;
    size_t i;
    memset(&list, 0, sizeof(list));
    if (!QueryInformationJobObject(s->job, JobObjectBasicProcessIdList, &list, sizeof(list), NULL))
        return problem(s, GetLastError() == ERROR_MORE_DATA ? "process_limit" : "job_snapshot_failed");
    if (list.assigned > PROCESS_CAP || list.listed > PROCESS_CAP || list.assigned != list.listed)
        return problem(s, "job_snapshot_incomplete");
    for (i = 0; i < list.listed; ++i)
        if (!discover(s, (DWORD)list.pids[i], 0, 1)) return 0;
    if (!prefix_snapshot(s, 0)) return 0;
    for (i = 0; i < s->count; ++i)
        if (!poll_process(s, &s->processes[i])) return 0;
    if (!QueryInformationJobObject(s->job, JobObjectBasicAccountingInformation,
            accounting, sizeof(*accounting), NULL)) return problem(s, "job_accounting_failed");
    return 1;
}

int wmain(int argc, WCHAR **argv)
{
    Session s = {0};
    WCHAR *result = NULL, *executable = NULL, *command = NULL, *explorer = NULL;
    WCHAR *raw_watch[WATCH_CAP], *raw_result = NULL;
    WCHAR desktop_arg[160], dimensions[32];
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION child = {0};
    JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting;
    DWORD startup_ms = 0, width = 0, height = 0;
    ULONGLONG begin, idle_since = 0, close_since = 0;
    size_t used = 0, i;
    unsigned int watch_count = 0;
    int arg, target = 0, have_startup = 0, desktop = 0, resumed = 0, created = 0;
    int close_requested = 0, idle = 0, running = 0, status = 2;
    const char *state = "failed";

    /* Locate the result independently so malformed options can still report. */
    for (arg = 1; arg < argc && wcscmp(argv[arg], L"--"); ++arg)
        if (!wcscmp(argv[arg], L"--result") && arg + 1 < argc) { raw_result = argv[arg + 1]; break; }
    if (raw_result) result = path_name(raw_result, 0, NULL);
    if (!result) { s.reason = "result_path_invalid"; goto done; }
    raw_result = NULL;
    for (arg = 1; arg < argc; ++arg)
    {
        const WCHAR *option = argv[arg];
        if (!wcscmp(option, L"--")) { target = arg + 1; break; }
        if (++arg >= argc) goto usage;
        if (!wcscmp(option, L"--result") && !raw_result) raw_result = argv[arg];
        else if (!wcscmp(option, L"--startup-ms") && !have_startup)
        {
            if (!number(argv[arg], &startup_ms) || !startup_ms || startup_ms == INFINITE) goto usage;
            have_startup = 1;
        }
        else if (!wcscmp(option, L"--watch") && watch_count < WATCH_CAP) raw_watch[watch_count++] = argv[arg];
        else if (!wcscmp(option, L"--desktop") && !desktop)
        {
            WCHAR *separator;
            if (wcslen(argv[arg]) >= sizeof(dimensions) / sizeof(*dimensions)) goto usage;
            wcscpy(dimensions, argv[arg]);
            separator = wcschr(dimensions, L'x');
            if (!separator) goto usage;
            *separator++ = 0;
            if (!number(dimensions, &width) || !number(separator, &height) ||
                    !width || !height || width > 65535 || height > 65535) goto usage;
            desktop = 1;
        }
        else goto usage;
    }
    if (!raw_result || !have_startup || !target || target >= argc) goto usage;
    if (!result_file(result, "starting", 0, NULL)) { s.reason = "result_write_failed"; goto done; }
    executable = path_name(argv[target], 1, NULL);
    if (!executable) { s.reason = "executable_path_invalid"; goto done; }
    if (!watch_count) raw_watch[watch_count++] = argv[target];
    for (i = 0; i < watch_count; ++i)
    {
        s.watch[i] = path_name(raw_watch[i], 1, &s.watch_file[i]);
        if (!s.watch[i])
        {
            s.reason = GetLastError() == ERROR_NOT_SUPPORTED ? "file_identity_unavailable" : "watch_path_invalid";
            goto done;
        }
        ++s.watch_count;
    }
    command = calloc(PATH_CAP, sizeof(*command));
    if (!command) { s.reason = "out_of_memory"; goto done; }
    if (desktop)
    {
        WCHAR windows[PATH_CAP];
        DWORD length = GetWindowsDirectoryW(windows, PATH_CAP);
        if (!length || length + 14 >= PATH_CAP) { s.reason = "explorer_path_invalid"; goto done; }
        wcscat(windows, L"\\explorer.exe");
        explorer = path_name(windows, 1, NULL);
        if (!explorer) { s.reason = "explorer_path_invalid"; goto done; }
        swprintf(desktop_arg, sizeof(desktop_arg) / sizeof(*desktop_arg),
                L"/desktop=MacTMNF-%lu-%llu,%lux%lu", GetCurrentProcessId(),
                (unsigned long long)GetTickCount64(), width, height);
        if (!quote(command, &used, explorer) || !quote(command, &used, desktop_arg)) goto long_command;
    }
    if (!quote(command, &used, executable)) goto long_command;
    for (arg = target + 1; arg < argc; ++arg)
        if (!quote(command, &used, argv[arg])) goto long_command;

    if (!service_ready(&s) || !native_reference(&s, L"conhost.exe", &s.conhost) ||
            !native_reference(&s, L"explorer.exe", &s.desktop)) goto done;
    s.job = CreateJobObjectW(NULL, NULL);
    if (!s.job) { s.reason = "job_setup_failed"; goto done; }
    if (!prefix_snapshot(&s, 1)) goto done;
    /* Baseline exits are not evidence that this launch started a target. */
    s.seen_target = 0;
    s.target_exit = 0;
    startup.cb = sizeof(startup);
    begin = GetTickCount64();
    if (!CreateProcessW(desktop ? explorer : executable, command, NULL, NULL, FALSE,
            CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT, NULL, NULL, &startup, &child))
    {
        state = "failed";
        s.reason = "create_process_failed";
        goto done;
    }
    created = 1;
    state = "uncertain";
    if (!AssignProcessToJobObject(s.job, child.hProcess)) { s.reason = "job_assign_failed"; goto done; }
    /* Keep CreateProcess's root handle separately from the registry. */
    {
        HANDLE retained;
        if (!DuplicateHandle(GetCurrentProcess(), child.hProcess, GetCurrentProcess(),
                &retained, 0, FALSE, DUPLICATE_SAME_ACCESS)) { s.reason = "process_open_failed"; goto done; }
        if (!register_process(&s, retained, child.dwProcessId, 0, 1, 1)) goto done;
    }
    if (ResumeThread(child.hThread) == (DWORD)-1) { s.reason = "resume_failed"; goto done; }
    resumed = 1;
    fprintf(stderr, "session: running root=%lu%s\n", child.dwProcessId, desktop ? " desktop" : "");

    for (;;)
    {
        DWORD known_job = 0, live_job = 0, live_desktops = 0, live_targets = 0, ambiguous = 0;
        Process *root = NULL;
        ULONGLONG now;
        int accounted;
        if (!reconcile(&s, &accounting)) goto done;
        if (!running && s.seen_target)
        {
            if (!result_file(result, "running", 0, NULL)) { s.reason = "result_write_failed"; goto done; }
            running = 1;
            fputs("session: watched target observed; state=running\n", stderr);
        }
        for (i = 0; i < s.count; ++i)
        {
            Process *p = &s.processes[i];
            if (p->root) root = p;
            if (p->in_job) ++known_job;
            if ((p->console_candidate && !p->console_verified && p->exited && !p->exit_code) ||
                    (p->desktop_candidate && !p->desktop_verified))
            {
                size_t j;
                if (!p->parent_known) goto unverified;
                for (j = 0; j < s.count; ++j)
                {
                    const Process *parent = &s.processes[j];
                    if (parent == p || parent->pid != p->parent_pid || !parent->in_job ||
                            !(parent->root || parent->watched) ||
                            CompareFileTime(&parent->created, &p->created) > 0) continue;
                    /* Retained process handles prevent PID reuse. Both images
                     * were registered in this job, not inferred from a name. */
                    if (p->console_candidate)
                    {
                        p->console_verified = 1;
                        fprintf(stderr, "session: pid=%lu verified console infrastructure parent=%lu exit=0\n",
                                p->pid, parent->pid);
                    }
                    else
                    {
                        p->desktop_verified = 1;
                        fprintf(stderr, "session: pid=%lu verified desktop infrastructure parent=%lu\n",
                                p->pid, parent->pid);
                    }
                    break;
                }
            }
unverified:
            /* An unrecognized job descendant remains ambiguous even after exit
             * zero. The explicit launch root is not an unknown descendant. A
             * verified desktop stays recognized while alive or after exit zero. */
            if (!p->watched && !p->baseline && !(p->desktop_verified && (!p->exited || !p->exit_code)) &&
                    ((!p->root && p->in_job && !p->console_verified) ||
                     (!p->exited && !(desktop && p->root)))) ++ambiguous;
            if (p->exited) continue;
            if (p->watched) ++live_targets;
            if (p->in_job) ++live_job;
            if (p->desktop_verified) ++live_desktops;
        }
        now = GetTickCount64();
        accounted = accounting.TotalProcesses == known_job && accounting.ActiveProcesses == live_job;
        if (!s.seen_target)
        {
            if (now - begin >= startup_ms)
            {
                if (!accounted) s.reason = "unobserved_job_process";
                else if (ambiguous) s.reason = "ambiguous_process_activity";
                else if (root && root->exited && !live_job)
                {
                    state = "failed";
                    s.reason = "command_not_started";
                }
                else s.reason = "startup_timeout";
                goto done;
            }
        }
        else if (live_targets) idle = 0;
        else
        {
            if (!idle) { idle = 1; idle_since = now; }
            if (now - idle_since >= SETTLE_MS)
            {
                if (!accounted) { s.reason = "unobserved_job_process"; goto done; }
                if (ambiguous) { s.reason = "ambiguous_process_activity"; goto done; }
                if (root && !root->exited)
                {
                    if (!desktop || root->watched || live_job != 1)
                    { s.reason = "root_did_not_settle"; goto done; }
                    if (!close_requested)
                    {
                        fprintf(stderr, "session: closing desktop root=%lu\n", root->pid);
                        /* Wine's desktop WM_CLOSE handler posts WM_QUIT. Use
                         * the retained main thread, not another desktop's HWND. */
                        if (WaitForSingleObject(child.hThread, 0) == WAIT_TIMEOUT)
                            PostThreadMessageW(child.dwThreadId, WM_QUIT, 0, 0);
                        close_requested = 1;
                        close_since = now;
                    }
                    else if (now - close_since >= SETTLE_MS)
                    {
                        /* This iteration reconciled again after the grace
                         * period. Never force a job or desktop from a snapshot:
                         * a new handoff may start immediately after it. */
                        s.reason = "desktop_stop_timeout";
                        goto done;
                    }
                }
                else if (!live_job)
                {
                    state = "exited";
                    status = 0;
                    goto done;
                }
                else if (live_job == live_desktops)
                {
                    /* Only the game's own desktop remains. Wine would close it a
                     * second after its last user left, but this helper's user32
                     * thread keeps it open. Ask the verified owner to close, as
                     * wineserver's close timeout does; never terminate it. */
                    if (!close_requested)
                    {
                        HWND window = GetDesktopWindow();
                        DWORD owner = 0;
                        size_t j;
                        if (window && GetWindowThreadProcessId(window, &owner))
                            for (j = 0; j < s.count; ++j)
                            {
                                const Process *p = &s.processes[j];
                                if (p->pid != owner || !p->desktop_verified || p->exited) continue;
                                fprintf(stderr, "session: closing desktop pid=%lu\n", owner);
                                if (!PostMessageW(window, WM_CLOSE, 0, 0)) owner = 0;
                                break;
                            }
                        if (!owner) fputs("session: waiting for the desktop to close\n", stderr);
                        close_requested = 1;
                        close_since = now;
                    }
                    else if (now - close_since >= DESKTOP_MS) { s.reason = "desktop_stop_timeout"; goto done; }
                }
                else { s.reason = "ambiguous_job_activity"; goto done; }
            }
        }
        Sleep(POLL_MS);
    }
usage:
    s.reason = "invalid_arguments";
    fputs("Usage: session.exe --result RESULT_PATH --startup-ms N [--watch WATCH_PATH ...] "
            "[--desktop WIDTHxHEIGHT] -- EXECUTABLE [ARGS...]\n", stderr);
    goto done;
long_command:
    s.reason = "command_line_too_long";
done:
    /* Roll back only the exact CREATE_SUSPENDED root before our first successful
     * resume. Never run an unsupervised game, or terminate a resumed target. */
    if (created && !resumed)
    {
        DWORD wait = WaitForSingleObject(child.hProcess, 0);
        if (wait == WAIT_TIMEOUT && TerminateProcess(child.hProcess, ERROR_PROCESS_ABORTED))
            wait = WaitForSingleObject(child.hProcess, SETTLE_MS);
        if (wait == WAIT_OBJECT_0)
        {
            state = "failed";
            fprintf(stderr, "session: stopped unresumed root=%lu\n", child.dwProcessId);
        }
        else
        {
            state = "uncertain";
            s.reason = "suspended_root_stop_failed";
            fprintf(stderr, "session: could not stop unresumed root=%lu\n", child.dwProcessId);
        }
    }
    if (s.reason && !strcmp(s.reason, "file_identity_unavailable")) state = "uncertain";
    if (!s.reason && strcmp(state, "exited")) s.reason = "internal_error";
    if (!strcmp(state, "exited")) fprintf(stderr, "session: exited target_status=%lu cleanup=true\n", s.target_exit);
    else fprintf(stderr, "session: %s reason=%s cleanup=false winerror=%lu\n", state, s.reason, GetLastError());
    if (!result || !result_file(result, state, s.target_exit, s.reason))
    {
        fputs("session: cannot publish result\n", stderr);
        status = 2;
    }
    if (child.hThread) CloseHandle(child.hThread);
    if (child.hProcess) CloseHandle(child.hProcess);
    for (i = 0; i < s.count; ++i) CloseHandle(s.processes[i].handle);
    free(s.processes);
    if (s.job) CloseHandle(s.job);
    if (s.conhost.handle) CloseHandle(s.conhost.handle);
    free(s.conhost.path);
    if (s.desktop.handle) CloseHandle(s.desktop.handle);
    free(s.desktop.path);
    for (i = 0; i < s.watch_count; ++i) free(s.watch[i]);
    free(result);
    free(executable);
    free(command);
    free(explorer);
    return status;
}
