/* HEAD https://HOST:PORT/online_game/request.php through WinINet the way
   TrackMania Forever creates accounts: INTERNET_FLAG_SECURE plus the
   ignore-certificate flags (0x80F000), and on certificate errors 12045, 12037
   and 12038 add 0xF380 to INTERNET_OPTION_SECURITY_FLAGS and retry. Prints
   "HTTP <status>" on success. */
#include <windows.h>
#include <wininet.h>
#include <stdio.h>
#include <stdlib.h>

#define GAME_FLAGS (INTERNET_FLAG_RELOAD | INTERNET_FLAG_NO_CACHE_WRITE | \
        INTERNET_FLAG_KEEP_CONNECTION | INTERNET_FLAG_NO_UI | 0x80F000)
#define GAME_RETRY_FLAGS 0xF380

int main(int argc, char **argv)
{
    HINTERNET session, connection, request;
    DWORD port, status = 0, size, flags, error;
    int attempt;

    if (argc != 3)
    {
        fprintf(stderr, "usage: inet_legacy HOST PORT\n");
        return 2;
    }
    port = (DWORD)strtoul(argv[2], NULL, 10);
    session = InternetOpenA("GameBox", INTERNET_OPEN_TYPE_DIRECT, NULL, NULL, 0);
    connection = session ? InternetConnectA(session, argv[1], (INTERNET_PORT)port, NULL, NULL,
            INTERNET_SERVICE_HTTP, 0, 0) : NULL;
    request = connection ? HttpOpenRequestA(connection, "HEAD", "/online_game/request.php",
            "HTTP/1.1", NULL, NULL, GAME_FLAGS | INTERNET_FLAG_SECURE, 0) : NULL;
    if (!request)
    {
        printf("open failed %lu\n", GetLastError());
        return 2;
    }
    for (attempt = 0; attempt < 3; attempt++)
    {
        if (HttpSendRequestA(request, NULL, 0, NULL, 0))
        {
            size = sizeof(status);
            if (!HttpQueryInfoA(request, HTTP_QUERY_STATUS_CODE | HTTP_QUERY_FLAG_NUMBER,
                    &status, &size, NULL))
            {
                printf("status query failed %lu\n", GetLastError());
                return 1;
            }
            printf("attempt %d\nHTTP %lu\n", attempt, status);
            return 0;
        }
        error = GetLastError();
        printf("attempt %d error %lu\n", attempt, error);
        if (error != 12045 && error != 12037 && error != 12038) return 1;
        size = sizeof(flags);
        if (!InternetQueryOptionA(request, INTERNET_OPTION_SECURITY_FLAGS, &flags, &size))
            flags = 0;
        flags |= GAME_RETRY_FLAGS;
        InternetSetOptionA(request, INTERNET_OPTION_SECURITY_FLAGS, &flags, sizeof(flags));
    }
    return 1;
}
