#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static volatile LONG callbacks, failures, io_completed, timer_callbacks, keep_polling, progress;

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s (error %lu)\n", __LINE__, #condition, GetLastError()); \
        InterlockedIncrement(&failures); \
    } \
} while (0)

static void CALLBACK callback(ULONG_PTR value)
{
    CHECK(value == 123);
    InterlockedIncrement(&callbacks);
}

static DWORD WINAPI queue_callbacks(void *target)
{
    for (int i = 0; i < 2000; ++i)
    {
        CHECK(QueueUserAPC(callback, target, 123));
        if (!(i % 13)) SwitchToThread();
    }
    return 0;
}

static DWORD WINAPI poll_thread(void *unused)
{
    (void)unused;
    CHECK(SleepEx(0, TRUE) == 0);
    CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
    CHECK(SleepEx(0, TRUE) == WAIT_IO_COMPLETION);
    return 0;
}

static void CALLBACK io_callback(DWORD error, DWORD transferred, OVERLAPPED *overlapped)
{
    (void)overlapped;
    CHECK(error == ERROR_SUCCESS);
    CHECK(transferred == 4);
    InterlockedIncrement(&io_completed);
}

static DWORD WINAPI suspendable_thread(void *unused)
{
    (void)unused;
    while (InterlockedCompareExchange(&keep_polling, 0, 0))
    {
        InterlockedIncrement(&progress);
        SleepEx(0, TRUE);
    }
    return 0;
}

static void CALLBACK timer_callback(void *arg, DWORD low, DWORD high)
{
    (void)arg;
    (void)low;
    (void)high;
    InterlockedIncrement(&timer_callbacks);
}

static void test_timer_cancellation(void)
{
    HANDLE timer = CreateWaitableTimerA(NULL, FALSE, NULL);
    LARGE_INTEGER due;
    due.QuadPart = -10000;
    CHECK(timer != NULL);
    for (int i = 0; i < 20; ++i)
    {
        CHECK(SetWaitableTimer(timer, &due, 0, timer_callback, NULL, FALSE));
        CHECK(WaitForSingleObject(timer, 5000) == WAIT_OBJECT_0);
        CHECK(CancelWaitableTimer(timer));
        CHECK(SleepEx(0, TRUE) == 0);
        CHECK(timer_callbacks == 0);
    }
    CHECK(SetWaitableTimer(timer, &due, 0, timer_callback, NULL, FALSE));
    ULONGLONG deadline = GetTickCount64() + 5000;
    while (!timer_callbacks && GetTickCount64() < deadline) SleepEx(0, TRUE);
    CHECK(timer_callbacks == 1);
    CloseHandle(timer);
}

static void test_io_and_suspend(void)
{
    char directory[MAX_PATH], path[MAX_PATH], bytes[4] = {0};
    OVERLAPPED overlapped = {0};
    DWORD written;
    CHECK(GetTempPathA(sizeof(directory), directory));
    CHECK(GetTempFileNameA(directory, "apc", 0, path));
    HANDLE file = CreateFileA(path, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(file != INVALID_HANDLE_VALUE);
    CHECK(WriteFile(file, "APC!", 4, &written, NULL));
    CHECK(written == 4);
    CloseHandle(file);
    file = CreateFileA(path, GENERIC_READ, 0, NULL, OPEN_EXISTING,
                       FILE_FLAG_OVERLAPPED | FILE_FLAG_DELETE_ON_CLOSE, NULL);
    CHECK(file != INVALID_HANDLE_VALUE);
    CHECK(ReadFileEx(file, bytes, sizeof(bytes), &overlapped, io_callback));
    ULONGLONG deadline = GetTickCount64() + 5000;
    while (!io_completed && GetTickCount64() < deadline) SleepEx(0, TRUE);
    CHECK(io_completed == 1);
    CHECK(!memcmp(bytes, "APC!", 4));
    CloseHandle(file);

    keep_polling = 1;
    HANDLE thread = CreateThread(NULL, 0, suspendable_thread, NULL, 0, NULL);
    CHECK(thread != NULL);
    deadline = GetTickCount64() + 5000;
    while (!progress && GetTickCount64() < deadline) Sleep(1);
    CHECK(progress > 0);
    CHECK(SuspendThread(thread) == 0);
    /* GetThreadContext waits until Wine has actually suspended the target. */
    CONTEXT context = {0};
    context.ContextFlags = CONTEXT_CONTROL;
    CHECK(GetThreadContext(thread, &context));
    LONG stopped_at = InterlockedCompareExchange(&progress, 0, 0);
    CHECK(QueueUserAPC(callback, thread, 123));
    Sleep(10);
    CHECK(progress == stopped_at);
    LONG previous_callbacks = callbacks;
    CHECK(ResumeThread(thread) == 1);
    deadline = GetTickCount64() + 5000;
    while (callbacks == previous_callbacks && GetTickCount64() < deadline) Sleep(1);
    CHECK(callbacks == previous_callbacks + 1);
    InterlockedExchange(&keep_polling, 0);
    CHECK(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0);
    CHECK(progress > stopped_at);
    CloseHandle(thread);
}

int main(void)
{
    LARGE_INTEGER frequency, begin, end;
    HANDLE self, producer;
    DWORD result;
    const DWORD sentinel = 0x12345678;
    const int iterations = 100000;

    /* An APC queued before the first alert descriptor is created must survive. */
    CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
    SetLastError(sentinel);
    CHECK(SleepEx(0, TRUE) == WAIT_IO_COMPLETION);
    CHECK(GetLastError() == sentinel);
    CHECK(callbacks == 1);

    /* Empty polling, followed by a new APC, exercises notification rearming. */
    for (int i = 0; i < 100; ++i)
    {
        CHECK(SleepEx(0, TRUE) == 0);
        CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
        CHECK(SleepEx(0, FALSE) == 0);
        CHECK(callbacks == i + 1);
        CHECK(SleepEx(0, TRUE) == WAIT_IO_COMPLETION);
        CHECK(callbacks == i + 2);
        CHECK(GetLastError() == sentinel);
    }

    /* Poll while another thread queues APCs. None may be lost or duplicated. */
    CHECK(DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(),
                          &self, 0, FALSE, DUPLICATE_SAME_ACCESS));
    producer = CreateThread(NULL, 0, queue_callbacks, self, 0, NULL);
    CHECK(producer != NULL);
    ULONGLONG deadline = GetTickCount64() + 10000;
    while (callbacks < 2101 && GetTickCount64() < deadline)
    {
        result = SleepEx(0, TRUE);
        CHECK(result == 0 || result == WAIT_IO_COMPLETION);
    }
    CHECK(WaitForSingleObject(producer, 10000) == WAIT_OBJECT_0);
    CHECK(callbacks == 2101);
    CHECK(SleepEx(0, TRUE) == 0);
    CloseHandle(producer);
    CloseHandle(self);

    /* Alert descriptor lifetime must follow thread lifetime, including reuse. */
    for (int i = 0; i < 100; ++i)
    {
        HANDLE thread = CreateThread(NULL, 0, poll_thread, NULL, 0, NULL);
        CHECK(thread != NULL);
        CHECK(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0);
        CloseHandle(thread);
    }
    CHECK(callbacks == 2201);

    /* Finite and infinite alertable waits still use the regular server path. */
    CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
    CHECK(SleepEx(INFINITE, TRUE) == WAIT_IO_COMPLETION);
    CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
    CHECK(SleepEx(10, TRUE) == WAIT_IO_COMPLETION);
    CHECK(callbacks == 2203);
    CHECK(SleepEx(1, TRUE) == 0);

    test_io_and_suspend();
    test_timer_cancellation();

    /* Check the NT return values too; SleepEx translates these statuses. */
    typedef LONG (WINAPI *delay_fn)(BOOLEAN, const LARGE_INTEGER *);
    delay_fn delay = (delay_fn)(void *)GetProcAddress(GetModuleHandleA("ntdll.dll"), "NtDelayExecution");
    CHECK(delay != NULL);
    if (!delay) return 1;
    SetLastError(sentinel);
    LARGE_INTEGER timeout = {0};
    CHECK(delay(TRUE, &timeout) == 0);
    CHECK(QueueUserAPC(callback, GetCurrentThread(), 123));
    CHECK(delay(TRUE, &timeout) == 0xc0);
    timeout.QuadPart = -10000;
    CHECK(delay(TRUE, &timeout) == 0);
    timeout.QuadPart = 1; /* An absolute deadline in 1601 is already expired. */
    CHECK(delay(TRUE, &timeout) == 0);
    CHECK(GetLastError() == sentinel);

    QueryPerformanceFrequency(&frequency);
    QueryPerformanceCounter(&begin);
    for (int i = 0; i < iterations; ++i) SleepEx(0, TRUE);
    QueryPerformanceCounter(&end);
    double microseconds = 1000000.0 * (end.QuadPart - begin.QuadPart) / frequency.QuadPart / iterations;
    printf("SleepEx(0, TRUE): %.3f us/call, %d calls\n", microseconds, iterations);
    const char *limit = getenv("MACTMNF_MAX_ZERO_WAIT_US");
    if (limit) CHECK(microseconds < strtod(limit, NULL));
    printf("APC callbacks: %ld; failures: %ld\n", callbacks, failures);
    return failures ? 1 : 0;
}
