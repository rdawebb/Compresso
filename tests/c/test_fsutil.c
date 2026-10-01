#include "files.h"
#include "fsutil.h"
#include "unity.h"
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

void setUp(void) {}
void tearDown(void) {
  remove("tmp_fsutil_a");
  remove("tmp_fsutil_b");
  remove("tmp_fsutil_link");
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
