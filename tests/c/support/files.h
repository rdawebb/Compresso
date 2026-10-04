// Small file helpers shared by the tests that drive whole-file entry points

#ifndef FILES_H
#define FILES_H

// An OVERWRITE target, as the tests write over their own scratch files
#define OVERWRITE_TO(p) (&(OutputTarget){.path = (p), .overwrite = 2})

// Non-zero if both files open and hold the same bytes
int files_equal(const char *a, const char *b);

// Create `path` holding exactly `contents`, failing the test if it cannot
void write_file(const char *path, const char *contents);

// The file's size in bytes, or -1 if it cannot be opened
long file_size(const char *path);

#endif // FILES_H
