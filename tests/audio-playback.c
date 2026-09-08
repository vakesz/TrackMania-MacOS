#define COBJMACROS
#define DIRECTSOUND_VERSION 0x0800
#include <windows.h>
#include <dsound.h>
#include <stdio.h>

static double milliseconds(void)
{
    LARGE_INTEGER now, frequency;
    QueryPerformanceCounter(&now);
    QueryPerformanceFrequency(&frequency);
    return now.QuadPart * 1000.0 / frequency.QuadPart;
}

static int check(HRESULT result, const char *operation)
{
    if (SUCCEEDED(result)) return 1;
    printf("FAIL %s: 0x%08lx\n", operation, result);
    return 0;
}

static DWORD WINAPI watchdog(void *done)
{
    if (WaitForSingleObject(done, 15000) == WAIT_TIMEOUT)
    {
        fprintf(stderr, "FAIL audio startup/playback exceeded 15 seconds\n");
        fflush(stderr);
        TerminateProcess(GetCurrentProcess(), 1);
    }
    return 0;
}

/* Exercise stereo playback clocks and mono 3D buffer/listener API state.
 * A moving DirectSound cursor depends on the actual CoreAudio render clock.
 * The quiet triangle wave permits a manual listening check, but this probe
 * does not verify audibility or spatialization. */
static int playback(IDirectSound8 *sound, WORD channels, DWORD rate)
{
    WAVEFORMATEX format = {WAVE_FORMAT_PCM, channels, rate, rate * channels * 2,
                          channels * 2, 16, 0};
    DSBUFFERDESC description = {0};
    IDirectSoundBuffer *buffer = NULL;
    IDirectSoundBuffer *primary = NULL;
    IDirectSound3DBuffer *spatial = NULL;
    IDirectSound3DListener *listener = NULL;
    void *first = NULL, *second = NULL;
    DWORD first_bytes, second_bytes, previous, position, status;
    DWORD advanced = 0;
    double begin, last_move, now, max_stall = 0;
    int result = 1;

    description.dwSize = sizeof(description);
    description.dwFlags = DSBCAPS_GLOBALFOCUS | DSBCAPS_GETCURRENTPOSITION2;
    if (channels == 1) description.dwFlags |= DSBCAPS_CTRL3D;
    description.dwBufferBytes = format.nAvgBytesPerSec;
    description.lpwfxFormat = &format;
    begin = milliseconds();
    if (!check(IDirectSound8_CreateSoundBuffer(sound, &description, &buffer, NULL), "CreateSoundBuffer"))
        return 1;
    printf("Created %lu Hz %u-channel buffer in %.1f ms\n", rate, channels, milliseconds() - begin);
    fflush(stdout);
    if (channels == 1)
    {
        DSBUFFERDESC primary_description = {0};
        DS3DBUFFER state = {0};
        DS3DLISTENER listener_state = {0};
        primary_description.dwSize = sizeof(primary_description);
        primary_description.dwFlags = DSBCAPS_PRIMARYBUFFER | DSBCAPS_CTRL3D;
        state.dwSize = sizeof(state);
        listener_state.dwSize = sizeof(listener_state);
        if (!check(IDirectSound8_CreateSoundBuffer(sound, &primary_description, &primary, NULL),
                   "Create primary 3D buffer") ||
            !check(IDirectSoundBuffer_QueryInterface(primary, &IID_IDirectSound3DListener,
                   (void **)&listener), "Query 3D listener") ||
            !check(IDirectSoundBuffer_QueryInterface(buffer, &IID_IDirectSound3DBuffer,
                   (void **)&spatial), "Query 3D buffer")) goto done;
        if (!check(IDirectSound3DBuffer_SetPosition(spatial, 3.0f, 0.0f, 4.0f, DS3D_IMMEDIATE), "Buffer SetPosition") ||
            !check(IDirectSound3DBuffer_SetMinDistance(spatial, 2.0f, DS3D_IMMEDIATE), "Buffer SetMinDistance") ||
            !check(IDirectSound3DBuffer_SetMaxDistance(spatial, 50.0f, DS3D_IMMEDIATE), "Buffer SetMaxDistance") ||
            !check(IDirectSound3DBuffer_SetMode(spatial, DS3DMODE_HEADRELATIVE, DS3D_IMMEDIATE), "Buffer SetMode") ||
            !check(IDirectSound3DBuffer_GetAllParameters(spatial, &state), "Buffer GetAllParameters")) goto done;
        if (state.vPosition.x != 3.0f || state.vPosition.y != 0.0f || state.vPosition.z != 4.0f ||
            state.flMinDistance != 2.0f || state.flMaxDistance != 50.0f || state.dwMode != DS3DMODE_HEADRELATIVE)
        {
            puts("FAIL: 3D buffer set/query state mismatch");
            goto done;
        }
        if (!check(IDirectSound3DListener_SetPosition(listener, 1.0f, 2.0f, 3.0f, DS3D_DEFERRED), "Listener SetPosition") ||
            !check(IDirectSound3DListener_SetOrientation(listener, 1.0f, 0.0f, 0.0f,
                   0.0f, 1.0f, 0.0f, DS3D_DEFERRED), "Listener SetOrientation") ||
            !check(IDirectSound3DListener_SetDistanceFactor(listener, 0.5f, DS3D_DEFERRED), "Listener SetDistanceFactor") ||
            !check(IDirectSound3DListener_CommitDeferredSettings(listener), "Listener CommitDeferredSettings") ||
            !check(IDirectSound3DListener_GetAllParameters(listener, &listener_state), "Listener GetAllParameters")) goto done;
        if (listener_state.vPosition.x != 1.0f || listener_state.vPosition.y != 2.0f || listener_state.vPosition.z != 3.0f ||
            listener_state.vOrientFront.x != 1.0f || listener_state.vOrientFront.y != 0.0f || listener_state.vOrientFront.z != 0.0f ||
            listener_state.vOrientTop.x != 0.0f || listener_state.vOrientTop.y != 1.0f || listener_state.vOrientTop.z != 0.0f ||
            listener_state.flDistanceFactor != 0.5f)
        {
            puts("FAIL: 3D listener set/commit/query state mismatch");
            goto done;
        }
        puts("PASS: 3D buffer and listener set/query state (spatialization not verified)");
    }
    if (!check(IDirectSoundBuffer_Lock(buffer, 0, description.dwBufferBytes, &first,
                                      &first_bytes, &second, &second_bytes, 0), "Lock")) goto done;
    for (DWORD i = 0; i < first_bytes / 2; ++i)
    {
        unsigned phase = (i / channels) % 128;
        ((short *)first)[i] = (phase < 64 ? (int)phase - 32 : 96 - (int)phase) * 24;
    }
    if (second_bytes) ZeroMemory(second, second_bytes);
    if (!check(IDirectSoundBuffer_Unlock(buffer, first, first_bytes, second, second_bytes), "Unlock")) goto done;
    if (!check(IDirectSoundBuffer_Play(buffer, 0, 0, DSBPLAY_LOOPING), "Play")) goto done;
    if (!check(IDirectSoundBuffer_GetCurrentPosition(buffer, &previous, NULL), "GetCurrentPosition")) goto done;
    begin = last_move = milliseconds();
    do
    {
        Sleep(10);
        now = milliseconds();
        if (!check(IDirectSoundBuffer_GetCurrentPosition(buffer, &position, NULL), "GetCurrentPosition")) goto done;
        if (position != previous)
        {
            advanced += (position + description.dwBufferBytes - previous) % description.dwBufferBytes;
            if (now - last_move > max_stall) max_stall = now - last_move;
            last_move = now;
        }
        previous = position;
    } while (now - begin < 1200);
    if (now - last_move > max_stall) max_stall = now - last_move;
    if (!check(IDirectSoundBuffer_GetStatus(buffer, &status), "GetStatus")) goto done;
    double played_ms = advanced * 1000.0 / format.nAvgBytesPerSec;
    result = !(status & DSBSTATUS_PLAYING) || played_ms < (now - begin) * 0.75
             || played_ms > (now - begin) * 1.25 || max_stall > 200;
    printf("%s %lu Hz %u-channel playback: clock=%.1f ms elapsed=%.1f ms max_cursor_gap=%.1f ms\n",
           result ? "FAIL" : "PASS", rate, channels, played_ms, now - begin, max_stall);
done:
    IDirectSoundBuffer_Stop(buffer);
    if (spatial) IDirectSound3DBuffer_Release(spatial);
    if (listener) IDirectSound3DListener_Release(listener);
    if (primary) IDirectSoundBuffer_Release(primary);
    IDirectSoundBuffer_Release(buffer);
    return result;
}

int main(void)
{
    IDirectSound8 *sound = NULL;
    HANDLE done = CreateEventW(NULL, TRUE, FALSE, NULL);
    HANDLE guard;
    double begin = milliseconds();
    int failures;
    if (!done) return 1;
    guard = CreateThread(NULL, 0, watchdog, done, 0, NULL);
    if (!guard) { CloseHandle(done); return 1; }
    printf("Starting %zu-bit DirectSound playback probe\n", sizeof(void *) * 8);
    fflush(stdout);
    if (!check(DirectSoundCreate8(NULL, &sound, NULL), "DirectSoundCreate8")) return 1;
    printf("DirectSound initialized in %.1f ms\n", milliseconds() - begin);
    fflush(stdout);
    if (!check(IDirectSound8_SetCooperativeLevel(sound, GetDesktopWindow(), DSSCL_NORMAL), "SetCooperativeLevel"))
    {
        IDirectSound8_Release(sound);
        return 1;
    }
    failures = playback(sound, 2, 44100);
    failures += playback(sound, 1, 48000);
    IDirectSound8_Release(sound);
    SetEvent(done);
    WaitForSingleObject(guard, INFINITE);
    CloseHandle(guard);
    CloseHandle(done);
    printf("%d playback failures\n", failures);
    return failures != 0;
}
