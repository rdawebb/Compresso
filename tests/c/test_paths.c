// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "archives/paths.h"
#include "fsutil.h"
#include "test_stubs.h"
#include "unity.h"
#include <stdio.h>
#include <string.h>

// Picks a row's answer for the platform; only '/' separates on POSIX, where
// a backslash or colon is an ordinary filename character
#if defined(_WIN32) || defined(_WIN64)
#define WIN_OR(win, posix) win
#else
#define WIN_OR(win, posix) posix
#endif

#define ROOT "tmp_paths_root"

static char resolved_root[FS_PATH_MAX];

void setUp(void) {
  if (!Py_IsInitialized())
    Py_Initialize();
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p(ROOT, 0755));
  TEST_ASSERT_EQUAL_INT(0, fs_realpath(ROOT, resolved_root));
}

void tearDown(void) {
  PyErr_Clear();
  remove(ROOT "/link");
  remove(ROOT);
  remove(ROOT "-evil");
  remove("tmp_paths_outside");
}

// Asserts an ExtractionPolicyError saying `needle`, or success when NULL
static void assert_outcome(int rc, const char *needle, const char *label) {
  if (!needle) {
    TEST_ASSERT_EQUAL_INT_MESSAGE(0, rc, label);
    TEST_ASSERT_NULL_MESSAGE(PyErr_Occurred(), label);
    return;
  }
  TEST_ASSERT_EQUAL_INT_MESSAGE(-1, rc, label);
  TEST_ASSERT_TRUE_MESSAGE(
      PyErr_ExceptionMatches(test_errors()->extraction_policy), label);
  TEST_ASSERT_TRUE_MESSAGE(error_says(needle), label);
  PyErr_Clear();
}

// ---- path_has_parent_segment / path_depth ----

typedef struct {
  const char *path;
  int has_parent;
  uint32_t depth;
} SegmentCase;

static const SegmentCase SEGMENT_CASES[] = {
    {"..", 1, 1},  {"a/../b", 1, 3}, {"..data", 0, 1},
    {"./a", 0, 1}, {"a//b", 0, 2},   {"..\\..\\x", WIN_OR(1, 0), WIN_OR(3, 1)},
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(SEGMENT_CASES) / sizeof(SEGMENT_CASES[0]) == 6,
               "update TEST_RANGE");

TEST_RANGE([ 0, 5, 1 ])
void test_segments_are_split_on_separators(int index) {
  const SegmentCase *c = &SEGMENT_CASES[index];
  TEST_ASSERT_EQUAL_INT_MESSAGE(c->has_parent, path_has_parent_segment(c->path),
                                c->path);
  TEST_ASSERT_EQUAL_UINT32_MESSAGE(c->depth, path_depth(c->path), c->path);
}

// ---- validate_entry_path ----

typedef struct {
  const char *path;
  const char *error; // NULL: accepted
} EntryCase;

static const EntryCase ENTRY_CASES[] = {
    {"/etc/passwd", "absolute path"},
    {"\\evil.txt", "absolute path"},
    {"C:\\Windows\\evil.txt", "absolute path"},
    {"C:/Windows/evil.txt", "absolute path"},
    {"C:evil.txt", "absolute path"},
    {"\\\\server\\share\\evil.txt", "absolute path"},
    {"\\\\?\\C:\\evil.txt", "absolute path"},
    {"notes.txt:evil.exe", WIN_OR("alternate data stream", NULL)},
    {"../escape.txt", "traversal"},        // Its parent resolves outside
    {"sub/../../escape.txt", "traversal"}, // Its parent doesn't exist yet
    {"..\\..\\escape.txt", WIN_OR("traversal", NULL)},
    {"..data", NULL},
    {"sub/file.txt", NULL},
};

_Static_assert(sizeof(ENTRY_CASES) / sizeof(ENTRY_CASES[0]) == 13,
               "update TEST_RANGE");

TEST_RANGE([ 0, 12, 1 ])
void test_entry_path_must_stay_below_the_root(int index) {
  const EntryCase *c = &ENTRY_CASES[index];
  ExtractionPolicy policy = {.max_depth = 32};

  int rc = validate_entry_path(test_errors(), ROOT, resolved_root, c->path,
                               path_depth(c->path), &policy);
  assert_outcome(rc, c->error, c->path);
}

typedef struct {
  uint32_t depth;
  uint32_t max_depth; // 0: unlimited
  int ok;
} DepthCase;

static const DepthCase DEPTH_CASES[] = {
    {32, 32, 1}, // The default limit
    {33, 32, 0}, {2, 2, 1}, {3, 2, 0}, {100, 0, 1},
};

_Static_assert(sizeof(DEPTH_CASES) / sizeof(DEPTH_CASES[0]) == 5,
               "update TEST_RANGE");

TEST_RANGE([ 0, 4, 1 ])
void test_entry_path_depth_is_capped_by_the_policy(int index) {
  const DepthCase *c = &DEPTH_CASES[index];
  char path[256] = "d";
  for (uint32_t i = 1; i < c->depth; i++)
    strcat(path, "/d");
  ExtractionPolicy policy = {.max_depth = c->max_depth};

  int rc = validate_entry_path(test_errors(), ROOT, resolved_root, path,
                               path_depth(path), &policy);
  assert_outcome(rc, c->ok ? NULL : "max depth", path);
}

void test_entry_cannot_write_through_a_symlinked_component(void) {
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p("tmp_paths_outside", 0755));
  TEST_ASSERT_EQUAL_INT(0, fs_symlink("../tmp_paths_outside", ROOT "/link"));
  ExtractionPolicy policy = {.max_depth = 32};

  // An existing parent is resolved through the link
  int rc = validate_entry_path(test_errors(), ROOT, resolved_root,
                               "link/escape.txt", 2, &policy);
  assert_outcome(rc, "traversal", "link/escape.txt");

  // A missing one is judged by its deepest existing ancestor
  size_t len;
  TEST_ASSERT_EQUAL_INT(
      0, existing_ancestor_is_contained(resolved_root, ROOT "/link/sub", &len));
  TEST_ASSERT_EQUAL_size_t(strlen(ROOT "/link"), len);
}

void test_containment_needs_a_component_boundary(void) {
  TEST_ASSERT_EQUAL_INT(0, fs_mkdir_p(ROOT "-evil", 0755));

  TEST_ASSERT_EQUAL_INT(1, dir_is_contained(resolved_root, ROOT));
  TEST_ASSERT_EQUAL_INT(0, dir_is_contained(resolved_root, ROOT "-evil"));
  TEST_ASSERT_EQUAL_INT(-1, dir_is_contained(resolved_root, ROOT "/missing"));
  TEST_ASSERT_EQUAL_INT(1, dir_is_contained("/", ROOT "-evil"));
}

// ---- check_symlink_target ----

typedef struct {
  const char *target;
  uint32_t base_depth; // The link's directory depth below the root
  const char *error;   // NULL: accepted
} TargetCase;

static const TargetCase TARGET_CASES[] = {
    {"../../x", 1, "symlink target"},
    {"../../x", 2, NULL},
    {"/etc/passwd", 5, "symlink target"},
    {"C:x", 5, "symlink target"},
    {"a/../a", 5, "symlink target"},
    {"../d/../a", 5, "symlink target"},
    {"", 5, "no target"},
    {"..", 0, "symlink target"},
    {"..", 1, NULL},
    {"./a", 0, NULL},
};

_Static_assert(sizeof(TARGET_CASES) / sizeof(TARGET_CASES[0]) == 10,
               "update TEST_RANGE");

TEST_RANGE([ 0, 9, 1 ])
void test_symlink_target_must_stay_below_the_root(int index) {
  const TargetCase *c = &TARGET_CASES[index];
  ArchiveEntry entry = {
      .path = "d/b",
      .type = ENTRY_SYMLINK,
      .link_target = (char *)c->target,
  };

  int rc = check_symlink_target(test_errors(), &entry, c->base_depth);
  assert_outcome(rc, c->error, c->target);
}
