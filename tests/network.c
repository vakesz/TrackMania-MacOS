/* CDN reachability, or controlled loopback TLS checks with normal verification. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define ASYNC_CALLBACKS (WINHTTP_CALLBACK_FLAG_SENDREQUEST_COMPLETE | \
        WINHTTP_CALLBACK_FLAG_HEADERS_AVAILABLE | WINHTTP_CALLBACK_FLAG_REQUEST_ERROR)

struct request_context
{
    HINTERNET request;
    HANDLE progress, closed;
    DWORD mask;
    char body;
    volatile LONG reads;
    WINHTTP_ASYNC_RESULT failure;
    volatile LONG secure_failures, secure_notifications, sends, headers, errors, closes, bad;
    volatile LONG sequence, secure_order, send_order, receive_order, headers_order, error_order, close_order;
};

/* Requests are sequential. Check the supplied context before dereferencing it. */
static struct request_context *active_context;
static DWORD last_connection_id;
static int require_reuse;
static int drain_response;
static int subscribe_secure = 1;
static int check_revocation;

static void CALLBACK status_callback(HINTERNET handle, DWORD_PTR context, DWORD status,
        void *information, DWORD length)
{
    struct request_context *state = active_context;
    LONG order;
    if (!state) return;
    if (context != (DWORD_PTR)state || handle != state->request || !(state->mask & status))
        InterlockedIncrement(&state->bad);
    if (handle != state->request) return;
    if (status == WINHTTP_CALLBACK_STATUS_READ_COMPLETE)
    {
        if (information != &state->body || length) InterlockedIncrement(&state->bad);
        InterlockedIncrement(&state->reads);
        SetEvent(state->progress);
        return;
    }
    order = InterlockedIncrement(&state->sequence);
    switch (status)
    {
    case WINHTTP_CALLBACK_STATUS_SECURE_FAILURE:
        InterlockedIncrement(&state->secure_notifications);
        state->secure_order = order;
        if (information && length == sizeof(DWORD))
            InterlockedOr(&state->secure_failures, *(DWORD *)information);
        else InterlockedIncrement(&state->bad);
        return;
    case WINHTTP_CALLBACK_STATUS_SENDREQUEST_COMPLETE:
        InterlockedIncrement(&state->sends);
        state->send_order = order;
        if (length) InterlockedIncrement(&state->bad);
        break;
    case WINHTTP_CALLBACK_STATUS_HEADERS_AVAILABLE:
        InterlockedIncrement(&state->headers);
        state->headers_order = order;
        if (length) InterlockedIncrement(&state->bad);
        break;
    case WINHTTP_CALLBACK_STATUS_REQUEST_ERROR:
        InterlockedIncrement(&state->errors);
        state->error_order = order;
        if (information && length == sizeof(WINHTTP_ASYNC_RESULT))
            memcpy(&state->failure, information, sizeof(state->failure));
        else InterlockedIncrement(&state->bad);
        break;
    case WINHTTP_CALLBACK_STATUS_HANDLE_CLOSING:
        InterlockedIncrement(&state->closes);
        state->close_order = order;
        if (!information || length != sizeof(HINTERNET) || *(HINTERNET *)information != handle)
            InterlockedIncrement(&state->bad);
        /* Final notification, and our last access to callback-owned state. */
        SetEvent(state->closed);
        return;
    default:
        InterlockedIncrement(&state->bad);
        break;
    }
    SetEvent(state->progress);
}

static BOOL wait_notification(HANDLE event, DWORD timeout)
{
    DWORD result = WaitForSingleObject(event, timeout);
    if (result == WAIT_OBJECT_0) return TRUE;
    if (result == WAIT_TIMEOUT) SetLastError(ERROR_TIMEOUT);
    return FALSE;
}

static int check_url(const WCHAR *url, DWORD expected, int local, int async, DWORD initial_flags, DWORD final_flags)
{
    HINTERNET session = NULL, connection = NULL, request = NULL;
    WCHAR host[256], path[2048];
    URL_COMPONENTS parts = {0};
    DWORD status = 0, size = sizeof(status), error = ERROR_SUCCESS;
    DWORD redirects = WINHTTP_OPTION_REDIRECT_POLICY_NEVER;
    struct request_context state = {0};
    DWORD_PTR context = (DWORD_PTR)&state;
    BOOL subscribed = FALSE;
    int result = 1;

    parts.dwStructSize = sizeof(parts);
    parts.lpszHostName = host;
    parts.dwHostNameLength = sizeof(host) / sizeof(host[0]);
    parts.lpszUrlPath = path;
    parts.dwUrlPathLength = sizeof(path) / sizeof(path[0]);
    parts.dwUserNameLength = parts.dwPasswordLength = parts.dwExtraInfoLength = (DWORD)-1;
    if (!WinHttpCrackUrl(url, 0, 0, &parts) || parts.nScheme != INTERNET_SCHEME_HTTPS ||
            parts.dwUserNameLength || parts.dwPasswordLength || parts.dwExtraInfoLength ||
            (local && wcscmp(host, L"127.0.0.1")))
    {
        fputs("FAIL: expected an HTTPS URL without credentials, query or fragment; "
                "local tests require the literal host 127.0.0.1\n", stderr);
        return 1;
    }

    state.progress = CreateEventW(NULL, FALSE, FALSE, NULL);
    state.closed = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!state.progress || !state.closed) goto api_failed;
    active_context = &state;
    state.mask = (subscribe_secure ? WINHTTP_CALLBACK_FLAG_SECURE_FAILURE : 0) | WINHTTP_CALLBACK_STATUS_HANDLE_CLOSING |
            (async ? ASYNC_CALLBACKS | (drain_response ? WINHTTP_CALLBACK_FLAG_READ_COMPLETE : 0) : 0);
    session = WinHttpOpen(L"MacTMNF runtime test", local ? WINHTTP_ACCESS_TYPE_NO_PROXY :
            WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, async ? WINHTTP_FLAG_ASYNC : 0);
    if (!session) goto api_failed;
    if (!WinHttpSetTimeouts(session, 15000, 15000, 15000, 15000)) goto api_failed;
    connection = WinHttpConnect(session, host, parts.nPort, 0);
    if (!connection) goto api_failed;
    request = WinHttpOpenRequest(connection, L"HEAD", parts.dwUrlPathLength ? path : L"/",
            NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE);
    if (!request) goto api_failed;
    state.request = request;
    if (local)
    {
        DWORD queried = 0, queried_size = sizeof(queried);
        if (!WinHttpSetOption(request, WINHTTP_OPTION_SECURITY_FLAGS, &initial_flags, sizeof(initial_flags)) ||
                !WinHttpSetOption(request, WINHTTP_OPTION_SECURITY_FLAGS, &final_flags, sizeof(final_flags)) ||
                !WinHttpQueryOption(request, WINHTTP_OPTION_SECURITY_FLAGS, &queried, &queried_size)) goto api_failed;
        if (queried != final_flags) { SetLastError(ERROR_INVALID_DATA); goto api_failed; }
        printf("INFO: security flags initial=0x%08lx effective=0x%08lx\n", initial_flags, queried);
        if (check_revocation)
        {
            DWORD feature = WINHTTP_ENABLE_SSL_REVOCATION;
            if (!WinHttpSetOption(request, WINHTTP_OPTION_ENABLE_FEATURE, &feature, sizeof(feature))) goto api_failed;
        }
    }
    if (!WinHttpSetOption(request, WINHTTP_OPTION_CONTEXT_VALUE, &context, sizeof(context))) goto api_failed;
    if (WinHttpSetStatusCallback(request, status_callback, state.mask, 0) ==
            WINHTTP_INVALID_STATUS_CALLBACK) goto api_failed;
    subscribed = TRUE;
    /* A redirect must not turn a fixture into a request to another endpoint. */
    if (local && !WinHttpSetOption(request, WINHTTP_OPTION_REDIRECT_POLICY,
            &redirects, sizeof(redirects))) goto api_failed;
    if (!WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, context))
    {
        error = GetLastError();
        if (!async) goto rejected;
        if (error != ERROR_IO_PENDING) goto done;
    }
    if (async)
    {
        if (!wait_notification(state.progress, 10000)) goto api_failed;
        if (state.errors) { error = state.failure.dwError; goto rejected; }
        if (state.sends != 1 || state.send_order != 1 || state.bad) goto done;
        state.receive_order = InterlockedIncrement(&state.sequence);
    }
    if (!WinHttpReceiveResponse(request, NULL))
    {
        error = GetLastError();
        if (!async) goto rejected;
        if (error != ERROR_IO_PENDING) goto done;
    }
    if (async)
    {
        if (!wait_notification(state.progress, 10000)) goto api_failed;
        if (state.errors) { error = state.failure.dwError; goto rejected; }
        if (state.headers != 1 || state.headers_order != 3) goto done;
    }
    error = ERROR_SUCCESS;
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) goto api_failed;
    result = expected || status != 200;
    if (local && !result)
    {
        DWORD id = 0, id_size = sizeof(id);
        if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_CUSTOM | WINHTTP_QUERY_FLAG_NUMBER,
                L"X-Test-Connection", &id, &id_size, WINHTTP_NO_HEADER_INDEX)) goto api_failed;
        printf("INFO: server connection=%lu previous=%lu require_reuse=%d\n", id, last_connection_id, require_reuse);
        if ((require_reuse == 1 && id != last_connection_id) ||
                (require_reuse == -1 && id == last_connection_id)) result = 1;
        last_connection_id = id;
    }
    if (!result && drain_response)
    {
        DWORD read = 99;
        if (!WinHttpReadData(request, &state.body, 1, async ? NULL : &read)) goto api_failed;
        if (async && !wait_notification(state.progress, 10000)) goto api_failed;
        if (async ? state.reads != 1 : read != 0) result = 1;
    }
    goto done;
rejected:
    /* An absent root or extra reason must not pass a date/CN test. */
    result = !(expected && error == ERROR_WINHTTP_SECURE_FAILURE);
    goto done;
api_failed:
    result = 1;
    error = GetLastError();
done:
    if (request)
    {
        if (!WinHttpCloseHandle(request) || (subscribed && !wait_notification(state.closed, 5000)))
        {
            fputs("FAIL: request HANDLE_CLOSING missing; exiting without releasing callback state\n", stderr);
            fflush(stderr);
            /* Do not unwind the stack or close events while callbacks may still run. */
            ExitProcess(1);
        }
    }
    if (subscribed)
    {
        result |= state.bad || state.closes != 1 || state.close_order != state.sequence ||
                (DWORD)state.secure_failures != (subscribe_secure ? expected : 0) ||
                state.secure_notifications != !!(expected && subscribe_secure);
        if (async)
            result |= expected ? state.secure_order != subscribe_secure || state.errors != 1 ||
                    state.error_order != 1 + subscribe_secure ||
                    state.failure.dwResult != API_SEND_REQUEST || state.failure.dwError != ERROR_WINHTTP_SECURE_FAILURE ||
                    state.sends || state.headers || state.receive_order || state.close_order != 2 + subscribe_secure :
                    state.errors || state.sends != 1 || state.send_order != 1 || state.receive_order != 2 ||
                    state.headers != 1 || state.headers_order != 3 || state.close_order != 4;
        if (local) printf("INFO: %s callbacks secure=%ld send=%ld headers=%ld errors=%ld closing=%ld bad=%ld "
                "order=%ld/%ld/%ld/%ld/%ld/%ld async_error=%lu api=%llu\n", async ? "async" : "sync",
                state.secure_notifications, state.sends, state.headers, state.errors, state.closes, state.bad,
                state.secure_order, state.send_order, state.receive_order, state.headers_order,
                state.error_order, state.close_order, state.failure.dwError, (unsigned long long)state.failure.dwResult);
    }
    if (result) fprintf(stderr, "FAIL: HTTPS status=%lu error=%lu secure_flags=0x%08lx expected=0x%08lx callbacks=%ld\n",
            status, error, (DWORD)state.secure_failures, expected, state.secure_notifications);
    else if (expected) printf("PASS: HTTPS rejected with ERROR_WINHTTP_SECURE_FAILURE, secure_flags=0x%08lx\n",
            (DWORD)state.secure_failures);
    else if (local) puts("PASS: local HTTPS returned HTTP 200 with requested certificate policy");
    else puts("PASS: HTTPS game CDN request returned HTTP 200 (certificate rejection not tested)");
    if (connection) WinHttpCloseHandle(connection);
    if (session) WinHttpCloseHandle(session);
    active_context = NULL;
    if (state.progress) CloseHandle(state.progress);
    if (state.closed) CloseHandle(state.closed);
    return result;
}

int main(int argc, char **argv)
{
    WCHAR control[2048], url[2048], module_path[2048];
    char module_utf8[6144];
    HMODULE module;
    DWORD expected, length, initial_flags = 0, final_flags = 0;
    int pool = 0, no_subscription = 0, revocation = 0;
    int async = 0;

    if (argc == 1)
        return check_url(L"https://nadeo-download.cdn.ubi.com/trackmaniaforever/tmnationsforever_setup.exe", 0, 0, 0, 0, 0);
    if (!strcmp(argv[1], "--async")) { async = 1; --argc; ++argv; }
    if ((argc != 5 && argc != 7 && argc != 8) || strcmp(argv[1], "--local")) goto usage;
    if (argc >= 7)
    {
        char *end;
        initial_flags = strtoul(argv[5], &end, 0);
        if (!*argv[5] || *end || (initial_flags & ~0x3300u)) goto usage;
        final_flags = strtoul(argv[6], &end, 0);
        if (!*argv[6] || *end || (final_flags & ~0x3300u)) goto usage;
    }
    if (argc == 8)
    {
        if (!strcmp(argv[7], "pool")) pool = 1;
        else if (!strcmp(argv[7], "pool-revocation")) pool = revocation = 1;
        else if (!strcmp(argv[7], "no-subscription")) no_subscription = 1;
        else goto usage;
    }
    if (!strcmp(argv[4], "valid")) expected = 0;
    else if (!strcmp(argv[4], "untrusted")) expected = WINHTTP_CALLBACK_STATUS_FLAG_INVALID_CA;
    else if (!strcmp(argv[4], "expired")) expected = WINHTTP_CALLBACK_STATUS_FLAG_CERT_DATE_INVALID;
    else if (!strcmp(argv[4], "hostname")) expected = WINHTTP_CALLBACK_STATUS_FLAG_CERT_CN_INVALID;
    else if (!strcmp(argv[4], "revocation")) expected = WINHTTP_CALLBACK_STATUS_FLAG_CERT_REV_FAILED;
    else goto usage;
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, argv[2], -1, control, 2048) ||
            !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, argv[3], -1, url, 2048)) goto usage;
    module = GetModuleHandleW(L"winhttp.dll");
    length = module ? GetModuleFileNameW(module, module_path, 2048) : 0;
    if (!length || length >= 2048 || !WideCharToMultiByte(CP_UTF8, 0, module_path, -1,
            module_utf8, sizeof(module_utf8), NULL, NULL))
    {
        fputs("FAIL: cannot identify loaded winhttp module\n", stderr);
        return 1;
    }
    printf("INFO: winhttp module=%s\n", module_utf8);
    fflush(stdout);
    /* Never credit rejection when the same process cannot validate the test CA. */
    if (check_url(control, 0, 1, async, 0, 0))
    {
        fputs("FAIL: trusted positive control failed; rejection case was not run\n", stderr);
        return 1;
    }
    if (pool)
    {
        drain_response = 1;
        if (check_url(url, 0, 1, async, initial_flags, initial_flags)) return 1;
        require_reuse = 1;
        if (check_url(url, 0, 1, async, initial_flags, initial_flags)) return 1;
        require_reuse = -1;
    }
    subscribe_secure = !no_subscription;
    check_revocation = revocation;
    return check_url(url, expected, 1, async, initial_flags, final_flags);
usage:
    fputs("Usage: network.exe [[--async] --local CONTROL_URL CASE_URL valid|untrusted|expired|hostname|revocation "
            "[INITIAL_FLAGS FINAL_FLAGS [pool|pool-revocation|no-subscription]]]\n", stderr);
    return 2;
}
