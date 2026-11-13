/* Host-only check of the redirect decision in src/c/session_redirect.h.
 * Arguments: the command line, then the excepted switches; prints "redirect"
 * or "keep". */
#include <stdio.h>
#include <stdlib.h>
#include "../src/c/session_redirect.h"

#define CAP 8

static wchar_t *wide(const char *text)
{
    size_t length = mbstowcs(NULL, text, 0);
    wchar_t *result;
    if (length == (size_t)-1 || !(result = calloc(length + 1, sizeof(*result)))) exit(2);
    mbstowcs(result, text, length + 1);
    return result;
}

int main(int argc, char **argv)
{
    const wchar_t *except[CAP];
    wchar_t *command;
    size_t count = 0;
    int arg;
    if (argc < 2 || argc - 2 > CAP)
    {
        fputs("usage: COMMAND_LINE [EXCEPTED_SWITCH ...]\n", stderr);
        return 2;
    }
    command = wide(argv[1]);
    for (arg = 2; arg < argc; ++arg) except[count++] = wide(argv[arg]);
    puts(redirect_allowed(command, except, count) ? "redirect" : "keep");
    return 0;
}
