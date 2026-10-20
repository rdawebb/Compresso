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
  TEST_ASSERT_EQUAL_INT(
      0, selection_init(&sel, (const char **)c->files, c->num_files));

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
