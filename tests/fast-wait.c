/* test_fast_wait.py inserts the actual patched Wine function and thread flag.
 * Only Wine dependencies and fault injection are stubs; pipes and poll are real. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <unistd.h>

typedef int BOOL;
typedef int BOOLEAN;
typedef unsigned int NTSTATUS;
typedef int64_t timeout_t;
typedef struct { int64_t QuadPart; } LARGE_INTEGER;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define STATUS_SUCCESS 0
#define STATUS_TIMEOUT 258
#define STATUS_NO_YIELD_PERFORMED 0x40000024
#define TIMEOUT_INFINITE INT64_MAX
#define SELECT_INTERRUPTIBLE 1
#define SELECT_ALERTABLE 2

struct thread_data {
    int alert_fd;
    /* THREAD_FIELD */
};
static struct thread_data threads[2], *current = threads;
static int acquisitions, polls, yields, waits, poll_error, poll_events;
static unsigned int server_status;
static struct thread_data *get_thread_data(void) { return current; }
static int get_inproc_alert_fd(void) { ++acquisitions; return current->alert_fd; }
static NTSTATUS NtYieldExecution(void) { ++yields; return STATUS_NO_YIELD_PERFORMED; }
static void NtQuerySystemTime(LARGE_INTEGER *now) { now->QuadPart = 100000; }
static NTSTATUS server_wait(void *handles, int count, int flags, const LARGE_INTEGER *timeout)
{
    (void)handles; (void)count; (void)timeout;
    assert(flags == (SELECT_INTERRUPTIBLE | SELECT_ALERTABLE));
    ++waits;
    return server_status;
}
static int test_poll(struct pollfd *fds, nfds_t count, int timeout)
{
    ++polls;
    assert(count == 1 && timeout == 0 && fds->events == POLLIN);
    if (poll_error) { errno = poll_error; return -1; }
    if (poll_events) { fds->revents = poll_events; return 1; }
    return poll(fds, count, timeout);
}
#define poll test_poll
/* CLIENT_FUNCTION */
#undef poll

static void reset(int fd)
{
    struct thread_data *data = get_thread_data();
    memset(data, 0xff, sizeof(*data));
    data->alert_fd = fd;
    /* THREAD_INIT */
    acquisitions = polls = yields = waits = poll_error = poll_events = 0;
    server_status = STATUS_TIMEOUT;
}

int main(void)
{
    LARGE_INTEGER zero = {0}, finite = {-1};
    int fds[2];
    assert(pipe(fds) == 0);
    assert(unsetenv("WINEDARWINFASTWAIT") == 0);
    reset(fds[0]);
#ifdef __APPLE__
    char byte;
    assert(!current->fast_wait_disabled);
    for (int i = 0; i < 100; ++i) assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
    assert(polls == 100 && yields == 100 && waits == 0);
    /* A readable byte survives the client. Server drain permits rearming. */
    for (int i = 0; i < 10; ++i)
    {
        assert(write(fds[1], "A", 1) == 1);
        server_status = 0xc0;
        assert(NtDelayExecution(TRUE, &zero) == 0xc0);
        assert(read(fds[0], &byte, 1) == 1 && byte == 'A');
        assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
        assert(!current->fast_wait_disabled);
    }
    reset(fds[0]);
    assert(setenv("WINEDARWINFASTWAIT", "0", 1) == 0);
    assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
    assert(!acquisitions && !polls && !yields && waits == 1);
    assert(unsetenv("WINEDARWINFASTWAIT") == 0);
    assert(NtDelayExecution(TRUE, &finite) == STATUS_SUCCESS);
    assert(NtDelayExecution(TRUE, NULL) == STATUS_SUCCESS);
    assert(NtDelayExecution(FALSE, &zero) == STATUS_NO_YIELD_PERFORMED);
    assert(!acquisitions && !polls && yields == 1 && waits == 3);

    /* Acquisition failure is bounded, including repeated calls afterwards. */
    reset(-1);
    for (int i = 0; i < 100; ++i) assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
    assert(current->fast_wait_disabled && acquisitions == 1 && !polls && !yields && waits == 100);
    server_status = 0xc0;
    assert(NtDelayExecution(TRUE, &zero) == 0xc0 && acquisitions == 1 && waits == 101);
    /* Another thread starts enabled; failure state is not process-global. */
    current = threads + 1;
    reset(fds[0]);
    assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS && polls == 1 && !waits);
    current = threads;
    assert(current->fast_wait_disabled);

    int errors[] = {EINTR, EAGAIN, ENOMEM, EINVAL, EBADF};
    for (unsigned int i = 0; i < sizeof(errors) / sizeof(errors[0]); ++i)
    {
        reset(fds[0]);
        poll_error = errors[i];
        assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS && waits == 1);
        assert(current->fast_wait_disabled == (errors[i] == EBADF));
        poll_error = 0;
        assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
        assert(polls == (errors[i] == EBADF ? 1 : 2));
        assert(waits == (errors[i] == EBADF ? 2 : 1));
        assert(fcntl(fds[0], F_GETFD) != -1);
    }
    reset(fds[0]);
    poll_events = POLLERR;
    assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS && waits == 1);
    assert(!current->fast_wait_disabled);
    poll_events = 0;
    assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS && waits == 1);

    int high = fcntl(fds[0], F_DUPFD, FD_SETSIZE);
    assert(high >= FD_SETSIZE);
    reset(high);
    assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS && polls == 1 && !waits);
    close(high);
    reset(high); /* Real POLLNVAL. */
    for (int i = 0; i < 100; ++i) assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
    assert(current->fast_wait_disabled && acquisitions == 1 && polls == 1 && yields == 1 && waits == 100);
    assert(current->alert_fd == high);

    /* HUP with unread data, then empty EOF. Neither may loop in the shortcut. */
    assert(write(fds[1], "A", 1) == 1);
    close(fds[1]);
    for (int buffered = 1; buffered >= 0; --buffered)
    {
        reset(fds[0]);
        for (int i = 0; i < 100; ++i) assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
        assert(current->fast_wait_disabled && acquisitions == 1 && polls == 1 && yields == 1 && waits == 100);
        assert(current->alert_fd == fds[0] && fcntl(fds[0], F_GETFD) != -1);
        assert(read(fds[0], &byte, 1) == buffered);
    }
#else
    (void)finite;
    (void)get_inproc_alert_fd;
    (void)test_poll;
    for (int i = 0; i < 100; ++i) assert(NtDelayExecution(TRUE, &zero) == STATUS_SUCCESS);
    assert(!acquisitions && !polls && !yields && waits == 100);
    close(fds[1]);
#endif
    close(fds[0]);
    puts("compiled NtDelayExecution pipe regressions passed");
    return 0;
}
