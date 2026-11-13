/* Whether session.c may redirect a process, judged from its command line.
 * Kept free of Windows types so a host compiler can unit test the decision.
 *
 * A launcher starts the same game file for play and for other jobs, such as a
 * benchmark. The host names those jobs' switches; a process whose arguments
 * start with one of them, ignoring case, keeps running. The program name is
 * skipped as Windows splits it: up to its closing quote, or the first blank.
 * Arguments follow the Windows C runtime's quoting, so a quoted argument is
 * one argument and a switch inside it is not a switch. Text right after the
 * program name's closing quote is read as an argument, so a switch there still
 * counts; finding a switch only ever keeps the process running. */
#ifndef TRACKMANIA_MAC_SESSION_REDIRECT_H
#define TRACKMANIA_MAC_SESSION_REDIRECT_H

#include <stddef.h>
#include <wchar.h>
#include <wctype.h>

static inline int redirect_starts_with(const wchar_t *text, const wchar_t *prefix)
{
    for (; *prefix; ++text, ++prefix)
        if (!*text || towlower((wint_t)*text) != towlower((wint_t)*prefix)) return 0;
    return 1;
}

static inline int redirect_blank(wchar_t c)
{
    return c == L' ' || c == L'\t';
}

static inline int redirect_allowed(const wchar_t *command, const wchar_t *const *except, size_t count)
{
    const wchar_t *c = command;
    size_t i;
    if (*c == L'"')
    {
        for (++c; *c && *c != L'"'; ++c) {}
        if (*c) ++c;
    }
    else
        while (*c && !redirect_blank(*c)) ++c;
    for (;;)
    {
        const wchar_t *start;
        int quoted = 0;
        while (redirect_blank(*c)) ++c;
        if (!*c) return 1;
        /* An argument may open with a quote: "/bench=x" is still /bench. */
        for (start = c; *start == L'"'; ++start) {}
        for (i = 0; i < count; ++i)
            if (redirect_starts_with(start, except[i])) return 0;
        while (*c && (quoted || !redirect_blank(*c)))
        {
            size_t slashes = 0;
            while (*c == L'\\') { ++slashes; ++c; }
            if (*c == L'"')
            {
                /* 2n backslashes and a quote toggle quoting; 2n+1 make it literal. */
                if (!(slashes % 2)) quoted = !quoted;
                ++c;
            }
            else if (!slashes && *c)
                ++c;
        }
    }
}

#endif
