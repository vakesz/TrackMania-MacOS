#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <stdio.h>

#define WAIT_MS 5000
#define WORKERS 4
#define ITERATIONS 64
#define TIMER_FLAGS (TIME_PERIODIC | TIME_CALLBACK_FUNCTION | TIME_KILL_SYNCHRONOUS)
#define READ(value) InterlockedCompareExchange(&(value), 0, 0)

static volatile LONG failures;
static HANDLE finished, workers_start;

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s (error %lu)\n", __LINE__, #condition, GetLastError()); \
        InterlockedIncrement(&failures); \
    } \
} while (0)

/* Do not unwind contexts or close handles while a timed-out callback uses them. */
#define REQUIRE(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FATAL line %d: %s (error %lu)\n", __LINE__, #condition, GetLastError()); \
        TerminateProcess(GetCurrentProcess(), 2); \
        return 2; \
    } \
} while (0)

struct timer_context
{
    HANDLE fired;
    volatile LONG calls;
};

struct reentry_context
{
    HANDLE done;
    struct timer_context child;
    volatile LONG entered;
};

struct cancellation_context
{
    HANDLE entered, release, killing;
    UINT timer;
    volatile LONG calls, active;
};

static DWORD WINAPI watchdog(void *unused)
{
    (void)unused;
    /* timeSetEvent/timeKillEvent have no timeout argument. */
    if (WaitForSingleObject(finished, 45000) != WAIT_OBJECT_0)
    {
        fprintf(stderr, "FAIL: multimedia timer probe exceeded 45 seconds\n");
        TerminateProcess(GetCurrentProcess(), 2);
    }
    return 0;
}

static void CALLBACK count_callback(UINT id, UINT message, DWORD_PTR user,
                                    DWORD_PTR first, DWORD_PTR second)
{
    struct timer_context *context = (void *)user;
    (void)id;
    (void)message;
    (void)first;
    (void)second;
    InterlockedIncrement(&context->calls);
    CHECK(SetEvent(context->fired));
}

static void CALLBACK reentry_callback(UINT id, UINT message, DWORD_PTR user,
                                      DWORD_PTR first, DWORD_PTR second)
{
    struct reentry_context *context = (void *)user;
    UINT child;
    (void)id;
    (void)message;
    (void)first;
    (void)second;
    if (InterlockedCompareExchange(&context->entered, 1, 0)) return;

    /* Never synchronously kill ourselves. This different timer cannot become
     * due before the watchdog, so it has no in-flight callback to wait for. */
    child = timeSetEvent(60000, 1, count_callback, (DWORD_PTR)&context->child, TIMER_FLAGS);
    CHECK(child != 0);
    if (child) CHECK(timeKillEvent(child) == TIMERR_NOERROR);
    CHECK(SetEvent(context->done));
}

static DWORD test_reentry(void)
{
    struct reentry_context context = {0};
    UINT timer;
    context.done = CreateEventA(NULL, TRUE, FALSE, NULL);
    context.child.fired = CreateEventA(NULL, TRUE, FALSE, NULL);
    REQUIRE(context.done != NULL && context.child.fired != NULL);
    timer = timeSetEvent(1, 1, reentry_callback, (DWORD_PTR)&context, TIMER_FLAGS);
    REQUIRE(timer != 0);
    REQUIRE(WaitForSingleObject(context.done, WAIT_MS) == WAIT_OBJECT_0);
    REQUIRE(timeKillEvent(timer) == TIMERR_NOERROR);
    CHECK(READ(context.entered) == 1);
    CHECK(READ(context.child.calls) == 0);
    CloseHandle(context.child.fired);
    CloseHandle(context.done);
    printf("Callback reentry: set/kill a different synchronous timer\n");
    return 0;
}

static DWORD WINAPI set_kill_worker(void *arg)
{
    struct timer_context *context = arg;
    REQUIRE(WaitForSingleObject(workers_start, WAIT_MS) == WAIT_OBJECT_0);
    for (int i = 0; i < ITERATIONS; ++i)
    {
        LONG stopped;
        UINT timer;
        REQUIRE(ResetEvent(context->fired));
        timer = timeSetEvent(1, 1, count_callback, (DWORD_PTR)context, TIMER_FLAGS);
        REQUIRE(timer != 0);
        REQUIRE(WaitForSingleObject(context->fired, WAIT_MS) == WAIT_OBJECT_0);
        REQUIRE(timeKillEvent(timer) == TIMERR_NOERROR);
        stopped = READ(context->calls);
        REQUIRE(ResetEvent(context->fired));
        CHECK(WaitForSingleObject(context->fired, 5) == WAIT_TIMEOUT);
        CHECK(READ(context->calls) == stopped);
    }
    return 0;
}

static DWORD test_concurrent_set_kill(void)
{
    struct timer_context contexts[WORKERS] = {0};
    HANDLE threads[WORKERS];
    workers_start = CreateEventA(NULL, TRUE, FALSE, NULL);
    REQUIRE(workers_start != NULL);
    for (int i = 0; i < WORKERS; ++i)
    {
        contexts[i].fired = CreateEventA(NULL, TRUE, FALSE, NULL);
        REQUIRE(contexts[i].fired != NULL);
        threads[i] = CreateThread(NULL, 0, set_kill_worker, &contexts[i], 0, NULL);
        REQUIRE(threads[i] != NULL);
    }
    REQUIRE(SetEvent(workers_start));
    REQUIRE(WaitForMultipleObjects(WORKERS, threads, TRUE, 15000) == WAIT_OBJECT_0);
    for (int i = 0; i < WORKERS; ++i)
    {
        CHECK(READ(contexts[i].calls) >= ITERATIONS);
        CloseHandle(threads[i]);
        CloseHandle(contexts[i].fired);
    }
    CloseHandle(workers_start);
    printf("Concurrent set/kill: %d workers, %d timers each\n", WORKERS, ITERATIONS);
    return 0;
}

static void CALLBACK blocking_callback(UINT id, UINT message, DWORD_PTR user,
                                       DWORD_PTR first, DWORD_PTR second)
{
    struct cancellation_context *context = (void *)user;
    (void)id;
    (void)message;
    (void)first;
    (void)second;
    InterlockedIncrement(&context->calls);
    InterlockedIncrement(&context->active);
    CHECK(SetEvent(context->entered));
    /* A controlled, finite hold lets the caller observe synchronous kill. */
    CHECK(WaitForSingleObject(context->release, WAIT_MS) == WAIT_OBJECT_0);
    InterlockedDecrement(&context->active);
}

static DWORD WINAPI kill_worker(void *arg)
{
    struct cancellation_context *context = arg;
    REQUIRE(SetEvent(context->killing));
    REQUIRE(timeKillEvent(context->timer) == TIMERR_NOERROR);
    CHECK(READ(context->active) == 0);
    return 0;
}

static DWORD test_synchronous_cancellation(void)
{
    struct cancellation_context context = {0};
    HANDLE thread;
    LONG stopped;
    context.entered = CreateEventA(NULL, TRUE, FALSE, NULL);
    context.release = CreateEventA(NULL, TRUE, FALSE, NULL);
    context.killing = CreateEventA(NULL, TRUE, FALSE, NULL);
    REQUIRE(context.entered != NULL && context.release != NULL && context.killing != NULL);
    context.timer = timeSetEvent(1, 1, blocking_callback, (DWORD_PTR)&context, TIMER_FLAGS);
    REQUIRE(context.timer != 0);
    REQUIRE(WaitForSingleObject(context.entered, WAIT_MS) == WAIT_OBJECT_0);
    thread = CreateThread(NULL, 0, kill_worker, &context, 0, NULL);
    REQUIRE(thread != NULL);
    REQUIRE(WaitForSingleObject(context.killing, WAIT_MS) == WAIT_OBJECT_0);
    CHECK(WaitForSingleObject(thread, 100) == WAIT_TIMEOUT);
    REQUIRE(SetEvent(context.release));
    REQUIRE(WaitForSingleObject(thread, WAIT_MS) == WAIT_OBJECT_0);
    CHECK(READ(context.active) == 0);
    stopped = READ(context.calls);
    CHECK(stopped >= 1);
    REQUIRE(ResetEvent(context.entered));
    CHECK(WaitForSingleObject(context.entered, 100) == WAIT_TIMEOUT);
    CHECK(READ(context.calls) == stopped);
    CloseHandle(thread);
    CloseHandle(context.killing);
    CloseHandle(context.release);
    CloseHandle(context.entered);
    printf("Synchronous cancellation: in-flight callback drained, no later callbacks\n");
    return 0;
}

static DWORD test_service_restart(void)
{
    struct timer_context context = {0};
    context.fired = CreateEventA(NULL, TRUE, FALSE, NULL);
    REQUIRE(context.fired != NULL);
    for (int i = 0; i < 20; ++i)
    {
        LONG before = READ(context.calls), stopped;
        UINT timer;
        REQUIRE(ResetEvent(context.fired));
        timer = timeSetEvent(1, 1, count_callback, (DWORD_PTR)&context, TIMER_FLAGS);
        REQUIRE(timer != 0);
        REQUIRE(WaitForSingleObject(context.fired, WAIT_MS) == WAIT_OBJECT_0);
        REQUIRE(timeKillEvent(timer) == TIMERR_NOERROR);
        stopped = READ(context.calls);
        CHECK(stopped > before);
        REQUIRE(ResetEvent(context.fired));
        /* No timers remain. Allow the service to retire before creating another.
         * The public API cannot confirm the service thread actually exited. */
        CHECK(WaitForSingleObject(context.fired, 20) == WAIT_TIMEOUT);
        CHECK(READ(context.calls) == stopped);
    }
    CloseHandle(context.fired);
    printf("Timer service restart: 20 empty-to-active cycles\n");
    return 0;
}

int main(void)
{
    HANDLE guard;
    finished = CreateEventA(NULL, TRUE, FALSE, NULL);
    REQUIRE(finished != NULL);
    guard = CreateThread(NULL, 0, watchdog, NULL, 0, NULL);
    REQUIRE(guard != NULL);
    if (test_reentry() || test_concurrent_set_kill() ||
        test_synchronous_cancellation() || test_service_restart()) return 2;
    REQUIRE(SetEvent(finished));
    REQUIRE(WaitForSingleObject(guard, WAIT_MS) == WAIT_OBJECT_0);
    CloseHandle(guard);
    CloseHandle(finished);
    printf("Multimedia timers: failures: %ld\n", READ(failures));
    return READ(failures) ? 1 : 0;
}
