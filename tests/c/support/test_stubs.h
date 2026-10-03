// The exception objects _core.c would create at import

#ifndef TEST_STUBS_H
#define TEST_STUBS_H

// Creates the comp_* exception objects, with _core.c's hierarchy, on first
// call; they start NULL, which PyErr_SetString can't take as a type
void ensure_comp_exceptions(void);

#endif // TEST_STUBS_H
