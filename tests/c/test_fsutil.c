// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#include "files.h"
#include "fsutil.h"
#include "unity.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// Names fs_mkstemp chose, for tearDown
static char made[2][32];

static void remove_conflicts(const char *path) {
  char name[FS_PATH_MAX];
  for (int n = 2; n <= 3; n++) {
    if (fs_conflict_path(path, n, name, sizeof(name)) == 0)
      remove(name);
  }
}

void setUp(void) { memset(made, 0, sizeof(made)); }
void tearDown(void) {
  for (size_t i = 0; i < sizeof(made) / sizeof(made[0]); i++) {
    if (made[i][0])
      remove(made[i]);
  }
  remove_conflicts("tmp_fsutil_b");
  remove("tmp_fsutil_a");
  remove("tmp_fsutil_b");
  remove("tmp_fsutil_link");
  remove("tmp_fsutil_dirs/a/b/c");
  remove("tmp_fsutil_dirs/a/b");
  remove("tmp_fsutil_dirs/a");
  remove("tmp_fsutil_dirs");
}

// ---- fs_join ----

void test_join_inserts_separator(void) {
  char out[16];
  TEST_ASSERT_EQUAL_INT(0, fs_join(out, sizeof(out), "dir", "name"));
  TEST_ASSERT_EQUAL_STRING("dir/name", out);
}

void test_join_keeps_existing_trailing_separator(void) {
  char out[16];
  TEST_ASSERT_EQUAL_INT(0, fs_join(out, sizeof(out), "dir/", "name"));
  TEST_ASSERT_EQUAL_STRING("dir/name", out);
}

void test_join_empty_dir_adds_no_separator(void) {
  char out[16];
  TEST_ASSERT_EQUAL_INT(0, fs_join(out, sizeof(out), "", "name"));
  TEST_ASSERT_EQUAL_STRING("name", out);
}

void test_join_empty_name_keeps_trailing_separator(void) {
  char out[16];
  TEST_ASSERT_EQUAL_INT(0, fs_join(out, sizeof(out), "dir", ""));
  TEST_ASSERT_EQUAL_STRING("dir/", out);
}

void test_join_fills_buffer_exactly(void) {
  // "dir/name" is 8 bytes plus the terminator
  char out[9];
  TEST_ASSERT_EQUAL_INT(0, fs_join(out, sizeof(out), "dir", "name"));
  TEST_ASSERT_EQUAL_STRING("dir/name", out);
}

void test_join_rejects_one_byte_short(void) {
  char out[8];
  errno = 0;
  TEST_ASSERT_EQUAL_INT(-1, fs_join(out, sizeof(out), "dir", "name"));
  TEST_ASSERT_EQUAL_INT(ENAMETOOLONG, errno);
}

void test_join_rejects_path_longer_than_path_max(void) {
  char name[FS_PATH_MAX];
  memset(name, 'a', sizeof(name) - 1);
  name[sizeof(name) - 1] = '\0';

  char out[FS_PATH_MAX];
  errno = 0;
  TEST_ASSERT_EQUAL_INT(-1, fs_join(out, sizeof(out), "d", name));
  TEST_ASSERT_EQUAL_INT(ENAMETOOLONG, errno);
}

// ---- fs_same_file ----

static fs_stat stat_of(const char *path) {
  fs_stat st;
  TEST_ASSERT_EQUAL_INT(0, fs_stat_path(path, &st));
  return st;
}

void test_same_file_matches_one_file_stat_twice(void) {
  write_file("tmp_fsutil_a", "a");
  fs_stat first = stat_of("tmp_fsutil_a");
  fs_stat second = stat_of("tmp_fsutil_a");

  TEST_ASSERT_TRUE(fs_same_file(&first, &second));
}

void test_same_file_matches_a_hardlink(void) {
  write_file("tmp_fsutil_a", "a");
  TEST_ASSERT_EQUAL_INT(0, link("tmp_fsutil_a", "tmp_fsutil_link"));
  fs_stat file = stat_of("tmp_fsutil_a");
  fs_stat link_st = stat_of("tmp_fsutil_link");

  TEST_ASSERT_TRUE(fs_same_file(&file, &link_st));
}

void test_same_file_tells_identical_contents_apart(void) {
  write_file("tmp_fsutil_a", "same");
  write_file("tmp_fsutil_b", "same");
  fs_stat a = stat_of("tmp_fsutil_a");
  fs_stat b = stat_of("tmp_fsutil_b");

  TEST_ASSERT_FALSE(fs_same_file(&a, &b));
}

void test_same_file_never_matches_an_unknown_identity(void) {
  fs_stat unknown;
  memset(&unknown, 0, sizeof(unknown));

  TEST_ASSERT_FALSE(fs_same_file(&unknown, &unknown));
}

// ---- fs_readlink ----

void test_readlink_fills_a_buffer_with_room_for_the_terminator(void) {
  TEST_ASSERT_EQUAL_INT(0, symlink("1234567", "tmp_fsutil_link"));

  char buf[8];
  TEST_ASSERT_EQUAL_INT(0, fs_readlink("tmp_fsutil_link", buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_STRING("1234567", buf);
}

void test_readlink_refuses_a_target_that_doesnt_fit(void) {
  TEST_ASSERT_EQUAL_INT(0, symlink("12345678", "tmp_fsutil_link"));

  char buf[8];
  errno = 0;
  TEST_ASSERT_EQUAL_INT(-1, fs_readlink("tmp_fsutil_link", buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_INT(ENAMETOOLONG, errno);
}

// ---- fs_mkdir_p ----

static int is_dir(const char *path) {
  fs_stat st;
  return fs_stat_path(path, &st) == 0 && st.type == FS_TYPE_DIR;
}

void test_mkdir_p_creates_every_missing_parent(void) {
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p("tmp_fsutil_dirs/a/b/c", 0755));
  TEST_ASSERT_TRUE(is_dir("tmp_fsutil_dirs/a/b/c"));
}

void test_mkdir_p_accepts_a_trailing_separator(void) {
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p("tmp_fsutil_dirs/a/", 0755));
  TEST_ASSERT_TRUE(is_dir("tmp_fsutil_dirs/a"));
}

void test_mkdir_p_starts_below_an_absolute_root(void) {
  char cwd[FS_PATH_MAX], path[FS_PATH_MAX];
  TEST_ASSERT_NOT_NULL(getcwd(cwd, sizeof(cwd)));
  TEST_ASSERT_EQUAL_INT(
      0, fs_join(path, sizeof(path), cwd, "tmp_fsutil_dirs/a/b"));

  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p(path, 0755));
  TEST_ASSERT_TRUE(is_dir("tmp_fsutil_dirs/a/b"));
}

// ---- fs_mkstemp ----

void test_mkstemp_opens_a_file_with_the_umask_applied_mode(void) {
  strcpy(made[0], "tmp_fsutil_XXXXXX");
  FILE *f = fs_mkstemp(made[0]);
  TEST_ASSERT_NOT_NULL(f);
  TEST_ASSERT_NULL(strstr(made[0], "XXXXXX"));
  TEST_ASSERT_TRUE(fputs("12345", f) >= 0);
  TEST_ASSERT_EQUAL_INT(0, fclose(f));
  TEST_ASSERT_EQUAL_INT(5, file_size(made[0]));

  mode_t mask = umask(0);
  umask(mask);
  struct stat st;
  TEST_ASSERT_EQUAL_INT(0, stat(made[0], &st));
  TEST_ASSERT_EQUAL_UINT32(0666 & ~mask, st.st_mode & 07777);
}

void test_mkstemp_picks_a_new_name_each_time(void) {
  strcpy(made[0], "tmp_fsutil_XXXXXX");
  strcpy(made[1], "tmp_fsutil_XXXXXX");
  FILE *a = fs_mkstemp(made[0]);
  FILE *b = fs_mkstemp(made[1]);
  TEST_ASSERT_NOT_NULL(a);
  TEST_ASSERT_NOT_NULL(b);
  fclose(a);
  fclose(b);

  TEST_ASSERT_NOT_EQUAL(0, strcmp(made[0], made[1]));
}

void test_mkstemp_rejects_a_template_without_the_placeholder(void) {
  char tmpl[] = "tmp_fsutil_XXXXX";
  errno = 0;
  TEST_ASSERT_NULL(fs_mkstemp(tmpl));
  TEST_ASSERT_EQUAL_INT(EINVAL, errno);
}

// ---- fs_commit_temp ----

// The temp holds "newer" (5 bytes) and an existing destination "old" (3), so
// sizes tell them apart
typedef struct {
  const char *label;
  int mode;
  int dst_exists;
  int rc;
  int err;
  int name_n; // 1: the destination, 2: its "name 2", 0: none reported
  long dst_size;
  int temp_left;
} CommitCase;

static const CommitCase COMMIT_CASES[] = {
    {"error, free", 0, 0, 0, 0, 1, 5, 0},
    {"skip, free", 1, 0, 0, 0, 1, 5, 0},
    {"overwrite, free", 2, 0, 0, 0, 1, 5, 0},
    {"rename, free", 3, 0, 0, 0, 1, 5, 0},
    {"error, taken", 0, 1, -1, EEXIST, 0, 3, 1},
    {"skip, taken", 1, 1, 1, 0, 0, 3, 1},
    {"overwrite, taken", 2, 1, 0, 0, 1, 5, 0},
    {"rename, taken", 3, 1, 0, 0, 2, 3, 0},
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(COMMIT_CASES) / sizeof(COMMIT_CASES[0]) == 8,
               "update TEST_RANGE");

TEST_RANGE([0, 7, 1])
void test_commit_temp_applies_the_overwrite_scheme(int index) {
  const CommitCase *c = &COMMIT_CASES[index];
  write_file("tmp_fsutil_a", "newer");
  if (c->dst_exists)
    write_file("tmp_fsutil_b", "old");

  char actual[FS_PATH_MAX] = "";
  errno = 0;
  int rc = fs_commit_temp("tmp_fsutil_a", "tmp_fsutil_b", c->mode, actual,
                          sizeof(actual));
  TEST_ASSERT_EQUAL_INT_MESSAGE(c->rc, rc, c->label);
  if (c->err)
    TEST_ASSERT_EQUAL_INT_MESSAGE(c->err, errno, c->label);

  if (c->name_n == 1) {
    TEST_ASSERT_EQUAL_STRING_MESSAGE("tmp_fsutil_b", actual, c->label);
  } else if (c->name_n == 2) {
    char expected[FS_PATH_MAX];
    TEST_ASSERT_EQUAL_INT(
        0, fs_conflict_path("tmp_fsutil_b", 2, expected, sizeof(expected)));
    TEST_ASSERT_EQUAL_STRING_MESSAGE(expected, actual, c->label);
    TEST_ASSERT_EQUAL_INT_MESSAGE(5, file_size(expected), c->label);
  }

  TEST_ASSERT_EQUAL_INT_MESSAGE(c->dst_size, file_size("tmp_fsutil_b"),
                                c->label);
  TEST_ASSERT_EQUAL_INT_MESSAGE(c->temp_left ? 5 : -1,
                                file_size("tmp_fsutil_a"), c->label);
}

void test_commit_temp_rename_probes_past_taken_variants(void) {
  char second[FS_PATH_MAX], third[FS_PATH_MAX];
  TEST_ASSERT_EQUAL_INT(
      0, fs_conflict_path("tmp_fsutil_b", 2, second, sizeof(second)));
  TEST_ASSERT_EQUAL_INT(
      0, fs_conflict_path("tmp_fsutil_b", 3, third, sizeof(third)));
  write_file("tmp_fsutil_a", "newer");
  write_file("tmp_fsutil_b", "old");
  write_file(second, "old");

  char actual[FS_PATH_MAX];
  TEST_ASSERT_EQUAL_INT(0, fs_commit_temp("tmp_fsutil_a", "tmp_fsutil_b", 3,
                                          actual, sizeof(actual)));
  TEST_ASSERT_EQUAL_STRING(third, actual);
  TEST_ASSERT_EQUAL_INT(5, file_size(third));
  TEST_ASSERT_EQUAL_INT(3, file_size(second));
}

void test_commit_temp_leaves_the_temp_when_the_name_cannot_be_reported(void) {
  write_file("tmp_fsutil_a", "newer");

  char actual[8]; // Too short for "tmp_fsutil_b"
  errno = 0;
  TEST_ASSERT_EQUAL_INT(-1, fs_commit_temp("tmp_fsutil_a", "tmp_fsutil_b", 2,
                                           actual, sizeof(actual)));
  TEST_ASSERT_EQUAL_INT(ENAMETOOLONG, errno);
  TEST_ASSERT_EQUAL_INT(5, file_size("tmp_fsutil_a"));
  TEST_ASSERT_EQUAL_INT(-1, file_size("tmp_fsutil_b"));
}
