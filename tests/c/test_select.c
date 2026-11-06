// Makes Unity define TEST_RANGE; the runner generator expands each one
#define UNITY_SUPPORT_TEST_CASES

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "archives/select.h"
#include "unity.h"
#include <stdlib.h>
#include <string.h>

void setUp(void) {}
void tearDown(void) {}

// As zip stores them; directories keep their trailing slash
static const char *const NAMES[] = {"d/",          "d/x.txt", "d/sub/",
                                    "d/sub/y.txt", "dx.txt",  "e.txt"};

typedef struct {
  const char *files[3];
  size_t num_files;
  const char *selected;  // NAMES picked, space-separated
  const char *unmatched; // Requested names nothing matched, in sorted order
} SelectCase;

static const SelectCase SELECT_CASES[] = {
    {{"d"}, 1, "d/ d/x.txt d/sub/ d/sub/y.txt", ""},
    {{"d/"}, 1, "d/ d/x.txt d/sub/ d/sub/y.txt", ""},
    {{"d/sub"}, 1, "d/sub/ d/sub/y.txt", ""},
    {{"e.txt", "d/sub/y.txt"}, 2, "d/sub/y.txt e.txt", ""},
    {{"d", "d/x.txt", "d"}, 3, "d/ d/x.txt d/sub/ d/sub/y.txt", ""},
    {{"d/su", "dx"}, 2, "", "d/su dx"}, // Never part of a component
    {{0}, 0, "d/ d/x.txt d/sub/ d/sub/y.txt dx.txt e.txt", ""},
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(SELECT_CASES) / sizeof(SELECT_CASES[0]) == 7,
               "update TEST_RANGE");

TEST_RANGE([ 0, 6, 1 ])
void test_names_select_whole_components(int index) {
  const SelectCase *c = &SELECT_CASES[index];
  Selection sel;
  TEST_ASSERT_EQUAL_INT(0, selection_init(&sel, (const char **)c->files,
                                          c->num_files, &(PathFilter){0}));

  char selected[128] = "";
  for (size_t i = 0; i < sizeof(NAMES) / sizeof(NAMES[0]); i++) {
    ArchiveEntry entry = {.path = (char *)NAMES[i]};
    if (entry_is_selected(&entry, &sel)) {
      if (*selected)
        strcat(selected, " ");
      strcat(selected, NAMES[i]);
    }
  }

  char unmatched[128] = "";
  for (size_t i = 0; i < sel.count; i++) {
    if (sel.items[i].matched)
      continue;
    if (*unmatched)
      strcat(unmatched, " ");
    strncat(unmatched, sel.items[i].name, sel.items[i].len);
  }
  free(sel.items);

  TEST_ASSERT_EQUAL_STRING(c->selected, selected);
  TEST_ASSERT_EQUAL_STRING(c->unmatched, unmatched);
}

typedef struct {
  const char *pat;
  const char *path;
  int matches;
} MatchCase;

static const MatchCase MATCH_CASES[] = {
    {"*.log", "a/b.log", 1},
    {"*.log", "b.log.gz", 0},
    {"node_modules", "x/node_modules_old/y.js", 0},
    {"a/b", "x/a/b/c", 1},
    {"b", "ab", 0},
    {"a*c", "a/b/c", 1}, // `*` crosses '/'
    {"a*b*c", "aXbYbZc", 1},
    {"?.txt", "a.txt", 1},
    {"[!ab].txt", "a.txt", 0},
    {"[a-c]x", "bx", 1},
    {"[]]", "]", 1},
    {"[a", "[a", 1}, // Unterminated, so literal
    {"\\*", "*", 1},
    {"\\*", "a", 0},
    {"d", "d/", 1}, // A zip directory's trailing slash
    {"", "/a", 0},  // A tar's absolute path has no empty component to match
};

// The TEST_RANGE below must span exactly these rows
_Static_assert(sizeof(MATCH_CASES) / sizeof(MATCH_CASES[0]) == 16,
               "update TEST_RANGE");

TEST_RANGE([ 0, 15, 1 ])
void test_path_matches(int index) {
  const MatchCase *c = &MATCH_CASES[index];
  TEST_ASSERT_EQUAL_INT_MESSAGE(c->matches, path_matches(c->path, c->pat),
                                c->pat);
}
