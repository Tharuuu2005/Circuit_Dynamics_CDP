/*
 * SDR Link.exe - double-click starter for app.py on Windows.
 *
 * Finds radioconda's pythonw.exe (python_path.txt written by Setup.bat, else the usual
 * install folders), sets up the environment the way "conda activate" does (so GNU Radio's
 * and Qt's DLLs are found) and starts app.py from the folder this .exe is in, with no
 * console window. Shows a message box if radioconda or app.py can't be found.
 *
 * Build (any OS): python -m ziglang cc -target x86_64-windows-gnu -O2 -mwindows
 *                 launcher.c icon.res -o "SDR Link.exe"
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <wchar.h>
#include <stdio.h>

#define N 4096

static int exists(const wchar_t *p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void trim(wchar_t *s) {
    size_t n = wcslen(s);
    while (n && (s[n - 1] == L'\r' || s[n - 1] == L'\n' || s[n - 1] == L' ' || s[n - 1] == L'"'))
        s[--n] = 0;
    if (s[0] == L'"') memmove(s, s + 1, (wcslen(s)) * sizeof(wchar_t));
}

/* root folder of the conda install that holds python.exe, or 0 */
static int find_root(const wchar_t *dir, wchar_t *root) {
    wchar_t p[N], py[N];
    /* 1. python_path.txt next to the exe (Setup.bat writes the python.exe it used) */
    swprintf(p, N, L"%ls\\python_path.txt", dir);
    FILE *f = _wfopen(p, L"r, ccs=UTF-8");
    if (f) {
        if (fgetws(root, N, f)) {
            trim(root);
            wchar_t *slash = wcsrchr(root, L'\\');
            if (slash && (_wcsicmp(slash + 1, L"python.exe") == 0 || _wcsicmp(slash + 1, L"pythonw.exe") == 0))
                *slash = 0;
            swprintf(py, N, L"%ls\\pythonw.exe", root);
            fclose(f);
            if (exists(py)) return 1;
        } else fclose(f);
    }
    /* 2. the usual radioconda / conda folders */
    const wchar_t *vars[] = {L"USERPROFILE", L"LOCALAPPDATA", L"PROGRAMDATA", L"PROGRAMFILES", 0};
    const wchar_t *names[] = {L"radioconda", L"miniforge3", L"mambaforge", L"anaconda3", L"miniconda3", 0};
    wchar_t base[N];
    for (int v = 0; vars[v]; v++) {
        if (!GetEnvironmentVariableW(vars[v], base, N)) continue;
        for (int k = 0; names[k]; k++) {
            swprintf(root, N, L"%ls\\%ls", base, names[k]);
            swprintf(py, N, L"%ls\\pythonw.exe", root);
            if (exists(py)) return 1;
        }
    }
    for (int k = 0; names[k]; k++) {
        swprintf(root, N, L"C:\\%ls", names[k]);
        swprintf(py, N, L"%ls\\pythonw.exe", root);
        if (exists(py)) return 1;
    }
    return 0;
}

int WINAPI wWinMain(HINSTANCE h, HINSTANCE prev, PWSTR args, int show) {
    wchar_t dir[N], app[N], root[N], cmd[3 * N], path[8 * N], old[6 * N];
    GetModuleFileNameW(NULL, dir, N);
    wchar_t *s = wcsrchr(dir, L'\\');
    if (s) *s = 0;
    swprintf(app, N, L"%ls\\app.py", dir);
    if (!exists(app)) {
        MessageBoxW(NULL, L"app.py is not next to SDR Link.exe.\n\nKeep the .exe inside the SDR Link folder "
                          L"(you can put a shortcut to it on the desktop).", L"SDR Link", MB_ICONERROR);
        return 1;
    }
    if (!find_root(dir, root)) {
        MessageBoxW(NULL, L"radioconda was not found on this PC.\n\nInstall radioconda, then run Setup.bat "
                          L"in the SDR Link folder once.", L"SDR Link", MB_ICONERROR);
        return 1;
    }
    /* what "conda activate" adds to PATH */
    old[0] = 0;
    GetEnvironmentVariableW(L"PATH", old, 6 * N);
    swprintf(path, 8 * N, L"%ls;%ls\\Library\\mingw-w64\\bin;%ls\\Library\\usr\\bin;%ls\\Library\\bin;"
                          L"%ls\\Scripts;%ls\\bin;%ls", root, root, root, root, root, root, old);
    SetEnvironmentVariableW(L"PATH", path);
    SetEnvironmentVariableW(L"CONDA_PREFIX", root);
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    swprintf(cmd, 3 * N, L"\"%ls\\pythonw.exe\" \"%ls\" %ls", root, app, args ? args : L"");
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi)) {
        wchar_t msg[N];
        swprintf(msg, N, L"Could not start the app (error %lu).\n\n%ls", GetLastError(), cmd);
        MessageBoxW(NULL, msg, L"SDR Link", MB_ICONERROR);
        return 1;
    }
    /* if the app ends within a few seconds, something is missing: tell the user */
    if (WaitForSingleObject(pi.hProcess, 6000) == WAIT_OBJECT_0) {
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        if (code != 0) {
            wchar_t msg[N];
            swprintf(msg, N, L"The app stopped at start (code %lu).\n\nRun Setup.bat once in the SDR Link "
                             L"folder; details are in data\\error.log.", code);
            MessageBoxW(NULL, msg, L"SDR Link", MB_ICONWARNING);
        }
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
