#include "fsutil.h"
#include "unity.h"
#include <errno.h>
#include <string.h>

void setUp(void) {}
void tearDown(void) {}

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
