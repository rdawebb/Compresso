// Where the shared test fixtures live

#ifndef FIXTURES_H
#define FIXTURES_H

// The Meson build passes an absolute path; the fallback is relative to
// tests/c, for tools that compile these files without the build's flags
#ifndef FIXTURE_DIR
#define FIXTURE_DIR "../fixtures"
#endif

#endif // FIXTURES_H
