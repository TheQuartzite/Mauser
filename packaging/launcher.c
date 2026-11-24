/* Mauser launcher: one downloadable file that unpacks the app once and from then on starts it directly.
 *
 * The file is: this program, then the app as a .tar.xz, then a 32-byte trailer
 * (payload size as 8 bytes little-endian, 16 characters of build id, the 8 characters "MAUSRPKG").
 * The app is unpacked to <data home>/mauser-app/<build id>/ and older builds there are deleted. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <ftw.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static void tell(const char *text, int error) {
    fprintf(stderr, "Mauser: %s\n", text);
    if (fork() == 0) {
        int null = open("/dev/null", O_WRONLY);
        if (null >= 0) { dup2(null, 1); dup2(null, 2); }
        if (error) {
            execlp("kdialog", "kdialog", "--title", "Mauser", "--error", text, (char *)NULL);
            execlp("zenity", "zenity", "--error", "--title", "Mauser", "--text", text, (char *)NULL);
        }
        execlp("notify-send", "notify-send", "Mauser", text, (char *)NULL);
        _exit(0);
    }
}

static int rm_one(const char *path, const struct stat *st, int flag, struct FTW *ftw) {
    (void)st; (void)flag; (void)ftw;
    return remove(path);
}

static void rm_tree(const char *path) { nftw(path, rm_one, 32, FTW_DEPTH | FTW_PHYS); }

static int mkdirs(const char *path) {
    char buf[PATH_MAX];
    snprintf(buf, sizeof buf, "%s", path);
    for (char *p = buf + 1; *p; p++)
        if (*p == '/') { *p = 0; if (mkdir(buf, 0755) && errno != EEXIST) return -1; *p = '/'; }
    return (mkdir(buf, 0755) && errno != EEXIST) ? -1 : 0;
}

int main(int argc, char **argv) {
    char self[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n <= 0) { tell("could not find its own file.", 1); return 1; }
    self[n] = 0;
    int fd = open(self, O_RDONLY);
    struct stat st;
    unsigned char tr[32];
    if (fd < 0 || fstat(fd, &st) || st.st_size < 32 || pread(fd, tr, 32, st.st_size - 32) != 32 ||
        memcmp(tr + 24, "MAUSRPKG", 8)) { tell("this file is damaged or incomplete. Download it again.", 1); return 1; }
    uint64_t size = 0;
    for (int i = 7; i >= 0; i--) size = (size << 8) | tr[i];
    char id[17];
    memcpy(id, tr + 8, 16); id[16] = 0;
    for (int i = 0; i < 16; i++)
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) { tell("this file is damaged.", 1); return 1; }
    off_t start = st.st_size - 32 - (off_t)size;
    if (start <= 0) { tell("this file is damaged or incomplete. Download it again.", 1); return 1; }

    char base[PATH_MAX], dir[PATH_MAX], target[PATH_MAX], tmp[PATH_MAX];
    const char *data = getenv("XDG_DATA_HOME"), *home = getenv("HOME");
    if (data && data[0] == '/') snprintf(base, sizeof base, "%.3000s/mauser-app", data);
    else if (home && home[0] == '/') snprintf(base, sizeof base, "%.3000s/.local/share/mauser-app", home);
    else { tell("no home folder is set.", 1); return 1; }
    snprintf(dir, sizeof dir, "%.3900s/%s", base, id);
    snprintf(target, sizeof target, "%.4000s/Mauser", dir);

    if (access(target, X_OK) != 0) {
        snprintf(tmp, sizeof tmp, "%.3900s/.unpack-%d", base, (int)getpid());
        if (mkdirs(base) || (rm_tree(tmp), mkdir(tmp, 0755))) { tell("could not create its folder under your home folder.", 1); return 1; }
        tell("Setting up for first use. This takes a few seconds.", 0);
        int pipefd[2];
        if (pipe(pipefd)) { tell("could not start unpacking.", 1); return 1; }
        pid_t child = fork();
        if (child == 0) {
            dup2(pipefd[0], 0); close(pipefd[0]); close(pipefd[1]);
            execlp("tar", "tar", "-xJf", "-", "-C", tmp, (char *)NULL);
            _exit(127);
        }
        close(pipefd[0]);
        static char buf[1 << 16];
        uint64_t left = size; off_t at = start; int ok = 1;
        while (left && ok) {
            ssize_t got = pread(fd, buf, left < sizeof buf ? left : sizeof buf, at);
            if (got <= 0) { ok = 0; break; }
            for (ssize_t done = 0; done < got;) {
                ssize_t w = write(pipefd[1], buf + done, got - done);
                if (w <= 0) { ok = 0; break; }
                done += w;
            }
            at += got; left -= got;
        }
        close(pipefd[1]);
        int status = 0;
        waitpid(child, &status, 0);
        if (!ok || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            rm_tree(tmp);
            tell(WIFEXITED(status) && WEXITSTATUS(status) == 127 ? "the 'tar' program is needed to set up, and was not found."
                 : "unpacking failed. Is the disk full? Otherwise download the file again.", 1);
            return 1;
        }
        if (rename(tmp, dir) && access(target, X_OK) != 0) { rm_tree(tmp); tell("could not finish setting up.", 1); return 1; }
        DIR *d = opendir(base);                 /* older builds are no longer needed */
        if (d) {
            struct dirent *e;
            while ((e = readdir(d))) {
                if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..") || !strcmp(e->d_name, id)) continue;
                char old[PATH_MAX];
                snprintf(old, sizeof old, "%.3800s/%.255s", base, e->d_name);
                rm_tree(old);
            }
            closedir(d);
        }
    }
    close(fd);
    argv[0] = target;
    if (argc > 1) {                              /* asked something on the command line: answer there */
        execv(target, argv);
        tell("the app could not be started after unpacking.", 1);
        return 1;
    }
    /* Start the app and watch its first seconds, so a start that fails says why instead of doing nothing. */
    char log[PATH_MAX];
    snprintf(log, sizeof log, "%.3900s/start.log", base);
    pid_t app = fork();
    if (app == 0) {
        int out = open(log, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (out >= 0) { dup2(out, 1); dup2(out, 2); close(out); }
        setsid();
        execv(target, argv);
        fprintf(stderr, "could not run %s: %s\n", target, strerror(errno));
        _exit(126);
    }
    for (int i = 0; i < 100; i++) {              /* ten seconds */
        int status = 0;
        if (waitpid(app, &status, WNOHANG) == app) {
            if (WIFEXITED(status) && WEXITSTATUS(status) == 0) return 0;      /* e.g. handed over to a window already open */
            char tail[700] = "", msg[1400];
            int in = open(log, O_RDONLY);
            if (in >= 0) {
                off_t len = lseek(in, 0, SEEK_END), from = len > 600 ? len - 600 : 0;
                ssize_t got = pread(in, tail, 600, from);
                tail[got > 0 ? got : 0] = 0;
                close(in);
            }
            if (WIFSIGNALED(status))
                snprintf(msg, sizeof msg, "the app stopped while starting (signal %d).\n\n%s\n\nFull details: %s", WTERMSIG(status), tail, log);
            else
                snprintf(msg, sizeof msg, "the app stopped while starting (code %d).\n\n%s\n\nFull details: %s", WEXITSTATUS(status), tail, log);
            tell(msg, 1);
            return 1;
        }
        usleep(100000);
    }
    return 0;
}
