// The exception objects _core.c would create at import

#ifndef TEST_STUBS_H
#define TEST_STUBS_H

#include "common.h"

// _core.c's exception classes, with its hierarchy, created on first call
const CompErrors *test_errors(void);

// A context holding only test_errors(), reset on each call, for a call that
// can fail
CoreContext *test_ctx(void);

// Whether the pending exception's message contains `needle`; leaves it set
int error_says(const char *needle);

#endif // TEST_STUBS_H
