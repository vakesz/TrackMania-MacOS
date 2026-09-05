/* Verify that the bundled TLS implementation can reach the game's CDN. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <winhttp.h>
#include <stdio.h>

int main(void)
{
    HINTERNET session, connection = NULL, request = NULL;
    DWORD status = 0, size = sizeof(status);
    int result = 1;
    session = WinHttpOpen(L"MacTMNF runtime test", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
            WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) goto done;
    if (!WinHttpSetTimeouts(session, 15000, 15000, 15000, 15000)) goto done;
    connection = WinHttpConnect(session, L"nadeo-download.cdn.ubi.com", 443, 0);
    if (!connection) goto done;
    request = WinHttpOpenRequest(connection, L"HEAD", L"/trackmaniaforever/tmnationsforever_setup.exe",
            NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE);
    if (!request || !WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
            WINHTTP_NO_REQUEST_DATA, 0, 0, 0) || !WinHttpReceiveResponse(request, NULL)) goto done;
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) goto done;
    result = status != 200;
done:
    if (result) fprintf(stderr, "TLS test failed: status=%lu error=%lu\n", status, GetLastError());
    else puts("PASS: HTTPS certificate verification and game CDN request");
    if (request) WinHttpCloseHandle(request);
    if (connection) WinHttpCloseHandle(connection);
    if (session) WinHttpCloseHandle(session);
    return result;
}
